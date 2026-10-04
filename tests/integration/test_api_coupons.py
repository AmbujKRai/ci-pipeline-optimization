import pytest

PERCENT = {"code": "MONSOON15", "kind": "percent", "value": "15", "max_discount": "500"}
FLAT = {"code": "FLAT250", "kind": "flat", "value": "250", "min_order": "2000"}


def test_admin_creates_a_percent_coupon(client, admin_headers):
    response = client.post("/coupons", json=PERCENT, headers=admin_headers)
    assert response.status_code == 201
    body = response.json()
    assert (body["value"], body["max_discount"], body["used_count"]) == ("15", "500.00", 0)


def test_admin_creates_a_flat_coupon(client, admin_headers):
    body = client.post("/coupons", json=FLAT, headers=admin_headers).json()
    assert (body["value"], body["min_order"]) == ("250.00", "2000.00")


def test_coupon_expiry_is_stored_in_utc(client, admin_headers):
    payload = {**FLAT, "code": "IST", "expires_at": "2026-12-31T23:30:00+05:30"}
    body = client.post("/coupons", json=payload, headers=admin_headers).json()
    assert body["expires_at"] == "2026-12-31T18:00:00"


def test_duplicate_coupon_code_is_refused(client, admin_headers):
    client.post("/coupons", json=PERCENT, headers=admin_headers)
    assert client.post("/coupons", json=PERCENT, headers=admin_headers).status_code == 409


@pytest.mark.parametrize(
    "change", [{"value": "95"}, {"code": "lower"}, {"kind": "bogo"}, {"usage_limit": 0}]
)
def test_invalid_coupons_are_rejected(client, admin_headers, change):
    response = client.post("/coupons", json={**PERCENT, **change}, headers=admin_headers)
    assert response.status_code == 422


def test_coupons_are_listed_by_code(client, admin_headers):
    client.post("/coupons", json=PERCENT, headers=admin_headers)
    client.post("/coupons", json=FLAT, headers=admin_headers)
    codes = [coupon["code"] for coupon in client.get("/coupons", headers=admin_headers).json()]
    assert codes == ["FLAT250", "MONSOON15"]


@pytest.mark.parametrize("method", ["get", "post"])
def test_coupons_are_admin_only(client, customer_headers, method):
    response = client.request(method, "/coupons", json=PERCENT, headers=customer_headers)
    assert response.status_code == 403
