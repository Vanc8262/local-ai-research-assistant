"""Calibration set for the referee: sampling, spot check, recall, agreement (no model, fake gateway)."""
import csv
import json
from types import SimpleNamespace

import pytest

from assistant.verify import CITATION_RE
from assistant.calibration import (PAIR_FIELDS, agreement, judge_pairs, meets, recall, sample_pairs, spot_check)

PAGE = "Alpha is very fast indeed. Beta is slow. Gamma is blue."


def write_run(runs, run_id, claims, removed=()):
    """claims: list of (text, verdict) in the final report; removed: texts repair took out."""
    run = runs / run_id
    (run / "pages").mkdir(parents=True)
    body = " ".join(f"{t} [1]." for t, _ in claims)
    claims = [(" ".join(CITATION_RE.sub("", f"{t} [1].").split()), v) for t, v in claims]
    (run / "report.md").write_text(f"# Q\n\n{body}\n\n## Sources\n[1] S https://a/1\n", encoding="utf-8")
    (run / "sources.json").write_text(json.dumps([{"index": 1, "title": "S1", "url": "https://a/1"}]), encoding="utf-8")
    (run / "pages" / "1.txt").write_text(PAGE, encoding="utf-8")
    (run / "verification.json").write_text(json.dumps(
        {"claims": [{"text": t, "verdict": v, "citations": ["1"]} for t, v in claims]}), encoding="utf-8")
    if removed:
        orig = " ".join(f"{t} [1]." for t in [*[t for t, _ in claims], *removed])
        (run / "report.original.md").write_text(f"# Q\n\n{orig}\n\n## Sources\n[1] S https://a/1\n", encoding="utf-8")
        (run / "repair.json").write_text(json.dumps(
            {"changes": [{"action": "removed", "before": f"{t} [1].", "reason": "x"} for t in removed]}), encoding="utf-8")
    return run


def make_eval(tmp_path, name="eval-1", question="d1", n_runs=3):
    runs = tmp_path
    eval_dir = runs / name
    eval_dir.mkdir()
    rows = []
    for i in range(n_runs):
        rid = f"{name}-r{i}"
        write_run(runs, rid,
                  [(f"Alpha number {i} one is fast", "supported"), (f"Beta number {i} two is slow", "supported"),
                   (f"Gamma number {i} three is blue", "unsupported"), (f"Delta number {i} four is red", "partial")],
                  removed=[f"Epsilon number {i} five is green", f"Zeta number {i} six is grey"])
        rows.append({"question": question, "mode": "local", "run": i + 1, "run_id": rid, "outcome": "ok"})
    rows.append({"question": question, "mode": "local", "run": 9, "run_id": "bad", "outcome": "error"})
    with (eval_dir / "summary.csv").open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return eval_dir


def read(path):
    with path.open(encoding="utf-8") as h:
        return list(csv.DictReader(h))


def test_sample_refuses_test_questions(tmp_path):
    eval_dir = make_eval(tmp_path, question="t1")
    with pytest.raises(ValueError, match="t1"):
        sample_pairs([eval_dir], tmp_path / "o.csv", {"d1"}, runs_dir=tmp_path)


def test_sample_is_seeded_and_stratified(tmp_path):
    eval_dir = make_eval(tmp_path)
    a = sample_pairs([eval_dir], tmp_path / "a.csv", {"d1"}, n=9, runs_dir=tmp_path)
    b = sample_pairs([eval_dir], tmp_path / "b.csv", {"d1"}, n=9, runs_dir=tmp_path)
    assert [r["pair_id"] for r in a] == [r["pair_id"] for r in b]
    assert [r["statement"] for r in a] == [r["statement"] for r in b]
    assert len(a) == 9
    assert {"supported", "doubtful", "removed"} <= {r["stratum"] for r in a}
    assert all(r["run_id"] != "bad" for r in a)
    assert all(r["label"] == "" and r["labeller"] == "" for r in a)
    rows = read(tmp_path / "a.csv")
    assert tuple(rows[0]) == PAIR_FIELDS and rows[0]["source_url"] == "https://a/1"
    assert any("Epsilon" in r["statement"] for r in a if r["stratum"] == "removed")


def test_sample_tops_up_when_a_stratum_is_short(tmp_path):
    eval_dir = make_eval(tmp_path, n_runs=1)
    rows = sample_pairs([eval_dir], tmp_path / "o.csv", {"d1"}, n=6, runs_dir=tmp_path)
    assert len(rows) == 6
    assert len({r["pair_id"] for r in rows}) == 6


def labelled(n, **over):
    rows = [{"pair_id": f"p{i}", "label": "supported", "labeller": "claude", "referee_verdict": "supported"}
            for i in range(n)]
    for r in rows:
        r.update(over)
    return rows


def test_spot_check_blanks_labels(tmp_path):
    src = tmp_path / "l.csv"
    rows = [{f: "x" for f in PAIR_FIELDS} | {"pair_id": f"p{i}", "label": "supported", "labeller": "claude"}
            for i in range(40)]
    with src.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=PAIR_FIELDS)
        w.writeheader()
        w.writerows(rows)
    out = spot_check(src, tmp_path / "s.csv", n=25)
    assert len(out) == 25 and len({r["pair_id"] for r in out}) == 25
    assert all(r["label"] == "" and r["labeller"] == "" for r in read(tmp_path / "s.csv"))
    assert [r["pair_id"] for r in out] == [r["pair_id"] for r in spot_check(src, tmp_path / "s2.csv", n=25)]


def test_recall_excludes_unclear():
    rows = (labelled(8) + labelled(2, referee_verdict="not_supported")
            + labelled(3, label="unclear") + labelled(4, label="not_supported", referee_verdict="not_supported")
            + labelled(1, label="not_supported", referee_verdict="supported") + labelled(2, label=""))
    r = recall(rows)
    assert r["supported"][:2] == (8, 10) and r["not_supported"][:2] == (4, 5)
    assert r["unclear_labels"] == 3
    assert r["supported"][2][0] < 0.8 < r["supported"][2][1]


def spec_with(s=0.90, n=0.85):
    return SimpleNamespace(referee=SimpleNamespace(min_supported_recall=s, min_not_supported_recall=n))


def result(s, n):
    return {"supported": (round(s * 100), 100, None), "not_supported": (round(n * 100), 100, None), "unclear_labels": 0}


def test_meets_uses_spec_minimums():
    assert meets(result(0.92, 0.86), spec_with())
    assert not meets(result(0.92, 0.80), spec_with())
    assert not meets(result(0.85, 0.90), spec_with())
    assert not meets({"supported": (0, 0, None), "not_supported": (0, 0, None), "unclear_labels": 0}, spec_with())


def test_agreement_counts(tmp_path):
    def put(name, labels):
        p = tmp_path / name
        with p.open("w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=("pair_id", "label"))
            w.writeheader()
            w.writerows({"pair_id": k, "label": v} for k, v in labels.items())
        return p
    claude = put("c.csv", {"a": "supported", "b": "supported", "c": "not_supported", "d": "unclear", "e": "supported"})
    van = put("v.csv", {"a": "supported", "b": "not_supported", "c": "not_supported", "d": "", "z": "supported"})
    assert agreement(claude, van) == (2, 3)


def test_judge_pairs_reads_run_folder(tmp_path):
    write_run(tmp_path, "r1", [("Alpha is fast", "supported")])
    src = tmp_path / "l.csv"
    base = {f: "" for f in PAIR_FIELDS}
    rows = [base | {"pair_id": "p1", "run_id": "r1", "statement": "Alpha is fast", "index": "1", "label": "supported"},
            base | {"pair_id": "p2", "run_id": "r1", "statement": "Alpha is fast", "index": "7", "label": "not_supported"}]
    with src.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=PAIR_FIELDS)
        w.writeheader()
        w.writerows(rows)
    calls = []

    class GW:
        def ask(self, tier, messages, **kw):
            calls.append((tier, kw["private"]))
            return SimpleNamespace(content=json.dumps({"verdict": "supported", "quote": "Alpha is very fast indeed", "reason": "r"}), error="")
    before = sorted(p.name for p in (tmp_path / "r1").iterdir())
    out = judge_pairs(src, tmp_path, GW(), "referee")
    assert [r["referee_verdict"] for r in out] == ["supported", "not_supported"]
    assert calls == [("referee", True)]
    assert sorted(p.name for p in (tmp_path / "r1").iterdir()) == before
