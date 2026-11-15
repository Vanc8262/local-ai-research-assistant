"""The bench attempt ledger, the retest cap, and `eval --bench/--candidate/--split`."""
import csv
import os
from pathlib import Path

import pytest

from assistant import __main__ as cli
from assistant import ledger as ledger_module
from assistant.ledger import (FINAL_STATUSES, LEDGER_FIELDS, MAX_TEST_ATTEMPTS, AttemptFinal, RetestLimit,
                              check_attempt, finish_attempt, finish_attempts, read_ledger, record_attempt)
from assistant.manifest import sha256_file
from assistant.referee import PROMPT_HASH, PROMPT_VERSION

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


def test_record_attempt_adds_a_missing_final_newline(tmp_path):
    led = tmp_path / "l.csv"
    led.write_text(",".join(LEDGER_FIELDS), encoding="utf-8")   # hand-edited: no newline at the end
    record_attempt(led, _row("k1"))
    rows = read_ledger(led)
    assert len(rows) == 1 and rows[0]["candidate_key"] == "k1"


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
    """A repo (tmp_path/repo, standing in for REPO_ROOT) with the bench spec and the ledger committed,
    a dev and a test question file, GGUF files for the candidate and the referee, a fake git (clean
    tree at a fixed commit), and a fake run_eval that records what it was given."""
    repo = tmp_path / "repo"
    (repo / "evals").mkdir(parents=True)
    (repo / "results").mkdir()
    monkeypatch.setattr(cli, "REPO_ROOT", repo)
    runs = tmp_path / "runs"
    config = tmp_path / "assistant.toml"
    config.write_text(CONFIG.format(runs=runs.as_posix()), encoding="utf-8")
    for name in ("dev_a", "dev_b", "test"):
        (repo / "evals" / f"{name}.toml").write_text(f'[[question]]\nid = "{name}"\ntext = "q"\npublic = true\n',
                                                     encoding="utf-8")
    gemma, referee = tmp_path / "gemma.gguf", tmp_path / "referee.gguf"
    gemma.write_bytes(b"GGUF gemma")
    referee.write_bytes(b"GGUF referee")
    monkeypatch.setenv("GEMMA_GGUF", str(gemma))
    monkeypatch.setenv("REFEREE_GGUF", str(referee))
    ledger = repo / "results" / "bench_ledger.csv"
    header = ",".join(LEDGER_FIELDS) + "\n"
    ledger.write_text(header, encoding="utf-8")   # as committed: the header only
    state = {"rows": [{"outcome": "ok", "gate": "pass"}], "porcelain": "",
             "tracked": {"evals/bench.toml", "results/bench_ledger.csv"},
             "head": {"evals/bench.toml": "", "results/bench_ledger.csv": header}}   # files in HEAD

    def fake_git(*args):
        if args[0] == "status":
            return state["porcelain"]
        if args[0] == "ls-files":   # --error-unmatch: prints the path when tracked, else fails ("")
            return args[-1] if args[-1] in state["tracked"] else ""
        if args[0] == "rev-parse" and args[-1].startswith("HEAD:"):   # the blob id, or "" when not in HEAD
            return "b10b" if args[-1][5:] in state["head"] else ""
        if args[0] == "show":   # like _git, the output comes back stripped
            return state["head"].get(args[-1][5:], "").strip()
        return "c0ffee1234567890"
    monkeypatch.setattr(cli, "_git", fake_git)
    calls = []

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
    state["served"] = None   # what llama-swap reports serving: None means the file GEMMA_GGUF names
    state["served_calls"] = []

    def fake_served(base_url, model):
        state["served_calls"].append((base_url, model))
        if isinstance(state["served"], Exception):
            raise state["served"]
        return state["served"] or os.environ.get("GEMMA_GGUF", "")
    monkeypatch.setattr(cli, "served_model_path", fake_served)

    def spec(frozen: bool, test_files, path: Path):
        text = SPEC.read_text(encoding="utf-8")
        dev = ", ".join(f'"{(repo / "evals" / n).as_posix()}.toml"' for n in ("dev_a", "dev_b"))
        test = ", ".join(f'"{(repo / "evals" / n).as_posix()}.toml"' for n in test_files)
        text = text.replace('dev = ["evals/research_questions.toml", "evals/heldout_questions.toml"]',
                            f"dev = [{dev}]")
        text = text.replace('test = ["evals/bench_test_questions.toml"]', f"test = [{test}]")
        if frozen:
            text = text.replace("frozen = false", "frozen = true")
            text = text.replace('sha256 = ""', f'sha256 = "{sha256_file(referee)}"')
            text = text.replace('prompt_hash = ""', f'prompt_hash = "{PROMPT_HASH}"')
        path.write_text(text, encoding="utf-8")
        return path

    def go(*argv, frozen: bool = True, test_files=("test",), ledger_path=None, spec_path=None):
        path = spec(frozen, test_files, spec_path or repo / "evals" / "bench.toml")
        return cli.main(["--config", str(config), "eval", "--bench", str(path),
                         "--ledger", str(ledger_path or ledger), *argv])
    go.calls, go.ledger, go.state, go.gemma, go.repo, go.tmp = calls, ledger, state, gemma, repo, tmp_path
    go.header = header
    return go


def test_dev_split_writes_no_ledger_row(bench, monkeypatch):
    monkeypatch.delenv("GEMMA_GGUF")   # dev runs without the GGUF; its sha is then ""
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0
    assert bench.ledger.read_text(encoding="utf-8") == bench.header
    (call,) = bench.calls
    assert call["questions"] == ["dev_a", "dev_b"] and call["modes"] == ["local"] and call["runs"] == 1
    assert call["candidate_tier"] == "local" and call["settings"].research.tavily_fallback is False
    assert call["bench"]["version"] == 1 and call["bench"]["split"] == "dev"
    assert call["bench"]["candidate"] == "gemma-e4b" and call["bench"]["candidate_gguf_sha256"] == ""
    assert call["bench"]["candidate_key"].startswith("-c0ffee123-")
    assert call["bench"]["tree_clean"] is True and call["bench"]["ledger"] is None
    assert call["bench"]["question_files"] == {
        f"evals/{n}.toml": sha256_file(bench.repo / "evals" / f"{n}.toml") for n in ("dev_a", "dev_b")}
    assert call["bench"]["spec"] == {"path": "evals/bench.toml",
                                     "sha256": sha256_file(bench.repo / "evals" / "bench.toml")}


def test_dev_split_records_a_dirty_tree_without_refusing(bench):
    bench.state["porcelain"] = " M assistant/verify.py"
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0
    assert bench.calls[0]["bench"]["tree_clean"] is False


def test_dev_split_runs_unfrozen_with_a_hybrid_reference_row(bench):
    assert bench("--candidate", "gemma-e4b", "--split", "dev", "--mode", "hybrid", frozen=False) == 0
    assert bench.calls[0]["modes"] == ["local", "hybrid"]
    assert bench.calls[0]["bench"]["candidate_gguf_sha256"] == sha256_file(bench.gemma)


def test_test_split_refused_when_not_frozen(bench, capsys):
    assert bench("--candidate", "gemma-e4b", "--split", "test", frozen=False) == 2
    assert "frozen" in capsys.readouterr().out
    assert not bench.calls and read_ledger(bench.ledger) == []


def test_test_split_records_the_attempt_before_the_first_question(bench):
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 0
    (call,) = bench.calls
    (started,) = call["ledger_at_start"]
    assert started["status"] == "started" and started["split"] == "test" and started["bench_version"] == "1"
    assert started["gguf_sha256"] == sha256_file(bench.gemma) and started["git_commit"] == "c0ffee1234567890"
    assert started["candidate"] == "gemma-e4b" and started["candidate_key"] == call["bench"]["candidate_key"]
    assert started["eval_id"] == f"eval-{call['eval_id']}" and call["questions"] == ["test"]
    assert [r["status"] for r in read_ledger(bench.ledger)] == ["started"]   # the referee sets passed or missed
    assert call["bench"]["tree_clean"] is True and call["bench"]["ledger"] == "results/bench_ledger.csv"
    assert call["bench"]["question_files"] == {"evals/test.toml": sha256_file(bench.repo / "evals" / "test.toml")}


def test_a_changed_ledger_alone_does_not_count_as_a_dirty_tree(bench, capsys):
    """After one attempt the ledger is modified; the next candidate may still run."""
    bench.state["porcelain"] = " M results/bench_ledger.csv"
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 0
    assert bench.calls[0]["bench"]["tree_clean"] is True
    bench.state["porcelain"] = " M results/bench_ledger.csv\n M assistant/verify.py"
    capsys.readouterr()
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    out = capsys.readouterr().out
    assert "assistant/verify.py" in out and "results/bench_ledger.csv" not in out   # dirty, before the retest check


def test_test_split_refuses_a_ledger_outside_the_repo(bench, capsys):
    outside = bench.tmp / "elsewhere.csv"
    assert bench("--candidate", "gemma-e4b", "--split", "test", ledger_path=outside) == 2
    assert "results/bench_ledger.csv" in capsys.readouterr().out and not bench.calls and not outside.exists()


def test_test_split_refuses_a_ledger_other_than_the_default(bench, capsys):
    """One ledger per repo: a second ledger file would reset the count."""
    other = bench.repo / "results" / "other_ledger.csv"
    other.write_text(bench.header, encoding="utf-8")
    bench.state["tracked"].add("results/other_ledger.csv")
    bench.state["head"]["results/other_ledger.csv"] = bench.header
    assert bench("--candidate", "gemma-e4b", "--split", "test", ledger_path=other) == 2
    assert "results/bench_ledger.csv" in capsys.readouterr().out and not bench.calls
    assert bench("--candidate", "gemma-e4b", "--split", "dev", ledger_path=other) == 0   # dev ignores the ledger


def test_test_split_refuses_a_ledger_staged_but_not_committed(bench, capsys):
    del bench.state["head"]["results/bench_ledger.csv"]
    bench.state["porcelain"] = "A  results/bench_ledger.csv"
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    assert "HEAD" in capsys.readouterr().out and not bench.calls and read_ledger(bench.ledger) == []


def test_test_split_refuses_a_ledger_that_lost_committed_rows(bench, capsys):
    committed = bench.header + "1,gemma-e4b,k-old,g,abc,test,eval-old,missed,2026-11-15T00:00:00+00:00,\n"
    bench.state["head"]["results/bench_ledger.csv"] = committed
    bench.state["porcelain"] = " M results/bench_ledger.csv"
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2   # the working file holds the header only
    assert "committed" in capsys.readouterr().out and not bench.calls
    bench.ledger.write_text(committed.replace(",missed,", ",passed,"), encoding="utf-8")   # a row edited
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    bench.ledger.write_text(committed, encoding="utf-8")
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 0
    assert [r["eval_id"] for r in read_ledger(bench.ledger)][0] == "eval-old"


def test_a_crlf_checkout_of_the_committed_ledger_is_accepted(bench):
    """Windows checkouts may turn "\n" into "\r\n"; the rows are still the committed ones."""
    bench.ledger.write_bytes(bench.header.replace("\n", "\r\n").encode("utf-8"))
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 0


def test_test_split_refuses_an_untracked_ledger_in_the_repo(bench, capsys):
    bench.state["tracked"].discard("results/bench_ledger.csv")
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    assert "results/bench_ledger.csv is not committed" in capsys.readouterr().out and not bench.calls


def test_test_split_refuses_an_untracked_copy_of_the_spec(bench, capsys):
    copy = bench.repo / "evals" / "bench_copy.toml"
    assert bench("--candidate", "gemma-e4b", "--split", "test", spec_path=copy) == 2
    assert "bench_copy.toml" in capsys.readouterr().out and not bench.calls


def test_test_split_refuses_a_modified_spec(bench, capsys):
    bench.state["porcelain"] = " M evals/bench.toml"
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    assert "evals/bench.toml has uncommitted changes" in capsys.readouterr().out and not bench.calls


@pytest.mark.parametrize("flag", [["--no-tavily"], ["--replay-search", "snap"], ["--only", "test"],
                                  ["--verify-tier", "local"]])
def test_test_split_refuses_flags_that_change_the_run(bench, capsys, flag):
    assert bench("--candidate", "gemma-e4b", "--split", "test", *flag) == 2
    assert flag[0] in capsys.readouterr().out and not bench.calls


def test_referee_pin_is_checked_before_the_candidate_gguf_is_hashed(bench, monkeypatch):
    hashed = []
    monkeypatch.setattr(cli, "sha256_file", lambda path: hashed.append(Path(path).name) or "0" * 64)
    monkeypatch.delenv("REFEREE_GGUF")
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    assert "gemma.gguf" not in hashed


def test_dirty_paths_reads_porcelain_output():
    # _git strips its output, so the first line may have lost its leading space
    out = "M assistant/verify.py\nM  results/bench_ledger.csv\nR  old name.toml -> evals/new name.toml\n?? x"
    assert cli._dirty_paths(out) == ["assistant/verify.py", "results/bench_ledger.csv", "evals/new name.toml", "x"]
    assert cli._dirty_paths("") == []


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
    ("dirty", "assistant/verify.py"), ("no_gguf", "GEMMA_GGUF"), ("missing_gguf", "does not exist"),
    ("bad_referee", "referee"), ("missing_questions", "nope.toml")])
def test_test_split_refusals(bench, monkeypatch, capsys, change, needle):
    test_files = ("test",)
    if change == "dirty":
        bench.state["porcelain"] = " M assistant/verify.py"
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
    assert not bench.calls and read_ledger(bench.ledger) == []


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


def test_shipped_ledger_is_well_formed():
    """The committed ledger: the header is LEDGER_FIELDS, and every row has every field, a known status
    and a whole-number bench version (rows are added by test-split attempts, so it need not be empty)."""
    with (ROOT / "results" / "bench_ledger.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == list(LEDGER_FIELDS)
    for row in rows[1:]:
        assert len(row) == len(LEDGER_FIELDS), row
        record = dict(zip(LEDGER_FIELDS, row))
        assert record["status"] in ("started", *FINAL_STATUSES), row
        assert record["bench_version"].isdigit() and record["split"] == "test", row
        assert record["eval_id"].startswith("eval-") and record["candidate_key"] and record["started_utc"], row


def test_bench_eval_probes_the_candidate_tier_for_gpu_offload(bench, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "fetch_llama_swap_log",
                        lambda base_url: seen.append(base_url) or "load_tensors: offloaded 40/49 layers to GPU")
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0
    probe = bench.calls[0]["offload_probe"]
    assert probe() == (40, 49) and seen == ["http://127.0.0.1:1234/v1"]


def test_a_final_status_never_changes(tmp_path):
    """Only "started" changes. passed, missed and error are final: the same status again is a no-op,
    another one is refused, so a miss can't be turned into a pass (nor an error into either)."""
    led = tmp_path / "l.csv"
    record_attempt(led, _row("k1", eval_id="eval-a"))
    record_attempt(led, _row("k2", eval_id="eval-b"))
    finish_attempt(led, "eval-a", "missed")
    finish_attempt(led, "eval-b", "error")
    before = led.read_text(encoding="utf-8")
    finish_attempt(led, "eval-a", "missed")   # the same again: nothing changes
    assert led.read_text(encoding="utf-8") == before
    with pytest.raises(AttemptFinal, match="eval-a.*missed.*passed"):
        finish_attempt(led, "eval-a", "passed")
    with pytest.raises(AttemptFinal, match="eval-b.*error"):
        finish_attempt(led, "eval-b", "passed")
    assert led.read_text(encoding="utf-8") == before


def test_finish_attempts_writes_all_or_nothing(tmp_path):
    led = tmp_path / "l.csv"
    for name in ("eval-a", "eval-b", "eval-c"):
        record_attempt(led, _row(name, eval_id=name))
    finish_attempt(led, "eval-c", "passed")
    before = led.read_text(encoding="utf-8")
    with pytest.raises(AttemptFinal, match="eval-c"):
        finish_attempts(led, {"eval-a": "passed", "eval-c": "missed"})
    assert led.read_text(encoding="utf-8") == before   # eval-a was not marked either
    finish_attempts(led, {"eval-a": "passed", "eval-b": "missed", "eval-c": "passed"})
    assert [r["status"] for r in read_ledger(led)] == ["passed", "missed", "passed"]


def test_ledger_writes_are_atomic(tmp_path, monkeypatch):
    """The ledger is written to a temporary file beside it and moved into place, so a file held open
    elsewhere (Excel on Windows) leaves the ledger as it was, with no temporary file behind."""
    led = tmp_path / "l.csv"
    record_attempt(led, _row("k1", eval_id="eval-a"))
    before = led.read_text(encoding="utf-8")

    def locked(src, dst):
        raise PermissionError(13, "The process cannot access the file", str(dst))
    monkeypatch.setattr(ledger_module.os, "replace", locked)
    with pytest.raises(PermissionError):
        record_attempt(led, _row("k2", eval_id="eval-b"))
    with pytest.raises(PermissionError):
        finish_attempt(led, "eval-a", "passed")
    assert led.read_text(encoding="utf-8") == before and [p.name for p in tmp_path.iterdir()] == ["l.csv"]


def test_check_attempt_compares_hashes_ignoring_case_and_spaces(tmp_path):
    """PowerShell's Get-FileHash prints upper case; a hand-copied hash must still count."""
    led = tmp_path / "l.csv"
    for n in range(MAX_TEST_ATTEMPTS):
        record_attempt(led, _row(f"k{n}", gguf=" ABCDEF ", eval_id=f"eval-{n}"))
    with pytest.raises(RetestLimit):
        check_attempt(led, 1, "abcdef", "k-new")


def test_a_failed_hybrid_reference_row_does_not_mark_the_attempt_error(bench, monkeypatch):
    """Only the candidate's own (local) rows decide "error"; hybrid is Gemini's reference row."""
    bench.state["rows"] = [{"outcome": "ok", "gate": "pass", "mode": "local"},
                           {"outcome": "no_report", "gate": "fail", "mode": "hybrid"}]
    bench("--candidate", "gemma-e4b", "--split", "test", "--mode", "hybrid")
    assert [r["status"] for r in read_ledger(bench.ledger)] == ["started"]
    other = bench.gemma.with_name("gemma2.gguf")   # another candidate, so the retest cap allows it
    other.write_bytes(b"GGUF gemma two")
    monkeypatch.setenv("GEMMA_GGUF", str(other))
    bench.state["head"]["results/bench_ledger.csv"] = bench.ledger.read_text(encoding="utf-8")
    bench.state["rows"] = [{"outcome": "no_report", "mode": "local"}, {"outcome": "ok", "mode": "hybrid"}]
    bench("--candidate", "gemma-e4b", "--split", "test", "--mode", "hybrid")
    assert [r["status"] for r in read_ledger(bench.ledger)] == ["started", "error"]


def _locked(monkeypatch):
    def locked(src, dst):
        raise PermissionError(13, "The process cannot access the file", str(dst))
    monkeypatch.setattr(ledger_module.os, "replace", locked)


def test_eval_says_how_to_recover_when_the_ledger_is_locked_at_the_start(bench, monkeypatch, capsys):
    _locked(monkeypatch)
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 1
    out = capsys.readouterr().out
    assert "Close results/bench_ledger.csv (e.g. in Excel) and run:" in out
    assert "python -m assistant --config" in out and "eval --bench" in out and "--split test" in out
    assert not bench.calls and bench.ledger.read_text(encoding="utf-8") == bench.header


def test_eval_marks_error_or_says_how_when_the_ledger_is_locked(bench, monkeypatch, capsys):
    bench.state["rows"] = [{"outcome": "no_report", "mode": "local"}]
    real = ledger_module.os.replace
    calls = []

    def second_write_locked(src, dst):
        calls.append(dst)
        if len(calls) > 1:
            raise PermissionError(13, "locked", str(dst))
        return real(src, dst)
    monkeypatch.setattr(ledger_module.os, "replace", second_write_locked)
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 1
    out = capsys.readouterr().out
    assert "Close results/bench_ledger.csv (e.g. in Excel) and run:" in out and "'error'" in out
    assert [r["status"] for r in read_ledger(bench.ledger)] == ["started"]


def test_eval_never_masks_its_own_exception_with_a_ledger_error(bench, monkeypatch, capsys):
    bench.state["rows"] = RuntimeError("model server died")
    real = ledger_module.os.replace
    calls = []

    def second_write_locked(src, dst):
        calls.append(dst)
        if len(calls) > 1:
            raise PermissionError(13, "locked", str(dst))
        return real(src, dst)
    monkeypatch.setattr(ledger_module.os, "replace", second_write_locked)
    with pytest.raises(RuntimeError, match="model server died"):
        bench("--candidate", "gemma-e4b", "--split", "test")
    assert "Close results/bench_ledger.csv (e.g. in Excel) and run:" in capsys.readouterr().out


def test_bench_manifest_records_the_referee_prompt(bench):
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0
    recorded = bench.calls[0]["bench"]
    assert recorded["referee_prompt_version"] == PROMPT_VERSION and recorded["referee_prompt_hash"] == PROMPT_HASH


def test_test_split_refuses_another_referee_prompt(bench, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PROMPT_HASH", "f" * 64)   # this code's prompt changed after the freeze
    monkeypatch.setattr("assistant.referee.PROMPT_HASH", "f" * 64)
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    out = capsys.readouterr().out
    assert "f" * 64 in out and PROMPT_HASH in out and not bench.calls and read_ledger(bench.ledger) == []
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0   # dev doesn't judge: no check


def test_test_split_checks_the_served_candidate_file(bench, capsys):
    other = bench.tmp / "other.gguf"
    other.write_bytes(b"GGUF other")
    bench.state["served"] = str(other)
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    out = capsys.readouterr().out
    assert "other.gguf" in out and "gemma.gguf" in out and not bench.calls and read_ledger(bench.ledger) == []
    assert bench.state["served_calls"] == [("http://127.0.0.1:1234/v1", "gemma")]


def test_test_split_fails_closed_when_the_served_file_is_unknown(bench, capsys):
    from assistant.bench_spec import BenchSpecError
    bench.state["served"] = BenchSpecError("could not read the served model file: ConnectError: refused")
    assert bench("--candidate", "gemma-e4b", "--split", "test") == 2
    assert "refused" in capsys.readouterr().out and not bench.calls and read_ledger(bench.ledger) == []


def test_dev_split_only_warns_about_the_served_file(bench, capsys):
    from assistant.bench_spec import BenchSpecError
    bench.state["served"] = BenchSpecError("could not read the served model file: ConnectError: refused")
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0
    assert "Warning" in capsys.readouterr().out and len(bench.calls) == 1
    other = bench.tmp / "other.gguf"
    other.write_bytes(b"GGUF other")
    bench.state["served"] = str(other)
    assert bench("--candidate", "gemma-e4b", "--split", "dev") == 0
    assert "other.gguf" in capsys.readouterr().out and len(bench.calls) == 2
