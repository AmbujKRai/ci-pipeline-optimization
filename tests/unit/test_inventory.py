import pytest

from app.models import Product
from app.services import inventory


def product(stock: int = 10, is_active: bool = True) -> Product:
    return Product(
        id=1,
        sku="X-1",
        name="Thing",
        category="home",
        price_paise=100,
        stock=stock,
        is_active=is_active,
    )


@pytest.mark.parametrize(("stock", "quantity", "left"), [(10, 1, 9), (10, 10, 0), (5, 3, 2)])
def test_reserve_takes_units_out_of_stock(stock, quantity, left):
    item = product(stock)
    inventory.reserve(item, quantity)
    assert item.stock == left


def test_reserve_more_than_available_fails():
    item = product(stock=2)
    with pytest.raises(inventory.InsufficientStockError) as excinfo:
        inventory.reserve(item, 3)
    assert (excinfo.value.requested, excinfo.value.available) == (3, 2)
    assert item.stock == 2


def test_inactive_product_cannot_be_reserved():
    with pytest.raises(inventory.InactiveProductError):
        inventory.reserve(product(is_active=False), 1)


@pytest.mark.parametrize("quantity", [0, -3])
def test_quantities_must_be_positive(quantity):
    with pytest.raises(ValueError):
        inventory.reserve(product(), quantity)
    with pytest.raises(ValueError):
        inventory.release(product(), quantity)
    with pytest.raises(ValueError):
        inventory.restock(product(), quantity)


def test_release_puts_units_back():
    item = product(stock=4)
    inventory.release(item, 6)
    assert item.stock == 10


def test_restock_has_an_upper_limit():
    item = product(stock=0)
    inventory.restock(item, inventory.MAX_RESTOCK_QUANTITY)
    assert item.stock == inventory.MAX_RESTOCK_QUANTITY
    with pytest.raises(ValueError, match="cannot restock"):
        inventory.restock(item, inventory.MAX_RESTOCK_QUANTITY + 1)


def test_low_stock_lists_active_products_lowest_first(db, make_product):
    make_product(name="Plenty", stock=6)
    make_product(name="Five left", stock=5)
    make_product(name="Sold out", stock=0)
    make_product(name="Three left", stock=3)
    make_product(name="Retired", stock=1, is_active=False)
    names = [item.name for item in inventory.low_stock(db, threshold=5)]
    assert names == ["Sold out", "Three left", "Five left"]


def test_low_stock_threshold_cannot_be_negative(db):
    with pytest.raises(ValueError):
        inventory.low_stock(db, threshold=-1)
