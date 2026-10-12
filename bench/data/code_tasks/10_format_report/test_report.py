from report import format_report


def test_two_items():
    text = format_report([("Widget", 3, 2.5), ("Gear", 10, 0.75)])
    assert text.split("\n") == [
        "Widget         3      7.50",
        "Gear          10      7.50",
        "TOTAL                15.00",
    ]


def test_long_name_is_cut():
    text = format_report([("Extra-long item name", 1, 1000)])
    assert text.split("\n") == [
        "Extra-long i   1   1000.00",
        "TOTAL              1000.00",
    ]


def test_empty():
    assert format_report([]) == "TOTAL                 0.00"


def test_every_line_is_26_wide_and_no_trailing_newline():
    text = format_report([("A", 1, 1), ("B", 2, 2)])
    assert not text.endswith("\n")
    assert all(len(line) == 26 for line in text.split("\n"))
