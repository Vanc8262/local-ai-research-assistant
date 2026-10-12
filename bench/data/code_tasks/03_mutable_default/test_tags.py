from tags import add_tag


def test_calls_do_not_share_state():
    assert add_tag("a") == ["a"]
    assert add_tag("b") == ["b"]


def test_appends_to_given_tags():
    assert add_tag("y", ["x"]) == ["x", "y"]


def test_does_not_modify_callers_list():
    original = ["x"]
    result = add_tag("y", original)
    assert original == ["x"]
    assert result is not original


def test_accepts_tuple():
    assert add_tag("z", ("x", "y")) == ["x", "y", "z"]
