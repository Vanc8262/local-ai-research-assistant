import json

import httpx

from assistant.config import load_settings
from assistant.sources import Router, abstract, merge, pick_live
from assistant.web_pages import search_pages

ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/2305.14314v1</id><title>QLoRA: Efficient Finetuning
  of Quantized LLMs</title><summary>We present QLoRA ... 4-bit NormalFloat (NF4) ... paged optimizers.</summary></entry>
</feed>"""

KIWIX = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>QLoRA</title><link>/content/wikipedia_en_all_nopic_2026-06/A/QLoRA</link>
  <description>QLoRA uses &lt;b&gt;4-bit&lt;/b&gt; quantization</description></item>
  <item><title>LoRA</title><link>/content/wikipedia_en_all_nopic_2026-06/A/LoRA</link>
  <description>Low-rank adaptation</description></item>
</channel></rss>"""


def upstream(seen: list):
    def handler(request):
        seen.append(request.url.host)
        host = request.url.host
        if request.url.path == "/catalog/v2/entries":
            return httpx.Response(200, text='<feed><entry><link type="text/html" href="/content/wikipedia_en_all_nopic_2026-01"/></entry>'
                                            '<entry><link type="text/html" href="/content/wikipedia_en_all_nopic_2026-06"/></entry>'
                                            '<entry><link type="text/html" href="/content/devdocs_en_python_2026-10"/></entry></feed>')
        if host == "127.0.0.1" and request.url.path == "/search" and request.url.port == 8090:
            assert request.url.params.get_list("books.name") == ["wikipedia_en_all_nopic_2026-06",
                                                                 "devdocs_en_python_2026-10"]
            return httpx.Response(200, text=KIWIX)
        if host == "127.0.0.1" and request.url.port == 8090:
            return httpx.Response(200, text="<html><p>QLoRA backpropagates through a frozen 4-bit model "
                                             "into low rank adapters, with paged optimizers.</p></html>",
                                  headers={"content-type": "text/html"})
        if host == "export.arxiv.org":
            return httpx.Response(200, text=ARXIV)
        if host == "api.openalex.org":
            return httpx.Response(200, json={"results": [{
                "title": "QLoRA", "doi": "https://doi.org/10.1/qlora",
                "abstract_inverted_index": {"QLoRA": [0], "uses": [1], "NF4": [2]}}]})
        if host == "huggingface.co":
            return httpx.Response(200, json=[{"paper": {"id": "2305.14314", "title": "QLoRA", "summary": "s"}}])
        if host == "127.0.0.1" and request.url.port == 8888:
            return httpx.Response(200, json={"results": [{"title": "S", "url": "https://s.example/x", "content": "c"}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def router(tmp_path, **kw):
    return Router(kiwix_url="http://127.0.0.1:8090", kiwix_books=["wikipedia_en_all_nopic", "not_downloaded", "devdocs_en_python"],
                  searxng_url="http://127.0.0.1:8888", cache_dir=tmp_path / "cache", **kw)


def test_queries_go_to_sources_that_suit_them():
    assert pick_live("How does QLoRA reduce memory when fine-tuning large language models?") == [
        "arxiv", "openalex", "hf_papers"]
    assert pick_live("What do the GGUF types in llama.cpp mean?") == ["github", "stackexchange"]
    assert pick_live("What are the main new features in the latest stable Python release?") == [
        "github", "stackexchange", "hackernews"]
    assert pick_live("Self-RAG versus retrieval-augmented generation for llama.cpp") == [
        "openalex", "github", "stackexchange"]   # both kinds: one paper index, two code sources
    assert pick_live("history of the printing press") == ["openalex", "github", "hackernews"]
    assert pick_live("QLoRA paper", allowed=["openalex"]) == ["openalex"]


def test_merge_takes_rank_one_from_every_source_first_and_caps_each():
    a = [{"url": f"a{i}"} for i in range(5)]
    b = [{"url": f"b{i}"} for i in range(5)]
    c = [{"url": "a0"}, {"url": "c1"}]
    assert [h["url"] for h in merge([a, b, c], 5)] == ["a0", "b0", "a1", "b1", "c1"]
    assert abstract({"b": [1], "a": [0], "c": [2, 3]}) == "a b c c"


def test_router_merges_layers_caches_and_offline_stays_on_the_pc(tmp_path):
    seen = []
    query = "How does QLoRA reduce memory when fine-tuning large language models?"
    hits, status = router(tmp_path).search(query, 6, transport=upstream(seen))
    assert [h["engine"] for h in hits][:4] == ["kiwix:wikipedia_en_all_nopic_2026-06", "arxiv", "openalex", "hf_papers"]
    assert {h["layer"] for h in hits} == {"offline", "live", "bonus"}
    assert hits[0]["url"] == "http://127.0.0.1:8090/content/wikipedia_en_all_nopic_2026-06/A/QLoRA"
    assert hits[0]["content"] == "QLoRA uses 4-bit quantization"
    assert {s["source"]: s["outcome"] for s in status}["arxiv"] == "ok"

    seen.clear()   # the same query again comes from the cache
    _, status = router(tmp_path).search(query, 6, transport=upstream(seen))
    assert {s["outcome"] for s in status} == {"cached"} and seen == ["127.0.0.1"]   # only the book list

    seen.clear()
    hits = search_pages("http://127.0.0.1:8888", "QLoRA paged optimizers", k=4, transport=upstream(seen),
                        tavily_key="tvly-never", router=router(tmp_path / "fresh"), offline=True)
    assert set(seen) == {"127.0.0.1"} and {h["layer"] for h in hits} == {"offline"}
    assert "paged optimizers" in hits[0]["text"]   # the article itself, fetched from kiwix-serve


def test_a_failing_source_is_reported_not_raised_and_tavily_is_last(tmp_path):
    def handler(request):
        if request.url.host == "api.tavily.com":
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t.example", "content": "x"}]})
        if request.url.host == "t.example":
            return httpx.Response(404)
        return httpx.Response(429, text="slow down")
    status = []
    hits = search_pages("http://127.0.0.1:8888", "QLoRA paper", k=3, transport=httpx.MockTransport(handler),
                        tavily_key="tvly-k", router=router(tmp_path), status=status)
    assert [h["engine"] for h in hits] == ["tavily"] and hits[0]["layer"] == "cloud"
    assert all(s["outcome"].startswith("error: HTTPStatusError") for s in status)
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").iterdir())   # errors are not cached


def test_search_settings_load_and_reject_unknown_sources(tmp_path):
    base = (tmp_path / "a.toml")
    base.write_text('[tiers.local]\nkind = "local"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "m"\n'
                    '[search]\nkiwix_books = ["devdocs_en_python_2026-10"]\nlive = ["arxiv"]\n', encoding="utf-8")
    settings = load_settings(base)
    assert settings.search.kiwix_books == ["devdocs_en_python_2026-10"] and settings.search.live == ["arxiv"]
    base.write_text(base.read_text(encoding="utf-8").replace('["arxiv"]', '["tavily"]'), encoding="utf-8")
    try:
        load_settings(base)
    except ValueError as exc:
        assert "tavily" in str(exc)
    else:
        raise AssertionError("tavily is not a free public source")
