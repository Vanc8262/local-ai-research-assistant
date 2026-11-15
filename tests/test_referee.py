"""Referee scoring of a saved run folder, with a scripted fake gateway (no model, no network)."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from assistant.bench_spec import REFEREE_CAVEAT
from assistant.evaluate import Question
from assistant.referee import Judgement, judge, score_run, statement_verdict, statements

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
    assert j.verdict == "unclear" and j.reason.startswith("quote not in source: ")


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


def test_referee_bad_reply_becomes_unclear():
    gw = FakeGateway(["nope", ""])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee")
    assert j.verdict == "unclear" and j.referee_error and gw.calls == 2


def test_error_reply_is_a_bad_reply():
    err = SimpleNamespace(content="", error="boom")
    gw = FakeGateway([err, err])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee")
    assert j.verdict == "unclear" and "boom" in j.referee_error


def test_verdict_outside_labels_is_bad():
    gw = FakeGateway([verdict("maybe"), verdict("maybe")])
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee")
    assert j.verdict == "unclear" and j.referee_error


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
    assert score_run(run, q(), FakeGateway(["nope", ""]), "referee")["complete"] is False
    assert score_run(run, q(), FakeGateway([verdict("unclear")]), "referee")["complete"] is True


def test_last_json_object_ignores_inner_and_trailing_objects():
    from assistant.referee import last_json_object
    inner = '{"verdict": "unclear", "quote": "a {} b", "reason": "r"}'
    assert last_json_object("think " + inner)["verdict"] == "unclear"
    assert last_json_object('{"verdict": "supported"} trailing {"note": 1}') == {"verdict": "supported"}


def test_no_stored_text_is_a_referee_error():
    j = judge("BM25 uses term frequency", "1", {"index": 1}, "", FakeGateway([]), "referee")
    assert j.verdict == "unclear" and j.referee_error == "no stored text"


def test_verdict_is_normalised():
    gw = FakeGateway([verdict(" Not_Supported ")])
    assert judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, gw, "referee").verdict == "not_supported"


def test_empty_reply_message():
    j = judge("BM25 uses term frequency", "1", {"index": 1}, PAGE, FakeGateway(["", ""]), "referee")
    assert j.referee_error == "empty reply"


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

import csv  # noqa: E402

import pytest  # noqa: E402

from assistant.bench_spec import BenchSpecError, load_bench  # noqa: E402
from assistant.config import Settings  # noqa: E402
from assistant.referee import score_eval  # noqa: E402

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
    settings = Settings(runs_dir=runs, default_tier="local")
    return eval_dir, spec, settings, {"REFEREE_GGUF": str(gguf)}


def test_score_eval_refuses_wrong_pin(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    spec.referee.sha256 = "0" * 64
    gw, reloads = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents")]), []
    with pytest.raises(BenchSpecError, match="pinned sha256"):
        score_eval(eval_dir, spec, settings, gw, env=env, reloader=lambda *a: reloads.append(a))
    assert gw.calls == 0 and reloads == []


def test_score_eval_skips_failed_runs(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path, outcomes=("ok", "no_report"))
    gw, reloads = FakeGateway([verdict("supported", "BM25 uses term frequency to rank documents")]), []
    rows = score_eval(eval_dir, spec, settings, gw, env=env, reloader=lambda s, t: reloads.append(t))
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
    score_eval(eval_dir, spec, settings, first, env=env, reloader=lambda *a: None)
    assert first.calls == 1
    again = FakeGateway([reply])
    rows = score_eval(eval_dir, spec, settings, again, env=env, reloader=lambda *a: None)
    assert again.calls == 0 and rows[0]["supported"] == 1
    # a different referee file is not the same judge: judged again
    other = tmp_path / "other.gguf"
    other.write_bytes(b"other bytes")
    third = FakeGateway([reply])
    score_eval(eval_dir, spec, settings, third, env={"REFEREE_GGUF": str(other)}, reloader=lambda *a: None)
    assert third.calls == 1


def test_score_eval_unknown_question_id(tmp_path):
    eval_dir, spec, settings, env = eval_folder(tmp_path)
    (tmp_path / "qs.toml").write_text('[[question]]\nid = "other"\ntext = "x"\n', encoding="utf-8")
    with pytest.raises(BenchSpecError, match="'q'"):
        score_eval(eval_dir, spec, settings, FakeGateway([]), env=env, reloader=lambda *a: None)
