import pytest

from app.models import Order
from app.services.orders import (
    MAX_ORDER_LINES,
    ORDER_STATUSES,
    ORDER_TRANSITIONS,
    InvalidTransitionError,
    OrderLineRequest,
    can_transition,
    merge_lines,
    transition,
)

ALLOWED = {
    ("pending", "paid"),
    ("pending", "cancelled"),
    ("paid", "shipped"),
    ("paid", "cancelled"),
    ("shipped", "delivered"),
}


@pytest.mark.parametrize("current", ORDER_STATUSES)
@pytest.mark.parametrize("target", ORDER_STATUSES)
def test_transition_matrix(current, target):
    assert can_transition(current, target) is ((current, target) in ALLOWED)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_transition_updates_the_order(current, target):
    order = Order(status=current)
    transition(order, target)
    assert order.status == target
    assert order.updated_at is not None


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("delivered", "cancelled"),
        ("cancelled", "paid"),
        ("shipped", "paid"),
        ("pending", "shipped"),
        ("pending", "delivered"),
    ],
)
def test_illegal_transition_is_refused(current, target):
    order = Order(status=current)
    with pytest.raises(InvalidTransitionError) as excinfo:
        transition(order, target)
    assert (excinfo.value.current, excinfo.value.target) == (current, target)
    assert order.status == current


def test_unknown_status_is_an_error():
    with pytest.raises(ValueError, match="unknown order status"):
        can_transition("lost-in-transit", "paid")


@pytest.mark.parametrize("terminal", ["delivered", "cancelled"])
def test_terminal_states_have_no_way_out(terminal):
    assert ORDER_TRANSITIONS[terminal] == frozenset()


def test_merge_combines_repeated_products_in_first_seen_order():
    lines = [OrderLineRequest(7, 2), OrderLineRequest(3, 1), OrderLineRequest(7, 3)]
    assert merge_lines(lines) == [OrderLineRequest(7, 5), OrderLineRequest(3, 1)]


def test_merge_rejects_an_empty_order():
    with pytest.raises(ValueError, match="no items"):
        merge_lines([])


def test_merge_rejects_zero_quantity():
    with pytest.raises(ValueError, match="at least 1"):
        merge_lines([OrderLineRequest(1, 0)])


def test_merge_limits_distinct_products():
    lines = [OrderLineRequest(i, 1) for i in range(1, MAX_ORDER_LINES + 2)]
    with pytest.raises(ValueError, match="at most"):
        merge_lines(lines)


def test_merge_checks_quantity_after_combining():
    with pytest.raises(ValueError, match="units per order"):
        merge_lines([OrderLineRequest(1, 600), OrderLineRequest(1, 500)])
