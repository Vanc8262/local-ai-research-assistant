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


def test_judge_pairs_skips_unlabelled_normalises_and_rejects_bad_labels(tmp_path):
    write_run(tmp_path, "r1", [("Alpha is fast", "supported")])
    base = {f: "" for f in PAIR_FIELDS} | {"run_id": "r1", "statement": "Alpha is fast", "index": "1"}

    def put(labels):
        src = tmp_path / "l.csv"
        with src.open("w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=PAIR_FIELDS)
            w.writeheader()
            w.writerows(base | {"pair_id": f"p{i}", "label": lab} for i, lab in enumerate(labels))
        return src
    calls = []

    class GW:
        def ask(self, tier, messages, **kw):
            calls.append(1)
            return SimpleNamespace(content="", error="boom")
    out = judge_pairs(put([" Supported ", "", "unclear", "NOT_SUPPORTED"]), tmp_path, GW(), "referee")
    assert [r["label"] for r in out] == ["supported", "not_supported"]
    assert all(r["referee_error"] for r in out) and recall(out)["referee_errors"] == 2
    assert len(calls) == 4   # two rows, each asked twice (retry)
    with pytest.raises(ValueError, match="p1"):
        judge_pairs(put(["supported", "suported"]), tmp_path, GW(), "referee")


# --- the CLI: referee calibrate ------------------------------------------------------------------

from pathlib import Path

from assistant import __main__ as cli
from assistant import calibration
from assistant.bench_spec import BenchSpecError

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "evals" / "bench_v1.toml"
CONFIG = """
[assistant]
default_tier = "local"
runs_dir = "{runs}"

[tiers.local]
kind = "local"
base_url = "http://127.0.0.1:1234/v1"
model = "gemma"

[tiers.referee]
kind = "local"
base_url = "http://127.0.0.1:1234/v1"
model = "referee"
"""


@pytest.fixture
def calibrate(tmp_path, monkeypatch):
    """A stand-in repo root, a cwd elsewhere, a referee GGUF, a fake served-file check and a fake
    judge_pairs that labels every pair as the referee would."""
    repo = tmp_path / "repo"
    (repo / "results").mkdir(parents=True)
    monkeypatch.setattr(cli, "REPO_ROOT", repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    gguf = tmp_path / "referee.gguf"
    gguf.write_bytes(b"GGUF referee")
    monkeypatch.setenv("REFEREE_GGUF", str(gguf))
    state = {"served": str(gguf), "judged": 0}

    def served(base_url, model):
        if isinstance(state["served"], Exception):
            raise state["served"]
        return state["served"]
    monkeypatch.setattr(cli, "served_model_path", served)

    def fake_judge(labelled, runs_dir, gateway, tier):
        state["judged"] += 1
        return [{"label": "supported", "referee_verdict": "supported", "referee_error": ""},
                {"label": "not_supported", "referee_verdict": "not_supported", "referee_error": ""}]
    monkeypatch.setattr(calibration, "judge_pairs", fake_judge)
    config = tmp_path / "assistant.toml"
    config.write_text(CONFIG.format(runs=(tmp_path / "runs").as_posix()), encoding="utf-8")
    labelled = tmp_path / "labelled.csv"
    labelled.write_text(",".join(PAIR_FIELDS) + "\n", encoding="utf-8")

    def go(*words, bench=BENCH):
        return cli.main(["--config", str(config), "referee", "calibrate", *words, "--bench", str(bench)])
    go.repo, go.state, go.labelled, go.elsewhere, go.tmp = repo, state, labelled, elsewhere, tmp_path
    return go


def test_calibrate_run_writes_the_report_the_scorecard_reads(calibrate):
    assert calibrate("run", str(calibrate.labelled)) == 0
    report = calibrate.repo / "results" / "calibration_v1_referee.md"   # what cmd_scorecard reads
    assert report.is_file() and "PASS" in report.read_text(encoding="utf-8")
    assert not (calibrate.elsewhere / "results").exists()


def test_calibrate_report_name_follows_the_spec_version(calibrate):
    spec = calibrate.tmp / "bench_v2.toml"
    spec.write_text(BENCH.read_text(encoding="utf-8").replace("version = 1", "version = 2", 1), encoding="utf-8")
    assert calibrate("run", str(calibrate.labelled), bench=spec) == 0
    assert (calibrate.repo / "results" / "calibration_v2_referee.md").is_file()


def test_calibrate_sample_and_spot_default_to_the_repo_results(calibrate, monkeypatch):
    (calibrate.repo / "evals").mkdir()
    for name in ("research_questions", "heldout_questions"):   # the spec's dev files, from the repo root
        (calibrate.repo / "evals" / f"{name}.toml").write_text(f'[[question]]\nid = "{name}"\ntext = "q"\n',
                                                               encoding="utf-8")
    seen = []
    monkeypatch.setattr(calibration, "sample_pairs", lambda dirs, out, ids, **kw: seen.append(out) or [{}])
    monkeypatch.setattr(calibration, "spot_check", lambda labelled, out: seen.append(out) or [{}])
    assert calibrate("sample", "runs/eval-x") == 0
    assert calibrate("spot", str(calibrate.labelled)) == 0
    assert seen == [calibrate.repo / "results" / "calibration_v1.csv",
                    calibrate.repo / "results" / "calibration_v1_spot.csv"]


def test_calibrate_run_refuses_another_prompt(calibrate, capsys):
    spec = calibrate.tmp / "bench.toml"
    spec.write_text(BENCH.read_text(encoding="utf-8").replace("prompt_version = 1", "prompt_version = 2"),
                    encoding="utf-8")
    assert calibrate("run", str(calibrate.labelled), bench=spec) == 1
    assert "prompt version" in capsys.readouterr().out and calibrate.state["judged"] == 0


def test_calibrate_run_refuses_when_llama_swap_serves_another_file(calibrate, capsys):
    other = calibrate.tmp / "other.gguf"
    other.write_bytes(b"other")
    calibrate.state["served"] = str(other)
    assert calibrate("run", str(calibrate.labelled)) == 1
    assert "other.gguf" in capsys.readouterr().out and calibrate.state["judged"] == 0
    calibrate.state["served"] = BenchSpecError("could not read the served model file: ConnectError: refused")
    assert calibrate("run", str(calibrate.labelled)) == 1
    assert "refused" in capsys.readouterr().out and calibrate.state["judged"] == 0


def test_judge_pairs_marks_a_final_failure(tmp_path):
    write_run(tmp_path, "r1", [("Alpha is fast", "supported")])
    src = tmp_path / "l.csv"
    base = {f: "" for f in PAIR_FIELDS}
    with src.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=PAIR_FIELDS)
        w.writeheader()
        w.writerow(base | {"pair_id": "p1", "run_id": "r1", "statement": "Alpha is fast", "index": "1",
                           "label": "supported"})

    class GW:
        def ask(self, tier, messages, **kw):
            return SimpleNamespace(content="no verdict here", error="")
    (row,) = judge_pairs(src, tmp_path, GW(), "referee")
    assert row["referee_verdict"] == "unclear" and row["referee_failed"] is True and row["referee_error"]
