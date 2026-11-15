"""Referee scoring of a saved run folder, with a scripted fake gateway (no model, no network)."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from assistant.bench_spec import REFEREE_CAVEAT, BenchSpecError, load_bench
from assistant.config import Settings, Tier
from assistant.evaluate import Question
from assistant.manifest import sha256_file
from assistant.referee import (PROMPT_HASH, PROMPT_VERSION, Judgement, check_prompt, judge, prompt_hash, score_eval,
                               score_run, statement_verdict, statements)

PAGE = ("BM25 uses term frequency to rank documents. It also applies saturation so that repeated "
        "terms add less and less to the score of a document.")


class FakeGateway:
    def __init__(self, replies):
        self.replies, self.calls, self.kwargs = list(replies), 0, []

    def ask(self, tier, messages, **kwargs):
        self.kwargs.append(kwargs)
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, SimpleNamespace):
            return reply
        return SimpleNamespace(content=reply, error="")


def verdict(v, quote="", reason="r"):
    return "Thinking about {braces}.\n" + json.dumps({"verdict": v, "quote": quote, "reason": reason})


def make_run(tmp_path, report, n_sources=1):
    run = tmp_path / "run"
    (run / "pages").mkdir(parents=True)
    (run / "report.md").write_text(report, encoding="utf-8")
    sources = [{"index": i, "title": f"S{i}", "url": f"https://a/{i}", "snippet": "a snippet"}
               for i in range(1, n_sources + 1)]
    (run / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    (run / "pages" / "1.txt").write_text(PAGE, encoding="utf-8")
    return run


def q(expect=None):
    return Question(id="q", text="How does BM25 work?", expect=expect)


def test_statements_are_verbatim_spans():
    s = statements("# Q\n\nBM25 uses term frequency [1]. It saturates [1].\n\n## Sources\n[1] x https://a")
    assert [x.raw.strip() for x in s] == ["BM25 uses term frequency [1].", "It saturates [1]."]


def test_statements_merge_duplicates():
    s = statements("# Q\n\nBM25 uses term frequency [1].\n\nBM25 uses term frequency [2].\n")
    assert len(s) == 1 and s[0].citations == ["1", "2"]


def test_supported_with_matching_quote(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1].\n")
    gw = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents")])
    j = judge("BM25 uses term frequency", "1", {"index": 1, "snippet": "s"}, PAGE, gw, "referee")
    assert j.verdict == "supported"
    kw = gw.kwargs[0]
    assert kw["temperature"] == 0 and kw["max_tokens"] == 4000 and kw["purpose"] == "referee"
    assert kw["private"] is True
    assert kw["extra_body"] == {"seed": 20261115, "chat_template_kwargs": {"enable_thinking": True}}


def test_quote_not_in_source_becomes_unclear():
    gw = FakeGateway([verdict("supported", "something never written in the page at all")])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee")
    assert j.verdict == "unclear" and j.reason.startswith("quote not in source: ") and j.referee_failed
    assert not j.referee_error


def test_referee_dangling_citation_is_not_supported(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [7].\n")
    gw = FakeGateway([])
    summary = score_run(run, q(), gw, "referee")
    assert summary["not_supported"] == 1 and gw.calls == 0
    data = json.loads((run / "referee.json").read_text(encoding="utf-8"))
    assert data["judgements"][0]["reason"] == "no such source"


def test_uncited_counts_as_not_supported_without_a_call(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency to rank documents every time.\n")
    gw = FakeGateway([])
    summary = score_run(run, q(), gw, "referee")
    assert summary["uncited"] == 1 and summary["not_supported"] == 1 and summary["cited"] == 0
    assert gw.calls == 0


def test_referee_bad_reply_becomes_a_final_unclear():
    """A reply that can't be read after the retry is deterministic: rerunning gives the same reply,
    so it is a final "unclear" flagged referee_failed, not an unjudged statement."""
    gw = FakeGateway(["nope", "still nope"])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee")
    assert j.verdict == "unclear" and j.referee_failed and not j.referee_error and gw.calls == 2
    assert j.reason.startswith("reply was not a verdict")


def test_a_client_error_reply_is_a_final_failure():
    err = SimpleNamespace(content="", error="HTTP 400: the prompt is longer than the context")
    gw = FakeGateway([err, err])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee")
    assert j.verdict == "unclear" and j.referee_failed and "HTTP 400" in j.reason and not j.referee_error


def test_verdict_outside_labels_is_a_final_failure():
    gw = FakeGateway([verdict("maybe"), verdict("maybe")])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee")
    assert j.verdict == "unclear" and j.referee_failed and not j.referee_error


@pytest.mark.parametrize("error", [
    "ConnectError: [WinError 10061] No connection could be made",
    "ReadTimeout: timed out",
    "RemoteProtocolError: Server disconnected without sending a response.",
    "HTTP 502: bad gateway",
    "HTTP 503: {\"error\":{\"code\":503,\"message\":\"Loading model\"}}",
    json.dumps({"code": 500, "message": "slot unavailable", "type": "server_error"}),
    "model not ready after reload",
])
def test_transient_errors_leave_the_statement_unjudged(error):
    """Connection errors, timeouts, 5xx and "not ready" are judged again on a rerun."""
    err = SimpleNamespace(content="", error=error)
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, FakeGateway([err, err]), "referee")
    assert j.verdict == "unclear" and j.referee_error and not j.referee_failed


def test_a_transient_error_then_a_bad_reply_is_still_unjudged():
    err = SimpleNamespace(content="", error="ReadTimeout: timed out")
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, FakeGateway([err, "nope"]), "referee")
    assert j.referee_error and not j.referee_failed
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE,
              FakeGateway([err, verdict("not_supported")]), "referee")
    assert j.verdict == "not_supported" and not j.referee_error and not j.referee_failed


def test_statement_verdict_order():
    mk = lambda v: Judgement("s", "1", v)
    assert statement_verdict([mk("unclear"), mk("supported")]) == "supported"
    assert statement_verdict([mk("unclear"), mk("not_supported")]) == "not_supported"
    assert statement_verdict([mk("unclear")]) == "unclear"


def test_facts_right_ignores_unsupported_sentences(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1]. It has saturation [1].\n")
    gw = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents"),
                      verdict("not_supported")])
    summary = score_run(run, q([["term frequency"], ["saturation"]]), gw, "referee")
    assert summary["facts_right"] == 0.5


def test_facts_right_none_without_expect(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1].\n")
    summary = score_run(run, q(), FakeGateway([verdict("unclear")]), "referee")
    assert summary["facts_right"] is None


def test_errors_through_denominator_includes_unclear(tmp_path):
    report = ("# Q\n\nBM25 uses term frequency to rank [1].\n\nIt applies saturation to scores [1].\n\n"
              "Length normalisation matters a lot here [1].\n\nThe constant k1 is tuned per corpus [1].\n")
    run = make_run(tmp_path, report)
    gw = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents"),
                      verdict("not_supported"), verdict("unclear"), verdict("unclear")])
    summary = score_run(run, q(), gw, "referee")
    assert summary["statements"] == 4 and summary["errors_through"] == 0.25
    assert summary["supported"] == 1 and summary["unclear"] == 2


def test_referee_reads_no_network(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(httpx, "get", boom)
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1].\n")
    score_run(run, q(), FakeGateway([verdict("unclear")]), "referee")


def test_referee_json_written(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1].\n\nIt applies saturation to scores [1].\n")
    score_run(run, q(), FakeGateway([verdict("unclear"), verdict("unclear")]), "referee")
    data = json.loads((run / "referee.json").read_text(encoding="utf-8"))
    assert data["summary"]["prompt_version"] == 1 and data["caveat"] == REFEREE_CAVEAT
    assert data["summary"]["prompt_hash"] == PROMPT_HASH
    assert len(data["judgements"]) == 2 and data["statements"][0]["verdict"] == "unclear"


def test_privacy_error_propagates(tmp_path):
    import pytest
    from assistant.gateway import PrivacyError

    class Raising:
        def ask(self, *a, **k):
            raise PrivacyError("no cloud")
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1].\n")
    with pytest.raises(PrivacyError):
        score_run(run, q(), Raising(), "referee")


def test_complete_flag(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1].\n")
    down = SimpleNamespace(content="", error="ConnectError: refused")
    assert score_run(run, q(), FakeGateway([down, down]), "referee")["complete"] is False
    failed = score_run(run, q(), FakeGateway(["nope", ""]), "referee")
    assert failed["complete"] is True and failed["referee_failed"] == 1 and failed["referee_errors"] == 0
    assert score_run(run, q(), FakeGateway([verdict("unclear")]), "referee")["complete"] is True


def test_referee_failed_statements_count_as_not_supported_for_facts(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1]. It has saturation [1].\n")
    gw = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents"), "nope", "nope"])
    summary = score_run(run, q([["term frequency"], ["saturation"]]), gw, "referee")
    assert summary["facts_right"] == 0.5 and summary["referee_failed"] == 1 and summary["unclear"] == 1
    assert summary["errors_through"] == 0.0 and summary["complete"] is True
    data = json.loads((run / "referee.json").read_text(encoding="utf-8"))
    assert [s["referee_failed"] for s in data["statements"]] == [False, True]
    assert data["judgements"][1]["referee_failed"] is True


def test_a_failed_judgement_beside_a_supported_one_does_not_fail_the_statement(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1][2].\n", n_sources=2)
    (run / "pages" / "2.txt").write_text(PAGE, encoding="utf-8")
    gw = FakeGateway(["nope", "nope", verdict("supported", "BM25 uses term frequency to rank documents")])
    summary = score_run(run, q(), gw, "referee")
    assert summary["supported"] == 1 and summary["referee_failed"] == 0


def test_last_json_object_ignores_inner_and_trailing_objects():
    from assistant.referee import last_json_object
    inner = '{"verdict": "unclear", "quote": "a {} b", "reason": "r"}'
    assert last_json_object("think " + inner)["verdict"] == "unclear"
    assert last_json_object('{"verdict": "supported"} trailing {"note": 1}') == {"verdict": "supported"}


def test_no_stored_text_is_a_final_failure():
    gw = FakeGateway([])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, "", gw, "referee")
    assert j.verdict == "unclear" and j.referee_failed and not j.referee_error and gw.calls == 0
    assert j.reason == "source has no stored text"


def test_verdict_is_normalised():
    gw = FakeGateway([verdict(" Not_Supported ")])
    assert judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee").verdict == "not_supported"


def test_empty_reply_message():
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, FakeGateway(["", ""]), "referee")
    assert j.reason == "empty reply" and j.referee_failed


def test_stored_source_text(tmp_path):
    from assistant.referee import stored_source_text
    run = make_run(tmp_path, "# Q\n")
    src = {"full_content": "bm25 USES term-frequency to rank documents", "snippet": "a snippet"}
    assert stored_source_text(run, "1", src) == PAGE + "\n\na snippet"   # contained: skipped
    src["full_content"] = "Extra body text."
    assert stored_source_text(run, "1", src) == PAGE + "\n\nExtra body text.\n\na snippet"
    assert stored_source_text(run, "9", {"snippet": "s"}) == "s"


def test_two_sources_one_statement(tmp_path):
    run = make_run(tmp_path, "# Q\n\nBM25 uses term frequency [1][2].\n", n_sources=2)
    (run / "pages" / "2.txt").write_text(PAGE, encoding="utf-8")
    gw = FakeGateway([verdict("not_supported"), verdict("supported", "BM25 uses term frequency to rank documents")])
    s = score_run(run, q([["term frequency"]]), gw, "referee")
    assert s["statements"] == 1 and s["supported"] == 1 and s["not_supported"] == 0 and s["facts_right"] == 1.0


# ---- score_eval: a whole eval folder ----


BENCH = Path(__file__).resolve().parent.parent / "evals" / "bench_v1.toml"


def eval_folder(tmp_path, outcomes=("ok",)):
    """An eval folder with one run per outcome; the question file holds question `q`."""
    runs = tmp_path / "runs"
    eval_dir = runs / "eval-1"
    eval_dir.mkdir(parents=True)
    rows = []
    for n, outcome in enumerate(outcomes, start=1):
        run_id = f"r{n}"
        if outcome == "ok":
            run = make_run(runs, "# Q\n\nBM25 uses term frequency [1].\n")
            run.rename(runs / run_id)
        rows.append({"question": "q", "mode": "local", "run": n, "outcome": outcome, "run_id": run_id})
    with (eval_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    qfile = tmp_path / "qs.toml"
    qfile.write_text('[[question]]\nid = "q"\ntext = "How does BM25 work?"\n', encoding="utf-8")
    spec = load_bench(BENCH)
    spec.questions = {"dev": [str(qfile)], "test": [str(tmp_path / "not_yet.toml")]}   # test file absent: skipped
    gguf = tmp_path / "ref.gguf"
    gguf.write_bytes(b"gguf bytes")
    settings = Settings(runs_dir=runs, default_tier="local", tiers={"referee": Tier(
        name="referee", kind="local", base_url="http://127.0.0.1:1234/v1", model="referee")})
    return eval_dir, spec, settings, {"REFEREE_GGUF": str(gguf)}


def served(env):
    """llama-swap's answer to /upstream/referee/props: the file the environment names."""
    return lambda base_url, model: env["REFEREE_GGUF"]


def test_score_eval_refuses_wrong_pin(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    spec.referee.sha256 = "0" * 64
    gw, reloads = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents")]), []
    with pytest.raises(BenchSpecError, match="pinned sha256"):
        score_eval(eval_dir, spec, settings, gw, env=env, served=served(env), reloader=lambda *a: reloads.append(a))
    assert gw.calls == 0 and reloads == []


def test_score_eval_skips_failed_runs(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path, outcomes=("ok", "no_report"))
    gw, reloads = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents")]), []
    rows = score_eval(eval_dir, spec, settings, gw, env=env, served=served(env), reloader=lambda s, t: reloads.append(t))
    assert [r["run_id"] for r in rows] == ["r1"] and reloads == ["referee"]   # candidate unloaded once
    with (eval_dir / "referee.csv").open(encoding="utf-8") as handle:
        written = list(csv.DictReader(handle))
    assert [r["run_id"] for r in written] == ["r1"]
    assert written[0]["supported"] == "1" and written[0]["referee_sha256"]
    text = (eval_dir / "referee.md").read_text(encoding="utf-8")
    assert REFEREE_CAVEAT in text and "| q |" in text


def test_score_eval_reuses_existing_referee_json(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    reply = verdict("supported", "BM25 uses term frequency to rank documents")
    first = FakeGateway([reply])
    score_eval(eval_dir, spec, settings, first, env=env, served=served(env), reloader=lambda *a: None)
    assert first.calls == 1
    again = FakeGateway([reply])
    rows = score_eval(eval_dir, spec, settings, again, env=env, served=served(env), reloader=lambda *a: None)
    assert again.calls == 0 and rows[0]["supported"] == 1
    # a different referee file is not the same judge: judged again
    other = tmp_path / "other.gguf"
    other.write_bytes(b"other bytes")
    third = FakeGateway([reply])
    score_eval(eval_dir, spec, settings, third, env={"REFEREE_GGUF": str(other)},
               served=lambda b, m: str(other), reloader=lambda *a: None)
    assert third.calls == 1


def test_score_eval_unknown_question_id(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    (tmp_path / "qs.toml").write_text('[[question]]\nid = "other"\ntext = "x"\n', encoding="utf-8")
    with pytest.raises(BenchSpecError, match="'q'"):
        score_eval(eval_dir, spec, settings, FakeGateway([]), env=env, served=served(env), reloader=lambda *a: None)


def test_score_eval_judges_an_incomplete_run_again(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    reply = verdict("supported", "BM25 uses term frequency to rank documents")
    first = FakeGateway([SimpleNamespace(content="", error="ConnectError: refused")] * 2)
    rows = score_eval(eval_dir, spec, settings, first, env=env, served=served(env), reloader=lambda *a: None)
    assert rows[0]["complete"] is False
    again = FakeGateway([reply])
    rows = score_eval(eval_dir, spec, settings, again, env=env, served=served(env), reloader=lambda *a: None)
    assert again.calls == 1 and rows[0]["complete"] is True


def test_score_eval_reruns_a_deterministic_failure_only_when_something_changed(tmp_path):
    """A final "unclear" from a failed judgement is reused like any verdict: rerunning can't help."""
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    rows = score_eval(eval_dir, spec, settings, FakeGateway(["nope", "nope"]), env=env, served=served(env),
                      reloader=lambda *a: None)
    assert rows[0]["complete"] is True and rows[0]["referee_failed"] == 1
    again = FakeGateway([])
    score_eval(eval_dir, spec, settings, again, env=env, served=served(env), reloader=lambda *a: None)
    assert again.calls == 0
    with (eval_dir / "referee.csv").open(encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["referee_failed"] == "1" and row["prompt_hash"] == PROMPT_HASH


# ---- the prompt pin ----

def test_prompt_hash_covers_the_prompt_and_its_settings():
    assert len(PROMPT_HASH) == 64 and PROMPT_HASH == prompt_hash()
    from assistant import referee
    base = dict(prompt=referee.PROMPT, retry=referee.RETRY, passages=referee.PASSAGE_COUNT, seed=referee.SEED,
                max_tokens=referee.MAX_TOKENS, temperature=referee.TEMPERATURE)
    assert prompt_hash(**base) == PROMPT_HASH
    for key, value in (("prompt", referee.PROMPT + " "), ("retry", ""), ("passages", 5), ("seed", 1),
                       ("max_tokens", 3000), ("temperature", 0.1)):
        assert prompt_hash(**{**base, key: value}) != PROMPT_HASH, key


def test_check_prompt(tmp_path):
    spec = load_bench(BENCH)
    check_prompt(spec)   # version 1, no hash yet (not frozen)
    spec.referee.prompt_version = PROMPT_VERSION + 1
    with pytest.raises(BenchSpecError, match=f"{PROMPT_VERSION + 1}.*{PROMPT_VERSION}"):
        check_prompt(spec)
    spec.referee.prompt_version = PROMPT_VERSION
    spec.referee.prompt_hash = "0" * 64
    with pytest.raises(BenchSpecError, match=f"0{{64}}.*{PROMPT_HASH}"):
        check_prompt(spec)
    spec.referee.prompt_hash = PROMPT_HASH.upper()
    check_prompt(spec)
    spec.frozen, spec.referee.prompt_hash = True, ""
    with pytest.raises(BenchSpecError, match="prompt_hash"):
        check_prompt(spec)


def test_score_eval_refuses_another_prompt_before_judging(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    spec.referee.prompt_version = 2
    gw, reloads = FakeGateway([]), []
    with pytest.raises(BenchSpecError, match="prompt version"):
        score_eval(eval_dir, spec, settings, gw, env=env, served=served(env), reloader=lambda *a: reloads.append(a))
    assert gw.calls == 0 and reloads == []


def test_score_eval_rejudges_a_run_from_another_prompt_hash(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    reply = verdict("supported", "BM25 uses term frequency to rank documents")
    score_eval(eval_dir, spec, settings, FakeGateway([reply]), env=env, served=served(env), reloader=lambda *a: None)
    path = settings.runs_dir / "r1" / "referee.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["summary"]["prompt_hash"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    again = FakeGateway([reply])
    score_eval(eval_dir, spec, settings, again, env=env, served=served(env), reloader=lambda *a: None)
    assert again.calls == 1


# ---- the served referee file ----

def test_score_eval_refuses_when_llama_swap_serves_another_file(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    other = tmp_path / "other.gguf"
    other.write_bytes(b"other bytes")
    gw = FakeGateway([])
    with pytest.raises(BenchSpecError, match="other.gguf"):
        score_eval(eval_dir, spec, settings, gw, env=env, served=lambda b, m: str(other), reloader=lambda *a: None)
    assert gw.calls == 0 and not (eval_dir / "referee.csv").exists()


def test_score_eval_fails_closed_when_the_served_file_cannot_be_read(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    seen = []

    def down(base_url, model):
        seen.append((base_url, model))
        raise BenchSpecError("could not read the served model file: ConnectError: refused")
    gw = FakeGateway([])
    with pytest.raises(BenchSpecError, match="refused"):
        score_eval(eval_dir, spec, settings, gw, env=env, served=down, reloader=lambda *a: None)
    assert gw.calls == 0 and seen == [("http://127.0.0.1:1234/v1", "referee")]


# ---- what the manifest recorded ----

def bench_manifest(eval_dir, qfile, split="dev", **extra):
    bench = {"version": 1, "split": split, "question_files": {str(qfile): sha256_file(qfile)}, **extra}
    (eval_dir / "manifest.json").write_text(json.dumps({"bench": bench}), encoding="utf-8")


def test_score_eval_refuses_a_question_file_changed_after_the_run(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    qfile = tmp_path / "qs.toml"
    bench_manifest(eval_dir, qfile)
    reply = verdict("supported", "BM25 uses term frequency to rank documents")
    score_eval(eval_dir, spec, settings, FakeGateway([reply]), env=env, served=served(env), reloader=lambda *a: None)
    qfile.write_text(qfile.read_text(encoding="utf-8").replace("How does", "Why does"), encoding="utf-8")
    gw = FakeGateway([reply])
    with pytest.raises(BenchSpecError, match="qs.toml changed"):
        score_eval(eval_dir, spec, settings, gw, env=env, served=served(env), reloader=lambda *a: None)
    assert gw.calls == 0


def test_score_eval_on_the_test_split_needs_the_committed_ledger_row(tmp_path):
    from assistant.ledger import LEDGER_FIELDS
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    bench_manifest(eval_dir, tmp_path / "qs.toml", split="test")
    header = ",".join(LEDGER_FIELDS)
    gw = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents")])
    for committed in (None, header, header + "\n1,gemma-e4b,k,g,c,test,eval-2,started,t,"):
        with pytest.raises(BenchSpecError, match="committed"):
            score_eval(eval_dir, spec, settings, gw, env=env, served=served(env), reloader=lambda *a: None,
                       committed_ledger=committed)
    assert gw.calls == 0
    rows = score_eval(eval_dir, spec, settings, gw, env=env, served=served(env), reloader=lambda *a: None,
                      committed_ledger=header + "\n1,gemma-e4b,k,g,c,test,eval-1,started,t,")
    assert rows[0]["supported"] == 1


# ---- the CLI ----

CLI_CONFIG = """
[assistant]
default_tier = "local"
runs_dir = "{runs}"

[tiers.local]
kind = "local"
base_url = "http://127.0.0.1:1234/v1"
model = "gemma"
"""


@pytest.mark.parametrize("raised", ["missing", "tier", "privacy", "spec", "transport"])
def test_cli_referee_refusals_are_one_line(tmp_path, monkeypatch, capsys, raised):
    from assistant import __main__ as cli
    from assistant.gateway import PrivacyError, TierNotReady
    config = tmp_path / "assistant.toml"
    config.write_text(CLI_CONFIG.format(runs=(tmp_path / "runs").as_posix()), encoding="utf-8")
    eval_dir = tmp_path / "runs" / "eval-1"
    if raised != "missing":
        eval_dir.mkdir(parents=True)
        (eval_dir / "summary.csv").write_text("question,run_id,outcome\n", encoding="utf-8")
    errors = {"tier": TierNotReady("tier 'referee' needs X"), "privacy": PrivacyError("tier leaves this PC"),
              "spec": BenchSpecError("question file a.toml changed\nand more"),
              "transport": httpx.ConnectError("refused")}

    def fake_score_eval(*args, **kwargs):
        raise errors[raised]
    monkeypatch.setattr(cli, "score_eval", fake_score_eval)
    monkeypatch.setattr(cli, "_git", lambda *a: "")
    assert cli.main(["--config", str(config), "referee", str(eval_dir), "--bench", str(BENCH)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Referee refused: ") and out.count("\n") == 1 and "Traceback" not in out


def test_cli_referee_passes_the_committed_ledger(tmp_path, monkeypatch, capsys):
    from assistant import __main__ as cli
    config = tmp_path / "assistant.toml"
    config.write_text(CLI_CONFIG.format(runs=(tmp_path / "runs").as_posix()), encoding="utf-8")
    eval_dir = tmp_path / "runs" / "eval-1"
    eval_dir.mkdir(parents=True)
    (eval_dir / "summary.csv").write_text("question,run_id,outcome\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(cli, "score_eval", lambda *a, **kw: seen.append(kw) or [])
    monkeypatch.setattr(cli, "_git", lambda *a: "ledger at HEAD" if a == ("show", "HEAD:results/bench_ledger.csv") else "")
    (eval_dir / "referee.md").write_text("# r\n", encoding="utf-8")
    cli.main(["--config", str(config), "referee", str(eval_dir), "--bench", str(BENCH)])
    assert seen[0]["committed_ledger"] == "ledger at HEAD"
