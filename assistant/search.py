"""Web search through the local SearXNG container (see compose.yaml)."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import httpx

from .runlog import RunLog


@dataclass
class Hit:
    title: str
    url: str
    snippet: str
    engine: str = ""


class SearchError(RuntimeError):
    pass


def search(base_url: str, query: str, log: RunLog, *, limit: int = 8,
           transport: httpx.BaseTransport | None = None) -> list[Hit]:
    """Run one query and log it as a "search" step. Queries leave the PC through
    SearXNG to public search engines, so callers must not put private code in them."""
    started = time.perf_counter()
    error = None
    hits: list[Hit] = []
    try:
        with httpx.Client(timeout=30.0, transport=transport) as http:
            resp = http.get(f"{base_url.rstrip('/')}/search", params={"q": query, "format": "json"})
        if resp.status_code != 200:
            error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        else:
            for item in resp.json().get("results", [])[:limit]:
                hits.append(Hit(title=item.get("title", ""), url=item.get("url", ""),
                                snippet=item.get("content", ""), engine=item.get("engine", "")))
    except (httpx.HTTPError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    log.step("search", tier="searxng", tier_kind="local", query=query,
             outcome="error" if error else "ok", hits=len(hits),
             total_s=round(time.perf_counter() - started, 3), error=error,
             results=[asdict(hit) for hit in hits])
    if error:
        raise SearchError(error)
    return hits
