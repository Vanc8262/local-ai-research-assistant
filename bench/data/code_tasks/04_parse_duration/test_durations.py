import pytest

from durations import parse_duration


@pytest.mark.parametrize("text, seconds", [
    ("45s", 45),
    ("2h", 7200),
    ("90m", 5400),
    ("1h30m", 5400),
    ("2h5m10s", 7510),
    ("1h10s", 3610),
    ("0s", 0),
])
def test_valid(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "10", "m", "1h1h", "30m1h", "1.5h", "1h 30m", "-5m"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)
