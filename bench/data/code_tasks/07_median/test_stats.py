import pytest

from stats import median


def test_odd_length():
    assert median([5, 1, 3]) == 3


def test_even_length():
    assert median([4, 1, 3, 2]) == 2.5


def test_single_value():
    assert median([7]) == 7


def test_two_values():
    assert median([1, 2]) == 1.5


def test_empty():
    with pytest.raises(ValueError):
        median([])


def test_input_not_reordered():
    values = [3, 1, 2]
    median(values)
    assert values == [3, 1, 2]
