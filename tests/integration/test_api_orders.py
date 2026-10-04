from datetime import timedelta

import pytest

from app.clock import utcnow
from app.models import Coupon, Product


def order_payload(*lines: tuple[int, int], state: str = "MH", coupon: str | None = None) -> dict:
    payload: dict = {
        "items": [{"product_id": pid, "quantity": qty} for pid, qty in lines],
        "shipping_state": state,
    }
    if coupon is not None:
        payload["coupon_code"] = coupon
    return payload


def stock_of(db, product_id: int) -> int:
    db.expire_all()
    return db.get(Product, product_id).stock


@pytest.fixture
def catalogue(make_product):
    """A phone (18 % GST) and a book (5 % GST)."""
    return (
        make_product(name="Phone", price_paise=1_500_000, stock=10, category="electronics"),
        make_product(name="Novel", price_paise=40_000, stock=100, category="books"),
    )


def test_quote_is_public(client, catalogue):
    phone, novel = catalogue
    response = client.post("/orders/quote", json=order_payload((phone.id, 1), (novel.id, 2)))
    assert response.status_code == 200
    amounts = response.json()["amounts"]
    assert amounts["subtotal"] == "15800.00"
    assert amounts["cgst"] == amounts["sgst"] == "1370.00"  # 9 % of 15,000 + 2.5 % of 800
    assert amounts["total"] == "18540.00"
    assert amounts["total_display"] == "₹18,540.00"


def test_placing_an_order_reserves_stock(client, db, customer_headers, catalogue):
    phone, _ = catalogue
    response = client.post("/orders", json=order_payload((phone.id, 3)), headers=customer_headers)
    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert stock_of(db, phone.id) == 7


def test_order_totals_match_the_quote(client, customer_headers, catalogue):
    phone, novel = catalogue
    payload = order_payload((phone.id, 1), (novel.id, 12), state="KA")
    quoted = client.post("/orders/quote", json=payload).json()["amounts"]
    placed = client.post("/orders", json=payload, headers=customer_headers).json()["amounts"]
    assert placed == quoted
    assert placed["igst"] != "0.00"
    assert placed["cgst"] == "0.00"


def test_insufficient_stock_leaves_everything_untouched(client, db, customer_headers, catalogue):
    phone, novel = catalogue
    response = client.post(
        "/orders", json=order_payload((novel.id, 5), (phone.id, 11)), headers=customer_headers
    )
    assert response.status_code == 409
    assert "only 10 in stock" in response.json()["detail"]
    assert stock_of(db, novel.id) == 100
    assert stock_of(db, phone.id) == 10


def test_repeated_lines_are_merged(client, customer_headers, catalogue):
    _, novel = catalogue
    body = client.post(
        "/orders", json=order_payload((novel.id, 2), (novel.id, 3)), headers=customer_headers
    ).json()
    assert [(item["product_id"], item["quantity"]) for item in body["items"]] == [(novel.id, 5)]


def test_unknown_product_is_404(client, customer_headers):
    response = client.post("/orders", json=order_payload((999, 1)), headers=customer_headers)
    assert response.status_code == 404


def test_inactive_product_cannot_be_ordered(client, customer_headers, make_product):
    retired = make_product(is_active=False)
    response = client.post("/orders", json=order_payload((retired.id, 1)), headers=customer_headers)
    assert response.status_code == 409


def test_unknown_state_is_rejected(client, customer_headers, catalogue):
    phone, _ = catalogue
    response = client.post(
        "/orders", json=order_payload((phone.id, 1), state="ZZ"), headers=customer_headers
    )
    assert response.status_code == 422


def test_ordering_requires_login(client, catalogue):
    phone, _ = catalogue
    assert client.post("/orders", json=order_payload((phone.id, 1))).status_code == 401


def test_coupon_is_applied_and_counted(client, db, customer_headers, catalogue, make_coupon):
    phone, _ = catalogue
    make_coupon(code="SAVE10", value=10, max_discount_paise=100_000)
    body = client.post(
        "/orders", json=order_payload((phone.id, 1), coupon="save10"), headers=customer_headers
    ).json()
    assert body["coupon_code"] == "SAVE10"
    assert body["amounts"]["discount"] == "1000.00"  # 10 % capped at ₹1,000
    db.expire_all()
    assert db.query(Coupon).one().used_count == 1


@pytest.mark.parametrize(
    ("coupon", "reason"),
    [
        ({"code": "OLD", "expires_at": "past"}, "expired"),
        ({"code": "USEDUP", "usage_limit": 1, "used_count": 1}, "usage_limit_reached"),
        ({"code": "BIGSPEND", "min_order_paise": 10_000_000}, "minimum_not_met"),
        ({"code": "PAUSED", "is_active": False}, "inactive"),
        (None, "not_found"),
    ],
)
def test_coupon_rejections(client, customer_headers, catalogue, make_coupon, coupon, reason):
    phone, _ = catalogue
    code = "MISSING"
    if coupon is not None:
        coupon = dict(coupon)
        if coupon.get("expires_at") == "past":
            coupon["expires_at"] = utcnow() - timedelta(days=1)
        code = make_coupon(**coupon).code
    response = client.post(
        "/orders", json=order_payload((phone.id, 1), coupon=code), headers=customer_headers
    )
    assert response.status_code == 422
    assert response.json()["reason"] == reason


def test_customers_only_see_their_own_orders(client, auth_headers, catalogue):
    _, novel = catalogue
    alice, bob = auth_headers(), auth_headers()
    client.post("/orders", json=order_payload((novel.id, 1)), headers=alice)
    bob_order = client.post("/orders", json=order_payload((novel.id, 2)), headers=bob).json()

    assert len(client.get("/orders", headers=alice).json()) == 1
    assert client.get(f"/orders/{bob_order['id']}", headers=alice).status_code == 404
    assert client.get(f"/orders/{bob_order['id']}", headers=bob).status_code == 200


def test_admins_see_every_order(client, auth_headers, admin_headers, catalogue):
    _, novel = catalogue
    for _ in range(3):
        client.post("/orders", json=order_payload((novel.id, 1)), headers=auth_headers())
    assert len(client.get("/orders", headers=admin_headers).json()) == 3


def test_filter_orders_by_status(client, customer_headers, catalogue):
    _, novel = catalogue
    first = client.post("/orders", json=order_payload((novel.id, 1)), headers=customer_headers)
    client.post("/orders", json=order_payload((novel.id, 1)), headers=customer_headers)
    client.post(
        f"/orders/{first.json()['id']}/pay",
        json={"card_token": "tok_visa"},
        headers=customer_headers,
    )
    paid = client.get("/orders", params={"status": "paid"}, headers=customer_headers).json()
    assert [order["id"] for order in paid] == [first.json()["id"]]


def test_payment_moves_the_order_to_paid(client, customer_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 1)), headers=customer_headers
    ).json()
    response = client.post(
        f"/orders/{order['id']}/pay", json={"card_token": "tok_visa"}, headers=customer_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert response.json()["payment_ref"].startswith("pay_")


def test_declined_payment_keeps_the_order_pending(client, customer_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 1)), headers=customer_headers
    ).json()
    response = client.post(
        f"/orders/{order['id']}/pay", json={"card_token": "tok_declined"}, headers=customer_headers
    )
    assert response.status_code == 402
    assert response.json()["reason"] == "card_declined"
    assert (
        client.get(f"/orders/{order['id']}", headers=customer_headers).json()["status"] == "pending"
    )


def test_an_order_cannot_be_paid_twice(client, customer_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 1)), headers=customer_headers
    ).json()
    pay = {"card_token": "tok_visa"}
    client.post(f"/orders/{order['id']}/pay", json=pay, headers=customer_headers)
    response = client.post(f"/orders/{order['id']}/pay", json=pay, headers=customer_headers)
    assert response.status_code == 409


def test_fulfilment_is_admin_only(client, customer_headers, admin_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 1)), headers=customer_headers
    ).json()
    client.post(
        f"/orders/{order['id']}/pay", json={"card_token": "tok_visa"}, headers=customer_headers
    )

    assert client.post(f"/orders/{order['id']}/ship", headers=customer_headers).status_code == 403
    shipped = client.post(f"/orders/{order['id']}/ship", headers=admin_headers)
    delivered = client.post(f"/orders/{order['id']}/deliver", headers=admin_headers)
    assert shipped.json()["status"] == "shipped"
    assert delivered.json()["status"] == "delivered"


@pytest.mark.parametrize("action", ["ship", "deliver"])
def test_fulfilling_a_missing_order_is_404(client, admin_headers, action):
    assert client.post(f"/orders/999/{action}", headers=admin_headers).status_code == 404


def test_unpaid_orders_cannot_ship(client, customer_headers, admin_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 1)), headers=customer_headers
    ).json()
    assert client.post(f"/orders/{order['id']}/ship", headers=admin_headers).status_code == 409


def test_cancelling_a_pending_order_returns_stock(client, db, customer_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 4)), headers=customer_headers
    ).json()
    response = client.post(f"/orders/{order['id']}/cancel", headers=customer_headers)
    assert response.status_code == 200
    assert response.json()["refunded"] is False
    assert response.json()["order"]["status"] == "cancelled"
    assert stock_of(db, phone.id) == 10


def test_cancelling_a_paid_order_refunds_it(client, app, customer_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 1)), headers=customer_headers
    ).json()
    paid = client.post(
        f"/orders/{order['id']}/pay", json={"card_token": "tok_visa"}, headers=customer_headers
    ).json()
    response = client.post(f"/orders/{order['id']}/cancel", headers=customer_headers)
    assert response.json()["refunded"] is True
    gateway = app.state.payment_gateway
    assert gateway.refunded_amount(paid["payment_ref"]) == 1_500_000 * 118 // 100


def test_shipped_orders_cannot_be_cancelled(client, customer_headers, admin_headers, catalogue):
    phone, _ = catalogue
    order = client.post(
        "/orders", json=order_payload((phone.id, 1)), headers=customer_headers
    ).json()
    client.post(
        f"/orders/{order['id']}/pay", json={"card_token": "tok_visa"}, headers=customer_headers
    )
    client.post(f"/orders/{order['id']}/ship", headers=admin_headers)
    assert client.post(f"/orders/{order['id']}/cancel", headers=customer_headers).status_code == 409


def test_cancelling_gives_the_coupon_use_back(client, db, customer_headers, catalogue, make_coupon):
    phone, _ = catalogue
    make_coupon(code="ONCE", usage_limit=1)
    order = client.post(
        "/orders", json=order_payload((phone.id, 1), coupon="ONCE"), headers=customer_headers
    ).json()
    client.post(f"/orders/{order['id']}/cancel", headers=customer_headers)
    db.expire_all()
    assert db.query(Coupon).one().used_count == 0


def test_customers_get_order_e_mails(client, app, make_user, admin_headers, catalogue):
    from tests.conftest import login

    phone, _ = catalogue
    user = make_user(email="buyer@example.com")
    headers = login(client, user.email)
    order = client.post("/orders", json=order_payload((phone.id, 1)), headers=headers).json()
    client.post(f"/orders/{order['id']}/pay", json={"card_token": "tok_visa"}, headers=headers)
    client.post(f"/orders/{order['id']}/ship", headers=admin_headers)

    subjects = [message.subject for message in app.state.notifier.sent_to("buyer@example.com")]
    assert subjects == [
        f"ShopLite order #{order['id']} confirmed",
        f"ShopLite order #{order['id']} shipped",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [], "shipping_state": "MH"},
        {"items": [{"product_id": 1, "quantity": 0}], "shipping_state": "MH"},
        {"shipping_state": "MH"},
    ],
)
def test_malformed_orders_are_rejected(client, customer_headers, payload):
    assert client.post("/orders", json=payload, headers=customer_headers).status_code == 422
