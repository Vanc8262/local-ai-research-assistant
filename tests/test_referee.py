"""Referee scoring of a saved run folder, with a scripted fake gateway (no model, no network)."""
import json
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
