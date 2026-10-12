from summary import summarize


def row(category, amount):
    return {"category": category, "amount": amount}


def test_sums_per_category():
    rows = [row("food", "10"), row("rent", "500"), row("food", "2.5")]
    assert summarize(rows) == {"food": 12.5, "rent": 500.0}


def test_category_case_and_spaces():
    rows = [row(" Food", "1"), row("FOOD ", "2"), row("food", "3")]
    assert summarize(rows) == {"food": 6.0}


def test_skips_bad_amounts():
    rows = [row("food", ""), row("food", "n/a"), row("food", " 4 "), row("rent", "abc")]
    assert summarize(rows) == {"food": 4.0}


def test_skips_empty_category():
    assert summarize([row("  ", "5"), row("fuel", "5")]) == {"fuel": 5.0}


def test_rounds_to_two_decimals():
    rows = [row("fuel", "0.1"), row("fuel", "0.2"), row("fuel", "0.123")]
    assert summarize(rows) == {"fuel": 0.42}


def test_empty_input():
    assert summarize([]) == {}
