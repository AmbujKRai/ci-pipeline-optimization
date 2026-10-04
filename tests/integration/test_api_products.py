from datetime import datetime

import pytest

from app.models import Product

NEW_PRODUCT = {
    "sku": "EL-SPEAKER-01",
    "name": "Bluetooth Speaker",
    "description": "Portable, 12 h battery.",
    "category": "electronics",
    "price": "1999.00",
    "stock": 40,
}


def test_empty_catalogue(client):
    body = client.get("/products").json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 1}


def test_only_active_products_are_listed(client, make_product):
    make_product(name="Visible")
    make_product(name="Hidden", is_active=False)
    names = [item["name"] for item in client.get("/products").json()["items"]]
    assert names == ["Visible"]


def test_pagination(client, make_product):
    for _ in range(25):
        make_product()
    first = client.get("/products", params={"page_size": 10}).json()
    last = client.get("/products", params={"page_size": 10, "page": 3}).json()
    assert (first["total"], first["pages"], len(first["items"])) == (25, 3, 10)
    assert len(last["items"]) == 5


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("name", ["Apple", "Banana", "Cherry"]),
        ("-name", ["Cherry", "Banana", "Apple"]),
        ("price", ["Banana", "Cherry", "Apple"]),
        ("-price", ["Apple", "Cherry", "Banana"]),
    ],
)
def test_sorting(client, make_product, sort, expected):
    make_product(name="Apple", price_paise=30_000)
    make_product(name="Banana", price_paise=10_000)
    make_product(name="Cherry", price_paise=20_000)
    items = client.get("/products", params={"sort": sort}).json()["items"]
    assert [item["name"] for item in items] == expected


def test_sort_by_newest_shows_latest_first(client, make_product):
    for day, name in enumerate(["First", "Second", "Third"], start=1):
        make_product(name=name, created_at=datetime(2026, 1, day))
    items = client.get("/products", params={"sort": "newest"}).json()["items"]
    assert [item["name"] for item in items] == ["Third", "Second", "First"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("speaker", ["Bluetooth Speaker"]),
        ("SPEAKER", ["Bluetooth Speaker"]),
        ("el-spk", ["Bluetooth Speaker"]),
        ("lamp", ["Desk Lamp"]),
        ("100%", ["100% Cotton Towel"]),
        ("zzz", []),
    ],
)
def test_search_by_name_or_sku(client, make_product, query, expected):
    make_product(name="Bluetooth Speaker", sku="EL-SPK-1")
    make_product(name="Desk Lamp", sku="HM-LAMP-1", category="home")
    make_product(name="100% Cotton Towel", sku="HM-TOWEL-1", category="home")
    items = client.get("/products", params={"q": query}).json()["items"]
    assert [item["name"] for item in items] == expected


def test_percent_sign_in_search_is_not_a_wildcard(client, make_product):
    make_product(name="Plain Towel")
    assert client.get("/products", params={"q": "%"}).json()["total"] == 0


@pytest.mark.parametrize("category", ["books", "home"])
def test_filter_by_category(client, make_product, category):
    make_product(category="books")
    make_product(category="home")
    make_product(category="home")
    items = client.get("/products", params={"category": category}).json()["items"]
    assert {item["category"] for item in items} == {category}


def test_unknown_category_filter_is_rejected(client):
    assert client.get("/products", params={"category": "toys"}).status_code == 422


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"min_price": "150"}, ["Mid", "Pricey"]),
        ({"max_price": "150"}, ["Cheap"]),
        ({"min_price": "100", "max_price": "300"}, ["Cheap", "Mid", "Pricey"]),
        ({"min_price": "250.50"}, ["Pricey"]),
    ],
)
def test_price_filters(client, make_product, params, expected):
    make_product(name="Cheap", price_paise=10_000)
    make_product(name="Mid", price_paise=20_000)
    make_product(name="Pricey", price_paise=30_000)
    items = client.get("/products", params=params).json()["items"]
    assert [item["name"] for item in items] == expected


def test_min_price_above_max_price_is_rejected(client):
    response = client.get("/products", params={"min_price": "500", "max_price": "100"})
    assert response.status_code == 422


def test_in_stock_filter(client, make_product):
    make_product(name="Available", stock=3)
    make_product(name="Sold out", stock=0)
    items = client.get("/products", params={"in_stock": "true"}).json()["items"]
    assert [item["name"] for item in items] == ["Available"]


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
def test_bad_paging_parameters(client, params):
    assert client.get("/products", params=params).status_code == 422


def test_get_one_product(client, make_product):
    product = make_product(name="Kettle", price_paise=149_900, category="home")
    body = client.get(f"/products/{product.id}").json()
    assert body["price"] == "1499.00"
    assert body["gst_rate"] == 18


def test_missing_and_inactive_products_are_404(client, make_product):
    hidden = make_product(is_active=False)
    assert client.get("/products/9999").status_code == 404
    assert client.get(f"/products/{hidden.id}").status_code == 404


def test_admin_can_create_a_product(client, admin_headers):
    response = client.post("/products", json=NEW_PRODUCT, headers=admin_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["price"] == "1999.00"
    assert body["gst_rate"] == 18
    assert client.get(f"/products/{body['id']}").status_code == 200


def test_customers_cannot_create_products(client, customer_headers):
    response = client.post("/products", json=NEW_PRODUCT, headers=customer_headers)
    assert response.status_code == 403


def test_creating_a_product_requires_login(client):
    assert client.post("/products", json=NEW_PRODUCT).status_code == 401


def test_duplicate_sku_is_refused(client, admin_headers):
    client.post("/products", json=NEW_PRODUCT, headers=admin_headers)
    assert client.post("/products", json=NEW_PRODUCT, headers=admin_headers).status_code == 409


@pytest.mark.parametrize(
    "change",
    [{"price": "-5"}, {"category": "toys"}, {"sku": "bad sku"}, {"stock": -1}, {"name": "x"}],
)
def test_invalid_products_are_rejected(client, admin_headers, change):
    response = client.post("/products", json={**NEW_PRODUCT, **change}, headers=admin_headers)
    assert response.status_code == 422


def test_admin_can_update_price_and_stock(client, admin_headers, make_product):
    product = make_product(price_paise=10_000, stock=5)
    response = client.patch(
        f"/products/{product.id}", json={"price": "125.50", "stock": 9}, headers=admin_headers
    )
    assert response.status_code == 200
    assert (response.json()["price"], response.json()["stock"]) == ("125.50", 9)


def test_explicit_nulls_in_an_update_are_ignored(client, admin_headers, make_product):
    product = make_product(name="Keep me")
    response = client.patch(f"/products/{product.id}", json={"name": None}, headers=admin_headers)
    assert response.json()["name"] == "Keep me"


def test_updating_a_missing_product_is_404(client, admin_headers):
    assert (
        client.patch("/products/999", json={"stock": 1}, headers=admin_headers).status_code == 404
    )


def test_delete_is_a_soft_delete(client, db, admin_headers, make_product):
    product = make_product()
    assert client.delete(f"/products/{product.id}", headers=admin_headers).status_code == 204
    assert client.get(f"/products/{product.id}").status_code == 404
    db.expire_all()
    assert db.get(Product, product.id).is_active is False


def test_restock(client, admin_headers, make_product):
    product = make_product(stock=2)
    response = client.post(
        f"/products/{product.id}/restock", json={"quantity": 48}, headers=admin_headers
    )
    assert response.json()["stock"] == 50


@pytest.mark.parametrize("quantity", [0, 100_001])
def test_restock_quantity_limits(client, admin_headers, make_product, quantity):
    product = make_product()
    response = client.post(
        f"/products/{product.id}/restock", json={"quantity": quantity}, headers=admin_headers
    )
    assert response.status_code == 422


def test_low_stock_report(client, admin_headers, make_product):
    make_product(name="Nearly gone", stock=1)
    make_product(name="Plenty", stock=100)
    response = client.get("/products/low-stock", params={"threshold": 3}, headers=admin_headers)
    assert [item["name"] for item in response.json()] == ["Nearly gone"]


def test_low_stock_report_is_admin_only(client, customer_headers):
    assert client.get("/products/low-stock", headers=customer_headers).status_code == 403
