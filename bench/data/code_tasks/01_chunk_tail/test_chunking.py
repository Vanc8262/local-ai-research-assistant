import pytest

from chunking import chunk


def test_exact_multiple():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_keeps_short_tail():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_size_larger_than_list():
    assert chunk([1, 2], 5) == [[1, 2]]


def test_empty():
    assert chunk([], 3) == []


def test_rejects_non_positive_size():
    with pytest.raises(ValueError):
        chunk([1], 0)
