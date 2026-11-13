import json

import httpx

import fake_model
from assistant.config import Settings, Tier
from assistant.gateway import Gateway
from assistant.runlog import RunLog, read_run
from assistant.verify import (best_passages, html_to_text, missing_facts, parse_verdict, split_claims,
                              verify_run, quote_in_source, PROMPT, COMBINED_PROMPT)

REPORT = """# What is new in Python 3.15?

Python 3.15 adds lazy imports through the new lazy keyword [1].
Python 3.15 adds Zstandard compression in the new compression.zstd module [2].
The free-threaded build no longer needs a separate installer on Windows, which helps adoption.
Its startup is faster thanks to frozen modules [1, 3].

## Sources

[1] What's new in Python 3.15
    https://docs.python.org/3/whatsnew/3.15.html
"""

PAGES = {
    "/3/whatsnew/3.15.html": "<html><nav>menu</nav><p>Python 3.15 introduces lazy imports: the new "
                             "<code>lazy</code> keyword defers loading a module until first use.</p>"
                             "<script>var x = 1;</script></html>",
    "/python-314": "<p>Python 3.14 adds Zstandard support in the new compression.zstd module.</p>",
}
SOURCES = [
    {"index": "1", "title": "What's new in Python 3.15", "link": "https://docs.python.org/3/whatsnew/3.15.html"},
    {"index": "2", "title": "Python 3.14 new features", "link": "https://realpython.com/python-314"},
]


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path / "runs", default_tier="local")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://local/v1", model="gemma")
    return settings


def transport(prompts: list):
    """Serves the cited pages, and plays a model that always says "supported" with a quote."""
    def handler(request):
        if request.url.host == "local":
            prompt = json.loads(request.content)["messages"][0]["content"]
            prompts.append(prompt)
            quote = ("the new lazy keyword defers loading a module until first use" if "lazy" in prompt
                     else "Python 3.14 adds Zstandard support in the new compression.zstd module.")
            answer = json.dumps({"verdict": "supported", "quote": quote, "reason": "stated"})
            return httpx.Response(200, content=fake_model.sse({"content": answer}))
        page = PAGES.get(request.url.path)
        if page is None:
            return httpx.Response(404)
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})
    return httpx.MockTransport(handler)


def make_run(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text(REPORT, encoding="utf-8")
    (run_dir / "sources.json").write_text(json.dumps(SOURCES), encoding="utf-8")
    return run_dir


def test_split_claims_finds_citations_and_skips_the_source_list():
    claims = split_claims(REPORT)
    assert [c.citations for c in claims] == [["1"], ["2"], [], ["1", "3"]]
    assert claims[0].text == "Python 3.15 adds lazy imports through the new lazy keyword ."
    assert all("docs.python.org" not in c.text for c in claims)


def test_citation_ranges_expand():
    (claim,) = split_claims("Three sources agree on this point about caching [2–4].")
    assert claim.citations == ["2", "3", "4"]


def test_verifier_catches_a_claim_cited_to_the_wrong_version(tmp_path):
    run_dir, prompts = make_run(tmp_path), []
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=transport(prompts))
    result = verify_run(run_dir, gateway, log, transport=transport(prompts))
    gateway.close()

    assert [(c.citations, c.verdict) for c in result.claims] == [
        (["1"], "supported"),
        (["2"], "partial"),          # the source is about 3.14, so "3.15" is missing from it
        ([], "uncited"),
        (["1", "3"], "supported"),   # the best of its sources counts; [3] does not exist
    ]
    zstd = result.claims[1].checks[0]
    assert zstd.missing_facts == ["3.15"] and "does not mention 3.15" in zstd.reason
    dangling = [c for c in result.claims[3].checks if c.index == "3"][0]
    assert dangling.verdict == "unsupported" and "no source" in dangling.reason

    summary = json.loads((run_dir / "verification.json").read_text())["summary"]
    assert summary["claims"] == 4 and summary["uncited"] == 1 and summary["citation_coverage"] == 0.75
    assert summary["dangling_citations"] == 1
    assert "Citation coverage: 75%" in (run_dir / "verification.md").read_text()
    assert (run_dir / "pages" / "1.txt").read_text().startswith("Python 3.15 introduces lazy imports")
    steps = [r for r in read_run(log.path) if r["event"] == "step"]
    assert {s["purpose"] for s in steps if s["kind"] == "model"} == {"verify"}
    assert all(s["private"] for s in steps if s["kind"] == "model")
    assert steps[-1]["kind"] == "verify" and steps[-1]["uncited"] == 1


def test_a_quote_the_source_does_not_contain_is_not_support(tmp_path):
    run_dir = make_run(tmp_path)
    (run_dir / "report.md").write_text("Python 3.15 adds lazy imports through a keyword [1].\n", encoding="utf-8")
    settings = settings_for(tmp_path)

    def invented(request):
        if request.url.host == "local":
            answer = json.dumps({"verdict": "supported", "quote": "Lazy imports make startup 50% faster."})
            return httpx.Response(200, content=fake_model.sse({"content": answer}))
        return httpx.Response(200, text=PAGES["/3/whatsnew/3.15.html"], headers={"content-type": "text/html"})

    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=httpx.MockTransport(invented))
    (claim,) = verify_run(run_dir, gateway, log, transport=httpx.MockTransport(invented)).claims
    assert claim.verdict == "partial" and "not in the source" in claim.checks[0].reason


def test_helpers():
    assert html_to_text("<p>a <b>b</b></p><script>x()</script><style>p{}</style>") == "a b"
    assert missing_facts("Python 3.15 is 20% faster since 2026", "In 2026 Python 3.15 got 20 % faster") == []
    assert missing_facts("Python 3.15 adds zstd", "Python 3.14 adds zstd") == ["3.15"]
    assert parse_verdict('Sure! {"verdict": "partial", "quote": "q"}')["verdict"] == "partial"
    assert parse_verdict("no json here")["verdict"] == "unchecked"
    text = "filler " * 200 + "the zstd module compresses data " + "filler " * 200
    assert "zstd" in best_passages("the zstd module", text)[0]


def test_lead_ins_and_bold_headings_are_not_claims_and_bullets_share_citations():
    report = """# Q

The main new features in Python 3.15 include:
**Context from Python 3.14 (Previous Stable Release)**
- **Subinterpreters** Python 3.14 introduced support for running subinterpreters from Python code. They share one process [6][7].
"""
    claims = split_claims(report)
    assert [(c.text[:30], c.citations, c.inherited) for c in claims] == [
        ("Subinterpreters Python 3.14 in", ["6", "7"], True),
        ("They share one process .", ["6", "7"], False),
    ]


def test_snippet_is_used_when_the_page_is_boilerplate_and_unparsed_replies_are_retried(tmp_path):
    run_dir = tmp_path / "runs" / "r2"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("The talk explains lazy imports in Python 3.15 [1].\n", encoding="utf-8")
    (run_dir / "sources.json").write_text(json.dumps([{
        "index": "1", "title": "Video", "link": "https://youtube.com/watch?v=x",
        "snippet": "In this talk: lazy imports in Python 3.15 explained."}]), encoding="utf-8")
    replies = iter(["Let me think about this...",
                    json.dumps({"verdict": "supported", "quote": "lazy imports in Python 3.15 explained."})])
    prompts = []

    def handler(request):
        if request.url.host == "local":
            prompts.append(json.loads(request.content)["messages"][0]["content"])
            return httpx.Response(200, content=fake_model.sse({"content": next(replies)}))
        return httpx.Response(200, text="<p>YouTube About Press Copyright</p>", headers={"content-type": "text/html"})

    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=httpx.MockTransport(handler))
    (claim,) = verify_run(run_dir, gateway, log, transport=httpx.MockTransport(handler)).claims
    assert claim.verdict == "supported" and claim.support == "1 of 1"
    assert len(prompts) == 2 and "JSON object only" in prompts[1] and "lazy imports in Python 3.15" in prompts[0]


def test_unparsed_reply_is_kept_for_diagnosis(tmp_path):
    run_dir = make_run(tmp_path)
    (run_dir / "report.md").write_text("Python 3.15 adds lazy imports through a keyword [1].\n", encoding="utf-8")

    def handler(request):
        if request.url.host == "local":
            return httpx.Response(200, content=fake_model.sse({"content": "I am not sure."}))
        return httpx.Response(200, text=PAGES["/3/whatsnew/3.15.html"], headers={"content-type": "text/html"})

    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=httpx.MockTransport(handler))
    (claim,) = verify_run(run_dir, gateway, log, transport=httpx.MockTransport(handler)).claims
    assert claim.verdict == "unchecked" and claim.checks[0].reason == "model reply was not a verdict: I am not sure."


def test_quote_matching_tolerates_formatting_but_not_invention():
    from assistant.verify import quote_in_source
    page = "| Version | Status |\n| 3.15.0 | Released on 9 Oct 2026 |  The default is None : use [12:01] lazy imports."
    assert quote_in_source("Released on 9 Oct 2026", page)
    assert quote_in_source("The default is None: use lazy imports.", page)            # spacing, timestamp
    assert quote_in_source("3.15.0 Released [...] The default is None", page)         # elision
    assert not quote_in_source("Released on 19 Oct 2026 with the GIL removed entirely", page)
    assert not quote_in_source("short", page)
    rows = "PEP 661: Add sentinel built-in type\nPEP 686: UTF-8 by default\nPEP 814: Add frozendict built-in type"
    assert quote_in_source("PEP 661: Add sentinel built-in type\nPEP 814: Add frozendict built-in type", rows)
    assert not quote_in_source("PEP 661: Add sentinel built-in type\nPEP 999: Remove the GIL entirely", rows)
    assert quote_in_source("support development and run workloads effectively",      # PDF ligature
                           "The ability to support development and run \nworkloads eﬀectively")


def test_sources_that_each_cover_part_of_a_list_are_judged_together(tmp_path):
    run_dir = tmp_path / "runs" / "r3"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("The framework covers security and also reliability [1][2].\n",
                                       encoding="utf-8")
    (run_dir / "sources.json").write_text(json.dumps([
        {"index": "1", "title": "A", "link": "https://a.example/p"},
        {"index": "2", "title": "B", "link": "https://b.example/p"}]), encoding="utf-8")
    pages = {"a.example": "<p>The security pillar protects data and systems.</p>",
             "b.example": "<p>The reliability pillar covers recovery from failure.</p>"}
    prompts = []

    def handler(request):
        if request.url.host == "local":
            prompt = json.loads(request.content)["messages"][0]["content"]
            prompts.append(prompt)
            if "support it together" in prompt:
                answer = {"verdict": "supported", "quotes": [
                    {"source": "1", "quote": "The security pillar protects data and systems."},
                    {"source": "2", "quote": "The reliability pillar covers recovery from failure."}]}
            else:
                answer = {"verdict": "partial", "quote": "", "reason": "covers one item"}
            return httpx.Response(200, content=fake_model.sse({"content": json.dumps(answer)}))
        return httpx.Response(200, text=pages[request.url.host], headers={"content-type": "text/html"})

    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=httpx.MockTransport(handler))
    (claim,) = verify_run(run_dir, gateway, log, transport=httpx.MockTransport(handler)).claims
    assert [c.verdict for c in claim.checks] == ["partial", "partial", "supported"]
    assert claim.checks[-1].combined and claim.checks[-1].index == "1+2"
    assert claim.verdict == "supported" and claim.support == "0 of 2 (together)"
    assert len(prompts) == 3


def test_model_reloads_every_n_verification_calls(tmp_path):
    run_dir, reloads = make_run(tmp_path), []
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=transport([]))
    verify_run(run_dir, gateway, log, transport=transport([]), reload_every=2,
               reloader=lambda: reloads.append(1))
    calls = sum(1 for r in read_run(log.path) if r.get("kind") == "model")
    assert calls == 3 and len(reloads) == 1   # before the third call


def test_verification_samples_at_temperature_zero(tmp_path):
    run_dir, bodies = make_run(tmp_path), []
    settings = settings_for(tmp_path)
    settings.tiers["local"].temperature = 1.0   # the tier's writing preset

    def handler(request):
        if request.url.host == "local":
            bodies.append(json.loads(request.content))
        return transport([]).handle_request(request)

    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=httpx.MockTransport(handler))
    verify_run(run_dir, gateway, log, transport=httpx.MockTransport(handler))
    assert bodies and all(body["temperature"] == 0.0 for body in bodies)


def test_short_sources_are_passed_whole_and_long_ones_keep_their_tail():
    chunk = "QLoRA quantizes the base model to 4 bits. " * 10 + "It adds adapters at every network layer."
    assert best_passages("adapters at every network layer", chunk) == [chunk]
    page = "filler text here. " * 200 + "The tail says adapters at every network layer."
    assert any(p.endswith("every network layer.") for p in best_passages("adapters every network layer", page))


class FakeDecider:
    """Says "true" with the given probability; error makes every decision fail."""

    def __init__(self, p_true: float = 0.95, error: str = "") -> None:
        self.p_true, self.error, self.asked = p_true, error, []

    def ask(self, state, question, **kwargs):
        from assistant.decide import Decision, confidence_of
        self.asked.append((state, question.instructions))
        probs = {"true": self.p_true, "false": 1 - self.p_true}
        return Decision(kind="noul", answer="" if self.error else max(probs, key=probs.get), probabilities=probs,
                        confidence=0.0 if self.error else confidence_of(probs), mass=1.0, method="chat",
                        latency_s=0.1, tier="local", error=self.error)


def verify_with(tmp_path, decider):
    run_dir, prompts = make_run(tmp_path), []
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=transport(prompts))
    result = verify_run(run_dir, gateway, log, transport=transport(prompts), decider=decider)
    gateway.close()
    return result


def test_the_decider_drops_a_pass_it_doubts_to_partial(tmp_path):
    decider = FakeDecider(p_true=0.3)
    result = verify_with(tmp_path, decider)
    assert result.claims[0].verdict == "partial"
    assert result.claims[0].checks[0].reason.startswith("the decider doubts it (p 0.30")
    assert result.summary()["doubted"] >= 1
    assert "The passages support this claim: Python 3.15 adds lazy imports" in decider.asked[0][1]
    assert "lazy keyword defers loading" in decider.asked[0][0]


def test_a_confident_decider_keeps_the_pass(tmp_path):
    result = verify_with(tmp_path, FakeDecider(p_true=0.97))
    assert result.claims[0].verdict == "supported" and result.claims[0].checks[0].decider_p == 0.97
    assert result.summary()["doubted"] == 0


def test_the_decider_overrules_below_p_0_8_only(tmp_path):
    assert verify_with(tmp_path / "a", FakeDecider(p_true=0.85)).claims[0].verdict == "supported"
    assert verify_with(tmp_path / "b", FakeDecider(p_true=0.75)).claims[0].verdict == "partial"


def test_a_failing_decider_is_counted_not_trusted_silently(tmp_path):
    result = verify_with(tmp_path, FakeDecider(error="no logprobs"))
    assert result.claims[0].verdict == "supported"
    assert result.summary()["decider_errors"] >= 1


def test_table_values_are_quoted_as_header_and_row():
    # The quantize README table, as the codex indexed it (2026-11-12 gguf-quants run).
    table = ("| Measure     | Q4_K_S | Q4_K_M | Q5_K_S | Q5_K_M | Q6_K   | Q8_0   |\n"
             "|-------------|--------|--------|--------|--------|--------|--------|\n"
             "| bits/weight | 4.6672 | 4.8944 | 5.5704 | 5.7036 | 6.5633 | 8.5008 |\n"
             "| size (GiB)  | 4.36   | 4.58   | 5.21   | 5.33   | 6.14   | 7.95   |\n")
    header_and_row = ("| Measure | Q4_K_S | Q4_K_M | Q5_K_S | Q5_K_M | Q6_K | Q8_0 |\n"
                      "| size (GiB) | 4.36 | 4.58 | 5.21 | 5.33 | 6.14 | 7.95 |")
    assert quote_in_source(header_and_row, table)
    # What the checker wrote instead: a row label joined to the header cells, not in the source.
    assert not quote_in_source("bits/weight | Q4_K_S | Q4_K_M | Q5_K_S | Q5_K_M | Q6_K", table)
    for prompt in (PROMPT, COMBINED_PROMPT):
        assert "header row" in prompt and "{claim}" in prompt
