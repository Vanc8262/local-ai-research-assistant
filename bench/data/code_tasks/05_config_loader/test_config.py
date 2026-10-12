import pytest

from config import DEFAULTS, load_config


def test_empty_text_gives_defaults():
    assert load_config("") == {"host": "localhost", "port": 8080, "debug": False}


def test_result_is_a_copy():
    result = load_config("")
    result["port"] = 1
    assert DEFAULTS["port"] == 8080


def test_overrides_and_types():
    result = load_config("host = example.org\nport = 9000\ndebug = TRUE")
    assert result == {"host": "example.org", "port": 9000, "debug": True}


def test_debug_false():
    assert load_config("debug = false")["debug"] is False


def test_skips_blank_lines_and_comments():
    assert load_config("\n# a comment\n\nport = 81\n")["port"] == 81


def test_value_may_contain_equals():
    assert load_config("host = a=b")["host"] == "a=b"


def test_line_without_equals():
    with pytest.raises(ValueError, match="line 2"):
        load_config("port = 81\nnonsense")


def test_unknown_key():
    with pytest.raises(ValueError, match="line 1"):
        load_config("colour = blue")


def test_bad_port():
    with pytest.raises(ValueError, match="line 3"):
        load_config("# header\n\nport = abc")


def test_bad_debug():
    with pytest.raises(ValueError, match="line 1"):
        load_config("debug = maybe")
