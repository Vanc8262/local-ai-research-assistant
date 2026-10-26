import json

import httpx

import fake_model
from assistant.config import Settings, Tier
from assistant.gateway import Gateway
from assistant.runlog import RunLog, read_run
from assistant.verify import (best_passages, html_to_text, missing_facts, parse_verdict, split_claims,
                              verify_run)

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
