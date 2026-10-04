"""End-to-end checkout through the HTTP API with the payment and e-mail stubs running at
realistic network latency."""

import pytest

from tests.conftest import make_settings, scaled_ms

pytestmark = pytest.mark.external


STATES = ["MH", "KA", "DL", "GJ", "TN", "WB", "UP", "TG"]
PAYMENT_METHODS = ["tok_visa", "tok_mastercard", "tok_rupay", "tok_upi"]


@pytest.fixture
def settings():
    return make_settings(payment_latency_ms=scaled_ms(400), notification_latency_ms=scaled_ms(200))


@pytest.fixture
def shop(client, make_product, auth_headers):
    """A customer, an admin and two products."""
    return {
        "customer": auth_headers("customer", email="buyer@example.com"),
        "admin": auth_headers("admin", email="ops@example.com"),
        "laptop": make_product(
            name="Laptop", price_paise=6_500_000, stock=20, category="electronics"
        ),
        "kurta": make_product(name="Kurta", price_paise=89_900, stock=200, category="apparel"),
    }


def place(client, shop, *lines, state="MH", coupon=None):
    payload = {
        "items": [{"product_id": shop[name].id, "quantity": qty} for name, qty in lines],
        "shipping_state": state,
    }
    if coupon:
        payload["coupon_code"] = coupon
    response = client.post("/orders", json=payload, headers=shop["customer"])
    assert response.status_code == 201, response.text
    return response.json()


def pay(client, shop, order_id, token="tok_visa"):
    return client.post(
        f"/orders/{order_id}/pay", json={"card_token": token}, headers=shop["customer"]
    )


def test_happy_path_from_order_to_delivery(client, app, shop):
    order = place(client, shop, ("laptop", 1), ("kurta", 2))
    assert pay(client, shop, order["id"]).json()["status"] == "paid"
    assert client.post(f"/orders/{order['id']}/ship", headers=shop["admin"]).status_code == 200
    final = client.post(f"/orders/{order['id']}/deliver", headers=shop["admin"]).json()
    assert final["status"] == "delivered"
    inbox = app.state.notifier.sent_to("buyer@example.com")
    assert [message.subject.split()[-1] for message in inbox] == ["confirmed", "shipped"]


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("token", PAYMENT_METHODS)
def test_checkout_matrix(client, app, shop, state, token):
    """Every payment method, shipping to a spread of states, through to delivery."""
    order = place(client, shop, ("laptop", 1), ("kurta", 2), state=state)
    amounts = order["amounts"]
    if state == "MH":
        assert amounts["igst"] == "0.00"
        assert amounts["cgst"] == amounts["sgst"] != "0.00"
    else:
        assert amounts["cgst"] == amounts["sgst"] == "0.00"
        assert amounts["igst"] != "0.00"

    paid = pay(client, shop, order["id"], token)
    assert paid.status_code == 200
    assert paid.json()["payment_ref"].startswith("pay_")
    for step in ("ship", "deliver"):
        assert (
            client.post(f"/orders/{order['id']}/{step}", headers=shop["admin"]).status_code == 200
        )
    assert len(app.state.notifier.sent_to("buyer@example.com")) == 2


def test_customer_can_retry_with_another_card_after_a_decline(client, shop):
    order = place(client, shop, ("laptop", 1))
    assert pay(client, shop, order["id"], "tok_insufficient_funds").status_code == 402
    assert pay(client, shop, order["id"], "tok_visa").status_code == 200


def test_a_flaky_gateway_is_retried_transparently(client, app, shop):
    order = place(client, shop, ("kurta", 3))
    response = pay(client, shop, order["id"], "tok_flaky")
    assert response.status_code == 200
    assert app.state.payment_gateway.calls == 2


def test_a_dead_gateway_returns_504(client, shop):
    order = place(client, shop, ("kurta", 1))
    response = pay(client, shop, order["id"], "tok_timeout")
    assert response.status_code == 504
    status = client.get(f"/orders/{order['id']}", headers=shop["customer"]).json()["status"]
    assert status == "pending"


def test_cancelling_after_payment_refunds_and_notifies(client, app, shop):
    order = place(client, shop, ("laptop", 2))
    pay(client, shop, order["id"])
    response = client.post(f"/orders/{order['id']}/cancel", headers=shop["customer"]).json()
    assert response["refunded"] is True
    last = app.state.notifier.sent_to("buyer@example.com")[-1]
    assert "refund" in last.body


def test_bulk_order_with_coupon_and_inter_state_shipping(client, shop, make_coupon):
    make_coupon(code="BULK200", kind="flat", value=20_000, min_order_paise=1_000_000)
    order = place(client, shop, ("kurta", 25), state="KA", coupon="BULK200")
    amounts = order["amounts"]
    assert amounts["subtotal"] == "20901.75"  # 25 x 899 with a 7 % bulk discount
    assert amounts["discount"] == "200.00"
    assert amounts["cgst"] == amounts["sgst"] == "0.00"
    assert amounts["igst"] == "2484.21"  # 12 % of 20,701.75
    assert pay(client, shop, order["id"]).status_code == 200


def test_repeated_payment_requests_are_idempotent(client, app, shop):
    order = place(client, shop, ("kurta", 1))
    first = pay(client, shop, order["id"], "tok_declined")
    second = pay(client, shop, order["id"], "tok_declined")
    assert first.json() == second.json()
    assert app.state.payment_gateway.calls == 2
