import json
import sys

import httpx
import pytest

import fake_model
from assistant.codex import build_index, embed_url, index_dir, write_manifest
from assistant.config import Settings, Tier
from assistant.gateway import Gateway, PrivacyError, TierNotReady
from assistant.research import run_research
from assistant.runlog import RunLog, read_run
from assistant.verify import verify_run

# Stands in for codex_index.py: records the job it was given and writes an index in its shape.
FAKE_INDEXER = r'''
import json, sys
from pathlib import Path
job = json.load(sys.stdin)
index = Path(job["index_dir"]); index.mkdir(parents=True, exist_ok=True)
files = [{"id": r["id"], "file": r["file"], "sha256": r["sha256"], "pages": 2, "chunks": 5} for r in job["files"]]
meta = {"embed_model": job["embed_model"], "files": files, "chunks": 5 * len(files)}
(index / "index.json").write_text(json.dumps(meta))
Path(job["out"]).write_text(json.dumps({**meta, "error": None, "elapsed_s": 1.0, "job": job}))
'''

# Stands in for LDR searching the codex: echoes the codex job and returns a file:// source.
FAKE_LDR = r'''
import json, sys
job = json.load(sys.stdin)
result = {"summary": "ReAct interleaves reasoning traces and actions [1].", "sources": [
    {"index": "1", "title": "ReAct (p. 1)", "link": "file:///codex/llm-agents/react.pdf#page=1",
     "snippet": "ReAct", "full_content": "ReAct prompts the model to interleave reasoning traces and actions.",
     "codex_job": job.get("codex"), "search_tool": job["overrides"]["search.tool"]}]}
json.dump({"result": result, "error": None, "elapsed_s": 1.0}, open(job["out"], "w"))
'''


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path / "runs", default_tier="local", codex_dir=tmp_path / "codex")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://127.0.0.1:1234/v1", model="gemma")
    return settings


def script(tmp_path, name: str, body: str) -> list[str]:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return [sys.executable, str(path)]


def add_pdfs(settings, *ids: str) -> None:
    rows = {}
    for codex_id in ids:
        path = settings.codex_dir / "llm-agents" / f"{codex_id}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7 " + codex_id.encode())
        rows[codex_id] = {"id": codex_id, "topic": "llm-agents", "title": codex_id, "authors": "", "year": 2023,
                          "url": f"https://arxiv.org/pdf/{codex_id}", "licence": "", "bytes": 10,
                          "file": f"llm-agents/{codex_id}.pdf", "sha256": f"sha-{codex_id}", "downloaded_at": ""}
    write_manifest(settings.codex_dir, rows)


def test_index_embeds_downloaded_pdfs_and_skips_when_current(tmp_path):
    settings = settings_for(tmp_path)
    add_pdfs(settings, "react", "lora")
    indexer = script(tmp_path, "fake_indexer.py", FAKE_INDEXER)

    log = RunLog(settings.runs_dir)
    record = build_index(settings, log, runner=indexer)
    assert record["error"] is None and record["chunks"] == 10
    assert record["job"]["embed_url"] == "http://127.0.0.1:1234/v1"
    assert record["job"]["index_dir"] == str(settings.codex_dir / ".index")
    assert sorted(r["id"] for r in record["job"]["files"]) == ["lora", "react"]

    again = build_index(settings, RunLog(settings.runs_dir), runner=["never-run"])
    assert again.get("current") and again["chunks"] == 10

    add_pdfs(settings, "react", "lora", "rag")      # a new PDF makes the index stale
    assert build_index(settings, RunLog(settings.runs_dir), runner=indexer)["chunks"] == 15
    steps = [r for r in read_run(log.path) if r["event"] == "step"]
    assert [(s["kind"], s["outcome"], s["chunks"]) for s in steps] == [("codex-index", "ok", 10)]


def test_index_reports_an_empty_codex_and_refuses_remote_embeddings(tmp_path):
    settings = settings_for(tmp_path)
    assert "codex fetch" in build_index(settings, RunLog(settings.runs_dir), runner=["never-run"])["error"]
    settings.research.embed_url = "https://api.example.com/v1"
    with pytest.raises(ValueError, match="private"):
        embed_url(settings)


def test_codex_research_passes_the_index_to_ldr_and_stays_private(tmp_path):
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    ldr = script(tmp_path, "fake_ldr.py", FAKE_LDR)
    with pytest.raises(TierNotReady, match="codex index"):
        run_research(settings, "q", log, search="codex", runner=ldr)
    with pytest.raises(PrivacyError, match="private"):
        run_research(settings, "q", log, search="codex", public=True, runner=ldr)

    add_pdfs(settings, "react")
    build_index(settings, RunLog(settings.runs_dir), runner=script(tmp_path, "fake_indexer.py", FAKE_INDEXER))
    result = run_research(settings, "What is ReAct?", log, search="codex", runner=ldr,
                          transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    source = result.sources[0]
    assert result.error is None and source["search_tool"] == "codex"
    assert source["codex_job"] == {"index_dir": str(index_dir(settings)), "embed_url": "http://127.0.0.1:1234/v1",
                                   "embed_model": settings.research.embed_model, "k": 8}
    assert "file:///codex/llm-agents/react.pdf#page=1" in result.report_path.read_text(encoding="utf-8")


def test_verifier_checks_codex_sources_against_their_chunk_without_fetching(tmp_path):
    settings, prompts = settings_for(tmp_path), []
    run_dir = settings.runs_dir / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# q\n\nReAct interleaves reasoning traces and actions [1].\n",
                                       encoding="utf-8")
    (run_dir / "sources.json").write_text(json.dumps([
        {"index": "1", "title": "ReAct (p. 1)", "link": "file:///codex/react.pdf#page=1", "snippet": "",
         "full_content": "ReAct prompts the model to interleave reasoning traces and actions."}]))

    def handler(request):
        assert request.url.host == "127.0.0.1", "codex sources must not be fetched"
        prompts.append(json.loads(request.content)["messages"][0]["content"])
        answer = {"verdict": "supported", "quote": "interleave reasoning traces and actions", "reason": "stated"}
        return httpx.Response(200, content=fake_model.sse({"content": json.dumps(answer)}))

    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=httpx.MockTransport(handler))
    result = verify_run(run_dir, gateway, log, transport=httpx.MockTransport(handler))
    gateway.close()
    assert [c.verdict for c in result.claims] == ["supported"]
    assert "interleave reasoning traces" in prompts[0]


def test_duplicate_sources_are_merged_and_citations_point_at_the_first():
    from assistant.research import merge_duplicate_sources
    chunk = {"link": "file:///c/qlora.pdf#page=2", "full_content": "NF4 is optimal for normal weights."}
    sources = [{"index": "1", **chunk}, {"index": "2", **chunk}, {"index": "3", **chunk},
               {"index": "4", "link": "file:///c/qlora.pdf#page=6", "full_content": "Matches 16-bit."},
               {"index": "5", "link": "file:///c/qlora.pdf#page=6", "full_content": "Matches 16-bit."},
               {"index": "6", "link": "file:///c/qlora.pdf#page=2", "full_content": "Another chunk."}]
    summary = "NF4 is optimal [1], [2], [3]. It matches 16-bit [4, 5] and [2]. Also [6] and [1-3]."
    merged, kept = merge_duplicate_sources(summary, sources)
    assert [s["index"] for s in kept] == ["1", "4", "6"]
    assert merged == "NF4 is optimal [1]. It matches 16-bit [4] and [1]. Also [6] and [1-3]."
    assert merge_duplicate_sources("a [1].", sources[:1]) == ("a [1].", sources[:1])


def test_keyword_terms_split_numbers_from_units_and_fusion_rewards_agreement():
    from types import SimpleNamespace

    from assistant.codex_index import fuse, terms
    assert terms("QLoRA fits 65B on one 48GB GPU, Q4_K_M 3.5") == [
        "qlora", "fits", "65", "b", "on", "one", "48", "gb", "gpu", "q", "4", "k", "m", "3.5"]

    def doc(page, text):
        return SimpleNamespace(metadata={"source": f"file:///q.pdf#page={page}"}, page_content=text)
    a, b, c, d = doc(1, "48GB headline"), doc(2, "NF4"), doc(3, "paged"), doc(22, "appendix")
    vector, keyword = [b, c, a, d], [a, d, b]
    fused = fuse([vector, keyword], 3)
    assert fused[0] in (a, b) and {x.page_content for x in fused} == {"48GB headline", "NF4", "appendix"}
    assert len(fuse([[a, a], [a]], 5)) == 1
