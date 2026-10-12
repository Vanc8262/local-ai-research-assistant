import json

import pytest

from bench.suites import research, vision

SETS = json.loads(research.DATA.read_text(encoding="utf-8"))
BUS = SETS[0]


def test_five_sets_and_every_fact_is_in_its_source():
    import re
    assert len(SETS) == 5
    for entry in SETS:
        for fact in entry["facts"]:
            source = entry["sources"][fact["source"] - 1]["text"]
            assert re.search(fact["pattern"], source, re.IGNORECASE), (entry["id"], fact["pattern"])
            others = [s["text"] for i, s in enumerate(entry["sources"], 1) if i != fact["source"]]
            assert not any(re.search(fact["pattern"], text, re.IGNORECASE) for text in others), \
                (entry["id"], fact["pattern"], "also matches another source")


def test_prompt_numbers_the_sources():
    prompt = research.build_prompt(BUS)
    assert "[1] Harlow Valley Transit Authority" in prompt and "[3] Letter" in prompt
    assert BUS["question"] in prompt


GOOD = ("Bus travel time fell from 31 to 24 minutes [1]. Ridership rose 11% and 68% of riders "
        "said reliability improved [2]. Merchants asked for loading bays. [3]")


def test_perfect_answer():
    score = research.score_answer(BUS, GOOD)
    assert score["citations_valid"] and score["fact_recall"] == 1 and score["citation_accuracy"] == 1
    assert score["auto_score"] == 1 and score["within_length"]


def test_wrong_source_lowers_citation_accuracy():
    score = research.score_answer(BUS, GOOD.replace("24 minutes [1]", "24 minutes [3]"))
    assert score["fact_recall"] == 1 and score["citation_accuracy"] == 0.75


def test_missing_fact_lowers_recall():
    score = research.score_answer(BUS, "Bus travel time fell to 24 minutes [1].")
    assert score["fact_recall"] == 0.25 and score["citation_accuracy"] == 1


def test_no_citations_or_bad_numbers_are_invalid():
    assert not research.score_answer(BUS, "Travel time fell to 24 minutes.")["citations_valid"]
    bad = research.score_answer(BUS, "Travel time fell to 24 minutes [7].")
    assert not bad["citations_valid"] and bad["citation_accuracy"] == 0


def test_citation_styles():
    for text in ("Ridership rose 11% [2].", "Ridership rose 11%. [2]", "Ridership rose 11%.[2]",
                 "Ridership rose 11% [1, 2].", "- Ridership rose 11% [2]\n- Other point [1]"):
        assert research.score_answer(BUS, text)["citation_accuracy"] == 1, text
    # a citation in the next sentence does not count for this one
    assert research.score_answer(BUS, "Ridership rose 11%. Travel time fell [2].")["citation_accuracy"] == 0


def test_decimal_points_do_not_split_sentences():
    lake = next(s for s in SETS if s["id"] == "lake-orrin")
    score = research.score_answer(lake, "The council approved $2.3 million for buffer strips [3].")
    assert score["citation_accuracy"] == 1


def test_length_limit():
    assert not research.score_answer(BUS, "word " * 200 + "[1]")["within_length"]


def test_vision_items_and_checks():
    items = vision.build_items()
    assert [i["id"] for i in items] == ["bar-highest", "bar-read-value", "line-peak", "table-cell", "terminal-error"]
    for item in items:
        assert item["image"].size == (800, 500)
        assert vision.to_data_url(item["image"]).startswith("data:image/png;base64,")
    check = {i["id"]: i for i in items}
    assert vision.is_correct(check["bar-highest"], "South")
    assert not vision.is_correct(check["bar-highest"], "South or Central")
    assert vision.is_correct(check["bar-read-value"], "The value is 180.")
    assert not vision.is_correct(check["bar-read-value"], "210")
    assert vision.is_correct(check["line-peak"], "The line may peak in Apr.")
    assert not vision.is_correct(check["line-peak"], "March")
    assert vision.is_correct(check["terminal-error"], "Line 17 raised a ValueError.")
    assert not vision.is_correct(check["terminal-error"], "ValueError on line 42")


def test_vision_is_skipped_for_text_only_models(tmp_path):
    from bench.config import Config, ModelCfg
    from bench.suites.base import Ctx
    ctx = Ctx(cfg=Config(), model=ModelCfg(id="m"), run_dir=tmp_path, chat_fn=None)
    assert vision.run(ctx).skipped
