import json
import sys

import httpx
import pytest

from assistant.config import Settings, Tier
from assistant.research import run_research
from assistant.runlog import RunLog
from assistant.snapshot import SearchSnapshot

HIT = [{"title": "What's new", "url": "https://docs.python.org/3.15", "engine": "arxiv", "layer": "live",
        "text": "Python 3.15 adds lazy imports."}]


def test_record_then_replay_exact_nearest_and_missed(tmp_path):
    calls = []
    record = SearchSnapshot(tmp_path, "record").wrap(lambda q: calls.append(q) or HIT)
    assert record("Python 3.15 lazy imports") == HIT and calls == ["Python 3.15 lazy imports"]

    replay = SearchSnapshot(tmp_path, "replay")
    search = replay.wrap(lambda q: pytest.fail("replay must not search"))
    assert search("python 3.15  LAZY imports") == HIT                 # same query, other spacing and case
    assert search("lazy imports in Python 3.15 release") == HIT       # shares most keywords
    assert search("AWS Well-Architected pillars") == []               # nothing close
    assert replay.counts == {"recorded": 0, "exact": 1, "nearest": 1, "missed": 1}


def test_replay_needs_a_recording(tmp_path):
    with pytest.raises(FileNotFoundError):
        SearchSnapshot(tmp_path / "none", "replay")
    with pytest.raises(ValueError):
        SearchSnapshot(tmp_path, "rewind")


# Stands in for LDR: one page search through the proxy, then a report citing its first result.
SEARCHING_LDR = r'''
import json, sys, urllib.request
job = json.load(sys.stdin)
req = urllib.request.Request(job["pages"]["search_url"], json.dumps({"query": "python 3.15 lazy imports"}).encode(),
                             {"Content-Type": "application/json"})
results = json.load(urllib.request.urlopen(req))["results"]
summary = "Python 3.15 adds lazy imports [1]." if results else "Nothing found."
sources = [{"index": "1", "title": r["title"], "link": r["url"]} for r in results[:1]]
json.dump({"result": {"summary": summary, "sources": sources}, "error": None, "elapsed_s": 1.0}, open(job["out"], "w"))
'''


def test_research_replays_page_searches_without_the_network(tmp_path):
    script = tmp_path / "ldr.py"
    script.write_text(SEARCHING_LDR, encoding="utf-8")
    settings = Settings(runs_dir=tmp_path / "runs", default_tier="local", codex_dir=tmp_path / "codex")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://localhost/v1", model="gemma")
    settings.search.router = False   # SearXNG first, as before the router
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.path == "/search":   # SearXNG
            return httpx.Response(200, json={"results": [{"title": "What's new", "url": "https://docs.python.org/3.15",
                                                          "content": "Python 3.15 adds lazy imports."}]})
        return httpx.Response(200, text="<p>Python 3.15 adds lazy imports.</p>", headers={"content-type": "text/html"})

    snapshots = tmp_path / "snap"
    log = RunLog(settings.runs_dir, task="record")
    recorded = run_research(settings, "q", log, runner=[sys.executable, str(script)], transport=httpx.MockTransport(handler),
                            snapshot=SearchSnapshot(snapshots, "record"))
    assert recorded.sources and seen
    saved = [json.loads(line) for line in (snapshots / "searches.jsonl").read_text(encoding="utf-8").splitlines()]
    assert saved[0]["query"] == "python 3.15 lazy imports" and saved[0]["results"]

    seen.clear()
    replay = SearchSnapshot(snapshots, "replay")
    log = RunLog(settings.runs_dir, task="replay")
    replayed = run_research(settings, "q", log, runner=[sys.executable, str(script)], transport=httpx.MockTransport(handler),
                            snapshot=replay)
    assert [s["link"] for s in replayed.sources] == [s["link"] for s in recorded.sources]
    assert seen == [] and replay.counts["exact"] == 1
