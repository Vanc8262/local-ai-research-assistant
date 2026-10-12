import pytest

from inventory import Inventory


@pytest.fixture
def stock():
    inv = Inventory()
    inv.add("bolt", 10)
    inv.add("nut", 4)
    return inv


def test_remove_reduces_count(stock):
    stock.remove("bolt", 3)
    assert stock.count("bolt") == 7


def test_remove_all_deletes_item(stock):
    stock.remove("nut", 4)
    assert stock.count("nut") == 0
    assert stock.items() == ["bolt"]


def test_remove_unknown_item(stock):
    with pytest.raises(KeyError):
        stock.remove("washer", 1)


@pytest.mark.parametrize("quantity", [0, -2, 11])
def test_remove_bad_quantity(stock, quantity):
    with pytest.raises(ValueError):
        stock.remove("bolt", quantity)
    assert stock.count("bolt") == 10


def test_total(stock):
    assert stock.total() == 14
    stock.remove("bolt", 10)
    assert stock.total() == 4


def test_total_empty():
    assert Inventory().total() == 0


def test_existing_behaviour(stock):
    stock.add("bolt")
    assert stock.count("bolt") == 11
    assert stock.items() == ["bolt", "nut"]
