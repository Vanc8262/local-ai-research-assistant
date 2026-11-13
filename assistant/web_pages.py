"""Web search that returns page text, not just the search engine's snippet.

SearXNG snippets are about 160 characters, so a local report was written from a few
sentences per source and left expected facts out (Tavily, in hybrid mode, returns about
1,300 characters). This retriever asks SearXNG, fetches each result's page and returns
the passages of it that share most words with the query: the same windows the verifier
later checks the claims against.

Like codex_index.py it runs in LDR's venv, started by ldr_runner.py, which puts the
repo on sys.path so the page-to-text and passage code in verify.py is shared.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from assistant.verify import best_passages, html_to_text

USER_AGENT = "Mozilla/5.0 (compatible; research-assistant/0.1)"
MAX_PAGE_BYTES = 2 * 2**20
# Engines that scrape no search engine, so they answer when Brave, Google and DuckDuckGo
# have suspended the instance. Weaker on plain-language questions; best for papers.
TOPIC_ENGINES = "arxiv,openalex,mwmbl,wikipedia,github,stackoverflow,hackernews"
TAVILY_URL = "https://api.tavily.com/search"
MIN_GAP_S = 4.0   # between SearXNG queries: LDR fires several at once and Brave answers 429
_gate, _last = threading.Lock(), [0.0]


def page_text(client: httpx.Client, url: str) -> str:
    try:
        resp = client.get(url)
        kind = resp.headers.get("content-type", "")
        if resp.status_code != 200 or len(resp.content) > MAX_PAGE_BYTES:
            return ""
        if "html" in kind:
            return html_to_text(resp.text)
        return " ".join(resp.text.split()) if kind.startswith("text/") else ""
    except httpx.HTTPError:
        return ""


def tavily_hits(client: httpx.Client, key: str, query: str, k: int) -> list[dict]:
    resp = client.post(TAVILY_URL, json={"query": query, "max_results": k},
                       headers={"Authorization": f"Bearer {key}"})
    resp.raise_for_status()
    return [{"title": r.get("title", ""), "url": r["url"], "content": r.get("content", "")}
            for r in resp.json().get("results", []) if r.get("url")][:k]


def searxng_hits(client: httpx.Client, searxng_url: str, query: str, k: int, engines: str = "") -> list[dict]:
    params = {"q": query, "format": "json", **({"engines": engines} if engines else {})}
    with _gate:   # one query at a time, spaced out
        time.sleep(max(0.0, _last[0] + MIN_GAP_S - time.monotonic()))
        resp = client.get(f"{searxng_url.rstrip('/')}/search", params=params)
        _last[0] = time.monotonic()
    resp.raise_for_status()
    return [h for h in resp.json().get("results", []) if h.get("url")][:k]


def search_pages(searxng_url: str, query: str, k: int = 6, transport=None, tavily_key: str = "") -> list[dict]:
    """[{title, url, text, engine}]: the top k results, each with its snippet and best page
    passages. A page that cannot be fetched keeps its snippet alone. If SearXNG's general
    engines have no results (they suspend it under load), Tavily is asked when a key was
    passed, which research.py does only for public questions; failing that, SearXNG's
    topic engines are."""
    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}, transport=transport) as client:
        hits, engine = searxng_hits(client, searxng_url, query, k), "searxng"
        if not hits and tavily_key:
            hits, engine = tavily_hits(client, tavily_key, query, k), "tavily"
        if not hits:   # last resort, and the only one for a private question
            hits, engine = searxng_hits(client, searxng_url, query, k, engines=TOPIC_ENGINES), "searxng-topic"
        with ThreadPoolExecutor(max_workers=max(len(hits), 1)) as pool:
            pages = list(pool.map(lambda h: page_text(client, h["url"]), hits))
    return [{"title": hit.get("title", ""), "url": hit["url"], "engine": engine,
             "text": "\n...\n".join([hit.get("content", "")] + (best_passages(query, page) if page else []))}
            for hit, page in zip(hits, pages)]


def load_retriever(searxng_url: str, k: int = 6, tavily_key: str = ""):
    from typing import Any

    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    class PageRetriever(BaseRetriever):
        url: str
        k: int
        tavily_key: str = ""
        transport: Any = None

        def _get_relevant_documents(self, query, *, run_manager=None):
            return [Document(page_content=r["text"], metadata={"title": r["title"], "source": r["url"],
                                                             "engine": r["engine"]})
                    for r in search_pages(self.url, query, self.k, self.transport, self.tavily_key)]

    return PageRetriever(url=searxng_url, k=k, tavily_key=tavily_key)
