"""The bench scorecard: quality per question, the paired bootstrap tie rule, ranking, and refusals."""
import csv
import json
from pathlib import Path

import pytest

from assistant import __main__ as cli
from assistant import scorecard
from assistant.bench_spec import REFEREE_CAVEAT, PassLine, load_bench
from assistant.ledger import LEDGER_FIELDS, read_ledger, record_attempt
from assistant.manifest import sha256_file
from assistant.scorecard import (Card, ScorecardRefused, build_cards, paired_bootstrap, per_question, rank,
                                 referee_family, scorecard_markdown, write_scorecard)

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "evals" / "bench_v1.toml"
SPEC = load_bench(SPEC_PATH)
PASS = PassLine(min_facts_right=0.75, max_errors_through=0.05)
SUMMARY_FIELDS = ("question", "mode", "run", "outcome", "research_s", "verify_s", "repair_s", "min_free_ram_mb",
                  "run_id")
REFEREE_FIELDS = ("question", "run", "mode", "run_id", "facts_right", "errors_through", "complete",
                  "referee_sha256", "prompt_version")
CALIBRATION = """# Referee calibration: referee

**PASS**

- supported recall: 95 of 100 = 0.950; 95% interval 0.888 to 0.979; minimum 0.9 (pass)
- not_supported recall: 44 of 50 = 0.880; 95% interval 0.762 to 0.944; minimum 0.85 (pass)
- labelled unclear (excluded): 3
"""


def make_eval(root: Path, name: str, runs: list[dict], *, version=1, split="dev", candidate="gemma-e4b",
              tree_clean=True, offload=(49, 49), referee=True, gguf=None, questions=None, runs_per_q=1,
              spec_sha=None):
    """An eval folder: manifest.json with a bench block, summary.csv, and referee.csv for the ok runs.
    Each run dict: question, outcome (default ok), minutes, facts, errors, complete, mode, run."""
    out = root / name
    out.mkdir(parents=True)
    summary, refs = [], []
    for n, r in enumerate(runs):
        run_id = f"{name}-r{n}"
        minutes = r.get("minutes", 10)
        row = {"question": r["question"], "mode": r.get("mode", "local"), "run": r.get("run", 1),
               "outcome": r.get("outcome", "ok"), "research_s": minutes * 60 * 0.6, "verify_s": minutes * 60 * 0.3,
               "repair_s": minutes * 60 * 0.1, "min_free_ram_mb": r.get("ram", 4000), "run_id": run_id}
        if row["outcome"] != "ok":
            row["verify_s"] = row["repair_s"] = ""
        summary.append(row)
        if row["outcome"] == "ok":
            refs.append({"question": r["question"], "run": row["run"], "mode": row["mode"], "run_id": run_id,
                         "facts_right": r.get("facts", 0.8), "errors_through": r.get("errors", 0.02),
                         "complete": r.get("complete", True), "referee_sha256": "", "prompt_version": 1})
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary)
    if referee:
        with (out / "referee.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REFEREE_FIELDS)
            writer.writeheader()
            writer.writerows(refs)
    bench = {"version": version, "split": split, "candidate": candidate, "candidate_key": f"key-{name}",
             "candidate_gguf_sha256": "a" * 64, "tree_clean": tree_clean,
             "spec": {"path": "evals/bench_v1.toml", "sha256": spec_sha or sha256_file(SPEC_PATH)},
             "ledger": "results/bench_ledger.csv" if split == "test" else None,
             "offload": list(offload) if offload else None}
    ids = questions or list(dict.fromkeys(r["question"] for r in runs if r.get("mode", "local") == "local"))
    manifest = {"git_dirty": True, "runs": runs_per_q, "questions": ids, "bench": bench,
                "gguf": gguf or {"GEMMA_GGUF": {"path": "D:\\models\\gemma-4-e4b-it-qat.gguf"},
                                 "REFEREE_GGUF": {"path": "D:\\models\\granite-4.2-8b-Q6_K.gguf"}}}
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return out


def questions(n, **run):
    return [{"question": f"q{i:02d}", **run} for i in range(n)]


def card(name, facts, errors=None, minutes=10.0, fit=()):
    """A card from per-question lists (all questions shared)."""
    errors = errors if errors is not None else [0.0] * len(facts)
    per_q = {f"q{i:02d}": {"facts_right": f, "errors_through": e, "minutes": minutes}
             for i, (f, e) in enumerate(zip(facts, errors))}
    mean = lambda values: sum(values) / len(values)
    return Card(candidate=name, candidate_key=f"key-{name}", eval_id=f"eval-{name}", bench_version=1,
                fit=list(fit), facts_right=mean(facts), facts_ci=None, errors_through=mean(errors), errors_ci=None,
                median_minutes=minutes, per_question=per_q, flags=[], passes=False)


# --- quality per question ---------------------------------------------------------------------

def test_quality_counts_failed_run_as_zero_facts(tmp_path):
    out = make_eval(tmp_path, "eval-a", [
        {"question": "q1", "run": 1, "facts": 0.8, "errors": 0.0, "minutes": 10},
        {"question": "q1", "run": 2, "facts": 0.6, "errors": 0.1, "minutes": 20},
        {"question": "q2", "run": 1, "outcome": "no_report", "minutes": 5},
        {"question": "q1", "run": 1, "mode": "hybrid", "facts": 1.0, "errors": 0.0},
    ])
    found = per_question(out)
    assert set(found) == {"q1", "q2"}   # local rows only
    assert found["q1"]["facts_right"] == pytest.approx(0.7)
    assert found["q1"]["errors_through"] == pytest.approx(0.05)
    assert found["q1"]["minutes"] == pytest.approx(15)
    assert found["q2"] == {"facts_right": 0.0, "errors_through": None, "minutes": pytest.approx(3)}  # research only
    assert per_question(out, mode="hybrid")["q1"]["facts_right"] == 1.0


def test_a_question_without_expect_groups_has_no_facts_score(tmp_path):
    out = make_eval(tmp_path, "eval-a", [{"question": "q1", "facts": ""}, {"question": "q2", "facts": 0.5}])
    found = per_question(out)
    assert found["q1"]["facts_right"] is None and found["q2"]["facts_right"] == 0.5


# --- the paired bootstrap -----------------------------------------------------------------------

def test_paired_bootstrap_identical_is_tied():
    a = {f"q{i}": v for i, v in enumerate([0.2, 0.9, 0.5, 0.7, 0.4])}
    mean, low, high = paired_bootstrap(a, dict(a))
    assert mean == 0 and low <= 0 <= high


def test_paired_bootstrap_clear_difference_and_noise():
    a = {f"q{i}": 0.9 for i in range(10)}
    b = {f"q{i}": 0.5 for i in range(10)}
    mean, low, high = paired_bootstrap(a, b)
    assert mean == pytest.approx(0.4) and low > 0
    noisy_b = {f"q{i}": (1.0 if i % 2 else 0.8) for i in range(10)}
    _, low, high = paired_bootstrap({f"q{i}": 0.9 for i in range(10)}, noisy_b)
    assert low < 0 < high


def test_paired_bootstrap_uses_shared_questions_and_skips_none():
    a = {"q1": 0.9, "q2": 0.9, "q3": None, "q4": 0.1}
    b = {"q1": 0.5, "q2": 0.5, "q3": 0.0, "q5": 0.0}
    mean, low, high = paired_bootstrap(a, b, n=200)
    assert (mean, low, high) == (pytest.approx(0.4), pytest.approx(0.4), pytest.approx(0.4))


def test_paired_bootstrap_refuses_when_no_question_is_shared():
    with pytest.raises(ValueError):
        paired_bootstrap({"q1": 0.5}, {"q2": 0.5})


# --- ranking ----------------------------------------------------------------------------------

def test_rank_quality_beats_speed():
    slow_good = card("A", [0.9] * 10, minutes=50)
    fast_poor = card("B", [0.5] * 10, minutes=5)
    assert [c.candidate for c in rank([fast_poor, slow_good], PASS)] == ["A", "B"]


def test_rank_tie_goes_to_faster():
    facts = [0.8, 0.6, 0.9, 0.7, 1.0, 0.5]
    slow, fast = card("slow", facts, minutes=40), card("fast", facts, minutes=20)
    assert [c.candidate for c in rank([slow, fast], PASS)] == ["fast", "slow"]


def test_rank_errors_break_a_facts_tie():
    facts = [0.8] * 10
    careful = card("careful", facts, errors=[0.0] * 10, minutes=50)
    sloppy = card("sloppy", facts, errors=[0.2] * 10, minutes=5)
    assert [c.candidate for c in rank([sloppy, careful], PASS)] == ["careful", "sloppy"]


def test_a_measure_with_fewer_than_10_shared_questions_is_tied():
    good, poor = card("good", [0.9] * 9, minutes=50), card("poor", [0.5] * 9, minutes=5)
    assert [c.candidate for c in rank([good, poor], PASS)] == ["poor", "good"]   # tied on 9: faster wins
    good, poor = card("good", [0.9] * 10, minutes=50), card("poor", [0.5] * 10, minutes=5)
    assert [c.candidate for c in rank([good, poor], PASS)] == ["good", "poor"]


def cycle_cards():
    """The reviewer's example: A beats B on facts; A and C, B and C tie on facts, so the faster wins:
    C beats A, B beats C. A cycle, so every Copeland score is 0 and facts_right orders them."""
    return [card("A", [0.9] * 10, minutes=20), card("B", [0.6] * 10, minutes=5),
            card("C", [0, 1, 1, 1, 0, 1, 1, 0, 1, 1], minutes=10)]


def test_rank_is_the_same_for_every_input_order():
    import itertools
    orders = {tuple(c.candidate for c in rank(list(p), PASS)) for p in itertools.permutations(cycle_cards())}
    assert orders == {("A", "C", "B")}


def test_rank_by_copeland_score():
    # D beats everyone on facts; E and F tie with each other on everything but E is faster
    d = card("D", [0.95] * 10, minutes=30)
    e = card("E", [0.5] * 10, minutes=10)
    f = card("F", [0.5] * 10, minutes=20)
    assert [c.candidate for c in rank([f, e, d], PASS)] == ["D", "E", "F"]


def test_scorecard_notes_a_cycle_and_the_shared_question_count():
    ranked = rank(cycle_cards(), PASS)
    text = scorecard_markdown(ranked, SPEC, None)
    assert "Ranks 1–3 are not separable on this bench (A, C, B)" in text
    assert "10 shared questions" in text
    plain = scorecard_markdown(rank([card("D", [0.95] * 10), card("E", [0.5] * 10)], PASS), SPEC, None)
    assert "not separable" not in plain


def test_ranking_paragraph_states_what_the_tests_assume():
    text = scorecard_markdown([card("A", [0.9] * 10)], SPEC, None)
    assert "no multiple-comparison correction" in text
    assert "both produced a report" in text
    assert "unpaired" in text and "at least 10" in text


def test_rank_fit_failure_last():
    unfit = card("unfit", [1.0] * 10, minutes=5, fit=["ram"])
    fit_ok = card("ok", [0.5] * 10, minutes=50)
    ranked = rank([unfit, fit_ok], PASS)
    assert [c.candidate for c in ranked] == ["ok", "unfit"]
    assert ranked[1].passes is False


def test_rank_sets_passes_from_the_pass_line():
    good = card("good", [0.8] * 5, errors=[0.04] * 5)
    few_facts = card("few", [0.7] * 5, errors=[0.0] * 5)
    leaky = card("leaky", [0.9] * 5, errors=[0.06] * 5)
    ranked = {c.candidate: c.passes for c in rank([good, few_facts, leaky], PASS)}
    assert ranked == {"good": True, "few": False, "leaky": False}


def test_rank_refuses_candidates_with_no_shared_questions():
    a = card("A", [0.9] * 3)
    b = card("B", [0.5] * 3)
    b.per_question = {f"other{k}": v for k, v in b.per_question.items()}
    with pytest.raises(ScorecardRefused):
        rank([a, b], PASS)


# --- building cards ---------------------------------------------------------------------------

def test_referee_family_flag(tmp_path):
    assert referee_family("D:\\models\\gemma-4-e4b-it.gguf", "D:\\models\\Gemma_3_27b.gguf")
    assert referee_family("Qwen3.5-9B-Q4_K_M.gguf", "/models/qwen3.5_27b.gguf")
    assert not referee_family("gemma-4-e4b.gguf", "qwen3.5-27b.gguf")
    assert not referee_family("", "")
    same = make_eval(tmp_path, "eval-a", questions(3), gguf={
        "GEMMA_GGUF": {"path": "D:\\models\\gemma-4-e4b-it-qat.gguf"},
        "REFEREE_GGUF": {"path": "D:\\models\\gemma-4-12b-it-qat.gguf"}})
    other = make_eval(tmp_path, "eval-b", questions(3))
    (a,), _ = build_cards([same], SPEC, env={})
    (b,), _ = build_cards([other], SPEC, env={})
    assert "referee family" in a.flags and "referee family" not in b.flags


def test_card_numbers_fit_and_unknown_gpu(tmp_path):
    out = make_eval(tmp_path, "eval-a", questions(4, facts=0.8, errors=0.02, minutes=10) +
                    [{"question": "q04", "outcome": "no_report"}], offload=None)
    (c,), reference = build_cards([out], SPEC, env={})
    assert reference == []
    assert c.candidate == "gemma-e4b" and c.eval_id == "eval-a" and c.bench_version == 1
    assert c.facts_right == pytest.approx(0.64)    # 4 x 0.8 and a failed run's 0
    assert c.errors_through == pytest.approx(0.02)
    assert c.fit == ["errors"]                      # 1 of 5 runs ended without a report
    assert "gpu?" in c.flags
    assert c.facts_ci[0] <= c.facts_right <= c.facts_ci[1]


def test_missing_local_runs_count_as_failed_runs(tmp_path):
    out = make_eval(tmp_path, "eval-a", questions(2, facts=1.0), questions=["q00", "q01", "q02"])
    (c,), _ = build_cards([out], SPEC, env={})
    assert c.per_question["q02"]["facts_right"] == 0.0 and "errors" in c.fit


def test_hybrid_rows_are_reference_not_ranked(tmp_path):
    out = make_eval(tmp_path, "eval-a", questions(3, facts=0.6) + questions(3, mode="hybrid", facts=0.9))
    cards, reference = build_cards([out], SPEC, env={})
    assert len(cards) == 1 and cards[0].facts_right == pytest.approx(0.6)
    assert len(reference) == 1 and reference[0].facts_right == pytest.approx(0.9)
    text = scorecard_markdown(rank(cards, SPEC.pass_line), SPEC, None, reference=reference)
    assert "Reference (not ranked)" in text
    assert text.index("Reference (not ranked)") > text.index("| 1 |")


def test_scorecard_refuses_mixed_versions(tmp_path):
    a = make_eval(tmp_path, "eval-a", questions(3), version=1)
    b = make_eval(tmp_path, "eval-b", questions(3), version=2)
    with pytest.raises(ScorecardRefused, match="version"):
        build_cards([a, b], SPEC, env={})


def test_scorecard_refuses_mixed_splits_and_other_spec_versions(tmp_path):
    a = make_eval(tmp_path, "eval-a", questions(3), split="dev")
    b = make_eval(tmp_path, "eval-b", questions(3), split="test")
    with pytest.raises(ScorecardRefused, match="split"):
        build_cards([a, b], SPEC, env={})
    c = make_eval(tmp_path, "eval-c", questions(3), version=2)
    with pytest.raises(ScorecardRefused, match="version"):
        build_cards([c], SPEC, env={})   # one eval, but not the spec's version


def test_scorecard_refuses_an_eval_that_is_not_a_bench_run(tmp_path):
    out = make_eval(tmp_path, "eval-a", questions(3))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    del manifest["bench"]
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ScorecardRefused, match="bench"):
        build_cards([out], SPEC, env={})


def test_scorecard_refuses_incomplete_referee_runs_and_names_them(tmp_path):
    out = make_eval(tmp_path, "eval-a", [{"question": "q1"}, {"question": "q2", "complete": False}])
    with pytest.raises(ScorecardRefused, match="eval-a-r1") as exc:
        build_cards([out], SPEC, env={})
    assert "eval-a-r0" not in str(exc.value) and "assistant referee" in str(exc.value)


def test_scorecard_refuses_ok_runs_the_referee_has_not_scored(tmp_path):
    out = make_eval(tmp_path, "eval-a", questions(2), referee=False)
    with pytest.raises(ScorecardRefused, match="eval-a-r0"):
        build_cards([out], SPEC, env={})


def test_scorecard_refuses_another_referee_prompt_version(tmp_path):
    out = make_eval(tmp_path, "eval-a", questions(2))
    text = (out / "referee.csv").read_text(encoding="utf-8").replace(",1\n", ",2\n")
    (out / "referee.csv").write_text(text, encoding="utf-8")
    with pytest.raises(ScorecardRefused, match="prompt"):
        build_cards([out], SPEC, env={})


def committed(*eval_ids):
    lines = [",".join(LEDGER_FIELDS)] + [f"1,gemma-e4b,key-{e},{'a' * 64},c0ffee,test,{e},started,2026-11-15,"
                                          for e in eval_ids]
    return "\n".join(lines)


def test_test_split_refusals(tmp_path):
    no_referee = make_eval(tmp_path, "eval-a", [{"question": "q1", "outcome": "no_report"}], split="test",
                           referee=False)
    with pytest.raises(ScorecardRefused, match="referee.csv"):
        build_cards([no_referee], SPEC, env={}, committed_ledger=committed("eval-a"))
    dirty = make_eval(tmp_path, "eval-b", questions(2), split="test", tree_clean=False)
    with pytest.raises(ScorecardRefused, match="tree"):
        build_cards([dirty], SPEC, env={}, committed_ledger=committed("eval-b"))
    uncommitted = make_eval(tmp_path, "eval-c", questions(2), split="test")
    with pytest.raises(ScorecardRefused, match="commit"):
        build_cards([uncommitted], SPEC, env={}, committed_ledger=committed("eval-b"))
    with pytest.raises(ScorecardRefused, match="commit"):
        build_cards([uncommitted], SPEC, env={}, committed_ledger=None)
    other_spec = make_eval(tmp_path, "eval-d", questions(2), split="test", spec_sha="0" * 64)
    with pytest.raises(ScorecardRefused, match="spec"):
        build_cards([other_spec], SPEC, env={}, committed_ledger=committed("eval-d"),
                    spec_sha256=sha256_file(SPEC_PATH))
    fine = make_eval(tmp_path, "eval-e", questions(2), split="test")
    cards, _ = build_cards([fine], SPEC, env={}, committed_ledger=committed("eval-e"),
                           spec_sha256=sha256_file(SPEC_PATH))
    assert cards[0].split == "test"


# --- the scorecard ----------------------------------------------------------------------------

def test_scorecard_has_caveat(tmp_path):
    a = make_eval(tmp_path, "eval-a", questions(5, facts=0.9, errors=0.01))
    cards, _ = build_cards([a], SPEC, env={})
    text = scorecard_markdown(rank(cards, SPEC.pass_line), SPEC, CALIBRATION)
    assert REFEREE_CAVEAT in text
    assert "supported recall: 95 of 100" in text and "not_supported recall: 44 of 50" in text
    assert "gemma-e4b" in text and "bench v1" in text.lower()
    missing = scorecard_markdown(cards, SPEC, None)
    assert REFEREE_CAVEAT in missing and "not calibrated" in missing.lower()


def test_write_scorecard_writes_markdown_and_csv(tmp_path):
    a = make_eval(tmp_path, "eval-a", questions(10, facts=0.9, errors=0.01, minutes=10))
    b = make_eval(tmp_path, "eval-b", questions(10, facts=0.5, errors=0.01, minutes=5), candidate="qwen-9b")
    cards, _ = build_cards([a, b], SPEC, env={})
    out = tmp_path / "results" / "bench_v1_scorecard.md"
    write_scorecard(out, rank(cards, SPEC.pass_line), SPEC, calibration_md=CALIBRATION)
    assert REFEREE_CAVEAT in out.read_text(encoding="utf-8")
    with out.with_suffix(".csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["rank"], r["candidate"], r["passes"]) for r in rows] == [("1", "gemma-e4b", "True"),
                                                                        ("2", "qwen-9b", "False")]
    assert rows[0]["fit"] == "pass" and float(rows[0]["facts_right"]) == pytest.approx(0.9)
    assert [r["result"] for r in rows] == ["pass", "miss"]


def test_an_errored_attempt_shows_its_scored_result():
    good = card("good", [0.9] * 10)
    good.ledger_status = "error"
    ranked = rank([good, card("poor", [0.5] * 10)], PASS)
    assert "error (scored passed)" in scorecard_markdown(ranked, SPEC, None)
    ranked[0].passes = False
    assert "error (scored missed)" in scorecard_markdown(ranked, SPEC, None)


# --- the CLI ----------------------------------------------------------------------------------

CONFIG = """
[assistant]
default_tier = "local"
runs_dir = "{runs}"

[tiers.local]
kind = "local"
base_url = "http://127.0.0.1:1234/v1"
model = "gemma"
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A stand-in for REPO_ROOT with the ledger, and a fake git whose HEAD holds `state["head"]`."""
    root = tmp_path / "repo"
    (root / "results").mkdir(parents=True)
    monkeypatch.setattr(cli, "REPO_ROOT", root)
    ledger = root / "results" / "bench_ledger.csv"
    ledger.write_text(",".join(LEDGER_FIELDS) + "\n", encoding="utf-8")
    state = {"head": ""}
    monkeypatch.setattr(cli, "_git", lambda *args: state["head"] if args[0] == "show" else "")
    config = tmp_path / "assistant.toml"
    config.write_text(CONFIG.format(runs=(tmp_path / "runs").as_posix()), encoding="utf-8")

    def go(*argv):
        return cli.main(["--config", str(config), "scorecard", *argv, "--bench", str(SPEC_PATH)])
    go.ledger, go.state, go.root = ledger, state, root
    return go


def attempt(ledger, eval_id):
    record_attempt(ledger, {"bench_version": 1, "candidate": "gemma-e4b", "candidate_key": f"key-{eval_id}",
                            "gguf_sha256": "a" * 64, "git_commit": "c0ffee", "split": "test", "eval_id": eval_id})


def test_cli_scorecard_marks_test_attempts_passed_or_missed(repo, tmp_path, capsys):
    good = make_eval(tmp_path / "runs", "eval-good", questions(10, facts=0.9, errors=0.01), split="test")
    poor = make_eval(tmp_path / "runs", "eval-poor", questions(10, facts=0.5, errors=0.01), split="test")
    for name in ("eval-good", "eval-poor"):
        attempt(repo.ledger, name)
    repo.state["head"] = repo.ledger.read_text(encoding="utf-8").strip()   # the attempts are committed
    out = tmp_path / "card.md"
    assert repo(str(good), str(poor), "--out", str(out)) == 0
    assert {r["eval_id"]: r["status"] for r in read_ledger(repo.ledger)} == {"eval-good": "passed",
                                                                             "eval-poor": "missed"}
    assert REFEREE_CAVEAT in out.read_text(encoding="utf-8") and out.with_suffix(".csv").is_file()
    assert REFEREE_CAVEAT in capsys.readouterr().out


def test_cli_scorecard_refuses_an_uncommitted_attempt_and_leaves_the_ledger(repo, tmp_path, capsys):
    good = make_eval(tmp_path / "runs", "eval-good", questions(5, facts=0.9), split="test")
    attempt(repo.ledger, "eval-good")   # recorded, but HEAD still holds the header only
    repo.state["head"] = ",".join(LEDGER_FIELDS)
    before = repo.ledger.read_text(encoding="utf-8")
    out = tmp_path / "card.md"
    assert repo(str(good), "--out", str(out)) == 1
    assert "commit" in capsys.readouterr().out
    assert repo.ledger.read_text(encoding="utf-8") == before and not out.exists()


def test_cli_scorecard_dev_split_writes_no_ledger(repo, tmp_path):
    a = make_eval(tmp_path / "runs", "eval-a", questions(3))
    before = repo.ledger.read_text(encoding="utf-8")
    assert repo(str(a), "--out", str(tmp_path / "card.md")) == 0
    assert repo.ledger.read_text(encoding="utf-8") == before


def test_cli_scorecard_refuses_mixed_versions_with_exit_1(repo, tmp_path, capsys):
    a = make_eval(tmp_path / "runs", "eval-a", questions(3), version=1)
    b = make_eval(tmp_path / "runs", "eval-b", questions(3), version=2)
    assert repo(str(a), str(b), "--out", str(tmp_path / "card.md")) == 1
    assert "refused" in capsys.readouterr().out.lower()


def test_cli_scorecard_keeps_an_error_attempt(repo, tmp_path):
    good = make_eval(tmp_path / "runs", "eval-good", questions(10, facts=0.9, errors=0.01), split="test")
    attempt(repo.ledger, "eval-good")
    from assistant.ledger import finish_attempt
    finish_attempt(repo.ledger, "eval-good", "error")   # a run had no checked report
    repo.state["head"] = repo.ledger.read_text(encoding="utf-8").strip()
    out = tmp_path / "card.md"
    assert repo(str(good), "--out", str(out)) == 0
    assert [r["status"] for r in read_ledger(repo.ledger)] == ["error"]
    assert "error (scored passed)" in out.read_text(encoding="utf-8")
    with out.with_suffix(".csv").open(encoding="utf-8") as handle:
        assert next(csv.DictReader(handle))["result"] == "error (scored passed)"


def test_cli_scorecard_checks_every_attempt_before_writing_any(repo, tmp_path, capsys):
    a = make_eval(tmp_path / "runs", "eval-a", questions(10, facts=0.9), split="test")
    b = make_eval(tmp_path / "runs", "eval-b", questions(10, facts=0.5), split="test")
    attempt(repo.ledger, "eval-a")
    attempt(repo.ledger, "eval-b")
    repo.state["head"] = repo.ledger.read_text(encoding="utf-8").strip()
    lines = repo.ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    repo.ledger.write_text("".join(lines[:2]), encoding="utf-8")   # the working copy lost eval-b
    before = repo.ledger.read_text(encoding="utf-8")
    out = tmp_path / "card.md"
    assert repo(str(a), str(b), "--out", str(out)) == 1
    assert "eval-b" in capsys.readouterr().out
    assert repo.ledger.read_text(encoding="utf-8") == before and not out.exists()


def test_cli_scorecard_default_paths_are_in_the_repo(repo, tmp_path, monkeypatch):
    (repo.root / "results" / "calibration_v1_referee.md").write_text(CALIBRATION, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    a = make_eval(tmp_path / "runs", "eval-a", questions(3))
    assert repo(str(a)) == 0
    written = repo.root / "results" / "bench_v1_scorecard.md"
    assert "supported recall: 95 of 100" in written.read_text(encoding="utf-8")
    assert written.with_suffix(".csv").is_file() and not (elsewhere / "results").exists()
