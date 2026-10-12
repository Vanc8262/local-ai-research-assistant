from intervals import merge_intervals


def test_overlapping():
    assert merge_intervals([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]


def test_unsorted_input():
    assert merge_intervals([(8, 10), (2, 6), (1, 3)]) == [(1, 6), (8, 10)]


def test_touching_intervals_merge():
    assert merge_intervals([(1, 2), (2, 3)]) == [(1, 3)]


def test_contained_interval():
    assert merge_intervals([(1, 10), (2, 3)]) == [(1, 10)]


def test_empty_and_single():
    assert merge_intervals([]) == []
    assert merge_intervals([(4, 5)]) == [(4, 5)]


def test_input_not_modified():
    data = [(5, 6), (1, 2)]
    merge_intervals(data)
    assert data == [(5, 6), (1, 2)]
