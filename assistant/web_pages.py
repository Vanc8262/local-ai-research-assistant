"""Web search that returns page text, not just the search engine's snippet.

SearXNG snippets are about 160 characters, so a local report was written from a few
sentences per source and left expected facts out (Tavily, in hybrid mode, returns about
1,300 characters). This retriever asks SearXNG, fetches each result's page and returns
the passages of it that share most words with the query: the same windows the verifier
later checks the claims against.

The searching happens in the assistant's own process: research.py hands `search_pages`
to the gateway proxy, which serves it at POST /search. The retriever that LDR loads
(`load_retriever`, started by ldr_runner.py) only calls that loopback address. LDR's
egress guard blocks every public host during a run it sees as private, and this way
the search, the Tavily key and the decision about what may leave the PC all stay on
our side of the proxy, where each search is logged as a step.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx

from assistant.verify import best_passages, html_to_text

USER_AGENT = "Mozilla/5.0 (compatible; research-assistant/0.1)"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_PAGE_BYTES = 2 * 2**20
# Engines that scrape no search engine, so they answer when Brave, Google and DuckDuckGo
# have suspended the instance. Weaker on plain-language questions; best for papers.
TOPIC_ENGINES = "arxiv,openalex,mwmbl,wikipedia,github,stackoverflow,hackernews"
SEARCH_TIMEOUT_S = 30.0   # SearXNG waits on slow or rate-limited engines; pages keep the 10 s default
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
    try:
        resp = client.post(TAVILY_URL, json={"query": query, "max_results": k},
                           headers={"Authorization": f"Bearer {key}"}, timeout=SEARCH_TIMEOUT_S)
        resp.raise_for_status()
        return [{"title": r.get("title", ""), "url": r["url"], "content": r.get("content", "")}
                for r in resp.json().get("results", []) if r.get("url")][:k]
    except (httpx.HTTPError, ValueError):
        return []


def searxng_hits(client: httpx.Client, searxng_url: str, query: str, k: int, engines: str = "") -> list[dict]:
    params = {"q": query, "format": "json", **({"engines": engines} if engines else {})}
    try:   # a timeout or an error counts as "no results", so the next source in line is asked
        with _gate:   # one query at a time, spaced out
            time.sleep(max(0.0, _last[0] + MIN_GAP_S - time.monotonic()))
            resp = client.get(f"{searxng_url.rstrip('/')}/search", params=params, timeout=SEARCH_TIMEOUT_S)
            _last[0] = time.monotonic()
        resp.raise_for_status()
        return [h for h in resp.json().get("results", []) if h.get("url")][:k]
    except (httpx.HTTPError, ValueError):
        return []


def search_pages(searxng_url: str, query: str, k: int = 6, transport=None, tavily_key: str = "",
                 router=None, offline: bool = False, status: list | None = None) -> list[dict]:
    """[{title, url, text, engine, layer}]: the top k results, each with its snippet and best
    page passages. A page that cannot be fetched keeps its snippet alone.

    With a `router` (sources.Router, the default from assistant.toml), the offline Kiwix
    copies, the free public APIs and SearXNG's general engines are asked together; `status`
    collects one row per source asked. `offline` keeps every request on this PC.
    Without a router, SearXNG's general engines are asked first, then Tavily when a key was
    passed (research.py passes one only for public questions), then SearXNG's topic engines.
    Either way Tavily is asked only when nothing else answered."""
    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}, transport=transport) as client:
        if router is not None:
            hits, rows = router.search(query, k, offline=offline, transport=transport)
            if status is not None:
                status.extend(rows)
            if not hits and tavily_key and not offline:
                hits = [{**h, "engine": "tavily", "layer": "cloud"} for h in tavily_hits(client, tavily_key, query, k)]
        else:
            hits, engine = searxng_hits(client, searxng_url, query, k), "searxng"
            if not hits and tavily_key:
                hits, engine = tavily_hits(client, tavily_key, query, k), "tavily"
            if not hits:   # last resort, and the only one for a private question
                hits, engine = searxng_hits(client, searxng_url, query, k, engines=TOPIC_ENGINES), "searxng-topic"
            hits = [{**h, "engine": engine, "layer": "cloud" if engine == "tavily" else "bonus"} for h in hits]

        def fetch(hit):
            if offline and urlparse(hit["url"]).hostname not in LOOPBACK:
                return ""   # offline: only kiwix-serve's own pages are fetched
            return page_text(client, hit["url"])
        with ThreadPoolExecutor(max_workers=max(len(hits), 1)) as pool:
            pages = list(pool.map(fetch, hits))
    return [{"title": hit.get("title", ""), "url": hit["url"], "engine": hit["engine"], "layer": hit["layer"],
             "text": "\n...\n".join([hit.get("content", "")] + (best_passages(query, page) if page else []))}
            for hit, page in zip(hits, pages)]


def load_retriever(search_url: str):
    """For LDR: a retriever that asks the gateway proxy's /search (see the module docstring)."""
    from typing import Any

    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    class PageRetriever(BaseRetriever):
        url: str
        transport: Any = None

        def _get_relevant_documents(self, query, *, run_manager=None):
            return [Document(page_content=r["text"], metadata={"title": r["title"], "source": r["url"],
                                                             "engine": r["engine"], "layer": r.get("layer", "")})
                    for r in self._ask(query)]

        def _ask(self, query):
            with httpx.Client(timeout=300.0, transport=self.transport) as client:   # queries queue 4 s apart
                resp = client.post(self.url, json={"query": query})
                resp.raise_for_status()
                return resp.json()["results"]

    return PageRetriever(url=search_url)
