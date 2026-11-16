"""Checks on the research bench's question files (spec section 6.8 keeps them honest).

The dev files exist now, so the checks that apply to every split (unique ids, disjoint splits,
`expect` groups) run on them. The test split is written later; its checks skip until
evals/bench_test_questions.toml exists.
"""
from pathlib import Path

import pytest

from assistant.bench_spec import load_bench
from assistant.evaluate import fact_pattern, load_questions

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "evals" / "bench_v1.toml"


def _split(name):
    spec = load_bench(SPEC)
    return [q for path in spec.questions[name] for q in load_questions(ROOT / path)]


def _test_questions():
    spec = load_bench(SPEC)
    paths = [ROOT / p for p in spec.questions["test"]]
    if not all(p.exists() for p in paths):
        pytest.skip("test questions not written yet")
    return _split("test")


def test_question_ids_unique_across_splits():
    ids = [q.id for q in _split("dev")]
    if all((ROOT / p).exists() for p in load_bench(SPEC).questions["test"]):
        ids += [q.id for q in _split("test")]
    assert len(ids) == len(set(ids))


def test_dev_and_test_disjoint():
    test = _test_questions()
    dev = _split("dev")
    assert not {q.id for q in dev} & {q.id for q in test}
    assert not {q.text.strip().lower() for q in dev} & {q.text.strip().lower() for q in test}


def test_test_questions_count():
    assert len(_test_questions()) >= 25


def test_test_questions_public_and_not_codex():
    for q in _test_questions():
        assert q.public, f"{q.id} must be public"
        assert not q.codex, f"{q.id} must not need the codex"


def test_no_fact_leaks_into_question_text():
    for q in _test_questions():
        for group in q.expect or []:
            for term in group:
                assert not fact_pattern(term).search(q.text), f"{q.id}: {term!r} is in the question"


@pytest.mark.parametrize("split", ["dev", "test"])
def test_every_question_has_expect_groups(split):
    questions = _split("dev") if split == "dev" else _test_questions()
    for q in questions:
        assert q.expect and len(q.expect) >= 3, f"{q.id}: needs at least 3 expect groups"
        assert all(group for group in q.expect), f"{q.id}: has an empty expect group"
