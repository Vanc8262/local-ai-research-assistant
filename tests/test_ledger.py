"""The bench attempt ledger, the retest cap, and `eval --bench/--candidate/--split`."""
import csv
from pathlib import Path

import pytest

from assistant import __main__ as cli
from assistant.ledger import (LEDGER_FIELDS, MAX_TEST_ATTEMPTS, RetestLimit, check_attempt, finish_attempt,
                              read_ledger, record_attempt)
from assistant.manifest import sha256_file

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "evals" / "bench_v1.toml"
CONFIG = """
[assistant]
default_tier = "local"
runs_dir = "{runs}"

[tiers.local]
kind = "local"
base_url = "http://127.0.0.1:1234/v1"
model = "gemma"
"""


def _row(key: str, gguf: str = "g", version: int = 1, eval_id: str = "eval-1") -> dict:
    return {"bench_version": version, "candidate": "gemma-e4b", "candidate_key": key, "gguf_sha256": gguf,
            "git_commit": "abc", "split": "test", "eval_id": eval_id, "status": "started",
            "started_utc": "2026-11-15T00:00:00+00:00", "note": ""}


def test_ledger_counts_started_attempts(tmp_path):
    led = tmp_path / "l.csv"
    record_attempt(led, _row("k1"))
    with pytest.raises(RetestLimit, match="k1"):
        check_attempt(led, 1, "g", "k1")
    assert read_ledger(led)[0]["status"] == "started"
    assert led.read_text(encoding="utf-8").splitlines()[0] == ",".join(LEDGER_FIELDS)


def test_two_retests_then_refused(tmp_path):
    led = tmp_path / "l.csv"
    for n, key in enumerate(("k1", "k2", "k3")):
        check_attempt(led, 1, "g", key)   # the first attempt and two retests are allowed
        record_attempt(led, _row(key, eval_id=f"eval-{n}"))
    assert MAX_TEST_ATTEMPTS == 3
    with pytest.raises(RetestLimit, match="3"):
        check_attempt(led, 1, "g", "k4")
    check_attempt(led, 2, "g", "k4")   # a new bench version starts a new count


def test_other_model_unaffected(tmp_path):
    led = tmp_path / "l.csv"
    for n, key in enumerate(("k1", "k2", "k3")):
        record_attempt(led, _row(key, eval_id=f"eval-{n}"))
    check_attempt(led, 1, "h", "x")


def test_missing_or_header_only_ledger_allows_an_attempt(tmp_path):
    check_attempt(tmp_path / "none.csv", 1, "g", "k1")
    shipped = ROOT / "results" / "bench_ledger.csv"
    assert shipped.read_text(encoding="utf-8").strip() == ",".join(LEDGER_FIELDS)
    led = tmp_path / "l.csv"
    led.write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
    check_attempt(led, 1, "g", "k1")
    record_attempt(led, _row("k1"))
    assert len(read_ledger(led)) == 1


def test_dev_rows_do_not_count(tmp_path):
    led = tmp_path / "l.csv"
    record_attempt(led, {**_row("k1"), "split": "dev"})
    check_attempt(led, 1, "g", "k1")


def test_finish_attempt_sets_the_status_of_that_eval_only(tmp_path):
    led = tmp_path / "l.csv"
    record_attempt(led, _row("k1", eval_id="eval-a"))
    record_attempt(led, _row("k2", eval_id="eval-b"))
    finish_attempt(led, "eval-b", "error")
    assert [r["status"] for r in read_ledger(led)] == ["started", "error"]
    with pytest.raises(ValueError):
        finish_attempt(led, "eval-a", "done")
    with pytest.raises(ValueError, match="eval-z"):
        finish_attempt(led, "eval-z", "missed")


# --- the CLI -------------------------------------------------------------------------------

@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A config, a dev and a test question file, GGUF files for the candidate and the referee,
    a clean git tree at a fixed commit, and a fake run_eval that records what it was given."""
    runs = tmp_path / "runs"
    config = tmp_path / "assistant.toml"
    config.write_text(CONFIG.format(runs=runs.as_posix()), encoding="utf-8")
    for name in ("dev_a", "dev_b", "test"):
        (tmp_path / f"{name}.toml").write_text(f'[[question]]\nid = "{name}"\ntext = "q"\npublic = true\n',
                                               encoding="utf-8")
    gemma, referee = tmp_path / "gemma.gguf", tmp_path / "referee.gguf"
    gemma.write_bytes(b"GGUF gemma")
    referee.write_bytes(b"GGUF referee")
    monkeypatch.setenv("GEMMA_GGUF", str(gemma))
    monkeypatch.setenv("REFEREE_GGUF", str(referee))
    monkeypatch.setattr(cli, "_git", lambda *args: "" if args[0] == "status" else "c0ffee1234567890")
    calls, ledger = [], tmp_path / "ledger.csv"
    state = {"rows": [{"outcome": "ok", "gate": "pass"}]}

    def fake_run_eval(settings, questions, modes, **kwargs):
        out = runs / f"eval-{kwargs.get('eval_id') or 'x'}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.md").write_text("# summary\n", encoding="utf-8")
        calls.append({"settings": settings, "questions": [q.id for q in questions], "modes": modes,
                      "ledger_at_start": read_ledger(ledger), **kwargs})
        if isinstance(state["rows"], Exception):
            raise state["rows"]
        return out, state["rows"]
    monkeypatch.setattr(cli, "run_eval", fake_run_eval)

    def spec(frozen: bool = True, test_files=("test",)):
        text = SPEC.read_text(encoding="utf-8")
        dev = ", ".join(f'"{(tmp_path / n).as_posix()}.toml"' for n in ("dev_a", "dev_b"))
        test = ", ".join(f'"{(tmp_path / n).as_posix()}.toml"' for n in test_files)
        text = text.replace('dev = ["evals/research_questions.toml", "evals/heldout_questions.toml"]',
                            f"dev = [{dev}]")
        text = text.replace('test = ["evals/bench_test_questions.toml"]', f"test = [{test}]")
        if frozen:
            text = text.replace("frozen = false", "frozen = true")
            text = text.replace('sha256 = ""', f'sha256 = "{sha256_file(referee)}"')
        path = tmp_path / "bench.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def go(*argv, frozen: bool = True, test_files=("test",)):
        return cli.main(["--config", str(config), "eval", "--bench", str(spec(frozen, test_files)),
                         "--ledger", str(ledger), *argv])
    go.calls, go.ledger, go.state, go.gemma = calls, ledger, state, gemma
    return go


def test_dev_split_writes_no_ledger_row(bench, monkeypatch):
    monkeypatch.delenv("GEMMA_GGUF")   # dev runs without the GGUF; its sha is then ""
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0
    assert not bench.ledger.exists()
    (call,) = bench.calls
    assert call["questions"] == ["dev_a", "dev_b"] and call["modes"] == ["local"] and call["runs"] == 1
    assert call["candidate_tier"] == "local" and call["settings"].research.tavily_fallback is False
    assert call["bench"]["version"] == 1 and call["bench"]["split"] == "dev"
    assert call["bench"]["candidate"] == "gemma-e4b" and call["bench"]["candidate_gguf_sha256"] == ""
    assert call["bench"]["candidate_key"].startswith("-c0ffee123-")


def test_dev_split_runs_unfrozen_with_a_hybrid_reference_row(bench):
    assert bench("--candidate", "gemma-e4b", "--split", "dev", "--mode", "hybrid", frozen=False) == 0
    assert bench.calls[0]["modes"] == ["local", "hybrid"]
    assert bench.calls[0]["bench"]["candidate_gguf_sha256"] == sha256_file(bench.gemma)


def test_test_split_refused_when_not_frozen(bench, capsys):
    assert bench("--candidate", "gemma-e4b", "--split", "test", frozen=False) == 2
    assert "frozen" in capsys.readouterr().out
    assert not bench.calls and not bench.ledger.exists()


def test_test_split_records_the_attempt_before_the_first_question(bench):
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 0
    (call,) = bench.calls
    (started,) = call["ledger_at_start"]
    assert started["status"] == "started" and started["split"] == "test" and started["bench_version"] == "1"
    assert started["gguf_sha256"] == sha256_file(bench.gemma) and started["git_commit"] == "c0ffee1234567890"
    assert started["candidate"] == "gemma-e4b" and started["candidate_key"] == call["bench"]["candidate_key"]
    assert started["eval_id"] == f"eval-{call['eval_id']}" and call["questions"] == ["test"]
    assert [r["status"] for r in read_ledger(bench.ledger)] == ["started"]   # the referee sets passed or missed


def test_a_test_run_with_a_failed_row_is_marked_error(bench):
    bench.state["rows"] = [{"outcome": "ok", "gate": "pass"}, {"outcome": "no_report"}]
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 1
    assert [r["status"] for r in read_ledger(bench.ledger)] == ["error"]


def test_a_test_run_that_raises_is_marked_error(bench):
    bench.state["rows"] = RuntimeError("model server died")
    with pytest.raises(RuntimeError):
        bench("--candidate", "gemma-e4b", "--split", "test")
    assert [r["status"] for r in read_ledger(bench.ledger)] == ["error"]


def test_the_same_candidate_never_runs_the_test_split_twice(bench, capsys):
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 0
    capsys.readouterr()
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    assert "already" in capsys.readouterr().out and len(bench.calls) == 1


@pytest.mark.parametrize("change, needle", [
    ("dirty", "uncommitted"), ("no_gguf", "GEMMA_GGUF"), ("missing_gguf", "does not exist"),
    ("bad_referee", "referee"), ("missing_questions", "nope.toml")])
def test_test_split_refusals(bench, monkeypatch, capsys, change, needle):
    test_files = ("test",)
    if change == "dirty":
        monkeypatch.setattr(cli, "_git", lambda *args: " M assistant/verify.py" if args[0] == "status" else "c0ffee1")
    elif change == "no_gguf":
        monkeypatch.delenv("GEMMA_GGUF")
    elif change == "missing_gguf":
        monkeypatch.setenv("GEMMA_GGUF", str(bench.gemma.with_name("gone.gguf")))
    elif change == "bad_referee":
        monkeypatch.delenv("REFEREE_GGUF")
    else:
        test_files = ("test", "nope")
    assert bench("--candidate", "gemma-e4b", "--split", "test", test_files=test_files) == 2
    assert needle in capsys.readouterr().out
    assert not bench.calls and not bench.ledger.exists()


@pytest.mark.parametrize("argv, needle", [
    (["--candidate", "nobody", "--split", "dev"], "nobody"),
    (["--candidate", "qwen-9b", "--split", "dev"], "local-backup"),   # not a tier in this config
    (["--candidate", "gemma-e4b", "--split", "dev", "--questions", "x.toml"], "--questions"),
    (["--candidate", "gemma-e4b", "--split", "dev", "--runs", "3"], "--runs"),
    (["--candidate", "gemma-e4b", "--split", "dev", "--mode", "codex"], "codex"),
    (["--split", "dev"], "--candidate"),
])
def test_bench_flag_errors_exit_2(bench, capsys, argv, needle):
    assert bench(*argv) == 2
    assert needle in capsys.readouterr().out and not bench.calls


def test_candidate_without_bench_exits_2(tmp_path, capsys):
    config = tmp_path / "assistant.toml"
    config.write_text(CONFIG.format(runs=(tmp_path / "runs").as_posix()), encoding="utf-8")
    assert cli.main(["--config", str(config), "eval", "--candidate", "gemma-e4b"]) == 2
    assert "--bench" in capsys.readouterr().out


def test_shipped_ledger_is_header_only():
    with (ROOT / "results" / "bench_ledger.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows == [list(LEDGER_FIELDS)]
