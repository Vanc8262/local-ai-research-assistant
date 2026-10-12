from slugs import slugify


def test_default_behaviour_unchanged():
    assert slugify("Hello, World!") == "hello-world"


def test_custom_separator():
    assert slugify("Hello, World!", sep="_") == "hello_world"


def test_max_words():
    assert slugify("The quick brown fox", max_words=2) == "the-quick"


def test_max_words_zero():
    assert slugify("The quick brown fox", max_words=0) == ""


def test_both_options():
    assert slugify("  Stage 0: model benchmark  ", sep=".", max_words=3) == "stage.0.model"
