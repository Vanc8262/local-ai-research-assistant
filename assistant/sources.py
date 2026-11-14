"""The local search router: web search with no commercial search API.

Three layers answer each query (design: research/ideas/local-search-path-design.md in the
project files, approved by Van on 2026-11-13):

- offline: kiwix-serve on this PC, serving ZIM copies of Wikipedia, the DevDocs manuals and
  Stack Exchange sites. Nothing leaves the PC, and nothing can block it.
- live: free public APIs called directly, each at its own published rate and through an
  on-disk cache: arXiv, OpenAlex, Hugging Face papers, GitHub, Stack Exchange, Hacker News.
  Calling them here instead of through SearXNG matters: SearXNG suspends an engine for an
  hour after one "too many requests" and for a day after a block, and LDR's bursts of
  queries set that off.
- bonus: SearXNG's general engines (Brave, Google, DuckDuckGo). Used when they answer;
  nothing depends on them.

`route` picks two or three live sources from the query's words, asks every chosen source at
once, and merges the lists by reciprocal rank, at most `per_source` hits from each.
`offline=True` asks kiwix-serve alone, so the query never leaves the PC.

The live and bonus layers send the search text to public services, like SearXNG always
has. Keys are optional and read from environment variables, never from a file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET   # Python 3.13's expat refuses entity expansion attacks; no DTDs are loaded
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx

from assistant.verify import html_to_text

USER_AGENT = "local-ai-research-assistant/0.1 (personal research tool; low volume)"
LAYERS = ("offline", "live", "bonus")
LIVE_SOURCES = ("arxiv", "openalex", "hf_papers", "github", "stackexchange", "hackernews")
TIMEOUT_S = 20.0

ACADEMIC = re.compile(r"\b(paper|papers|study|research|model|models|llm|llms|quantiz\w*|train\w*|fine-?tun\w*|"
                      r"lora|qlora|rag|retriev\w*|transformer\w*|neural|learning|attention|benchmark\w*|"
                      r"embedding\w*|agent\w*|diffusion|inference|reasoning|arxiv)\b", re.I)
CODE = re.compile(r"\b(python|library|package|error|exception|api|function|install\w*|docker|kubernetes|"
                  r"aws|azure|gcp|cloud|code|coding|llama\.?cpp|gguf|framework|sql|git|github|pandas|"
                  r"numpy|pytorch|cli|release|version|syntax|compiler|terraform)\b", re.I)
NEWS = re.compile(r"\b(latest|new|newest|release[sd]?|announc\w*|launch\w*|20\d\d)\b", re.I)
STOP = set("""a an and are as at be by can do does did for from how in is it its of on or that the this to
what when which who why with vs versus compared comparison comparisons difference differences differ different
between main their each one these those there into other about more most such like also than then them they
specific specifically technical mechanism current state art practical real world empirical empirically trade
offs off beyond general primary precise detailing across during standard explain explained mean means use used
using work works affect affects performance metrics new latest features feature key exactly particular various
typical typically overall approach approaches method methods way ways compare respect regarding terms well
measurable significant significantly provide provides stable""".split())


# Bump when a client changes how it builds its request, so old cached hits are not reused.
CACHE_VERSION = 3


class RateGate:
    """At most one request per `gap_s` seconds to one host, shared across threads."""

    def __init__(self, gap_s: float) -> None:
        self.gap_s, self._lock, self._last = gap_s, threading.Lock(), 0.0

    def wait(self) -> None:
        with self._lock:
            time.sleep(max(0.0, self._last + self.gap_s - time.monotonic()))
            self._last = time.monotonic()


GATES = {"arxiv": RateGate(3.1), "openalex": RateGate(1.0), "hf_papers": RateGate(1.0),
         "github": RateGate(6.0), "stackexchange": RateGate(1.0), "hackernews": RateGate(0.5),
         "searxng": RateGate(4.0), "kiwix": RateGate(0.0)}


def _technical(word: str) -> bool:
    """Names rather than words: QLoRA, Q4_K_M, llama.cpp, Self-RAG, GGUF, 3.15."""
    return bool(re.search(r"\d|_|\w\.\w|[A-Z]{2}", word))


def keywords(query: str, limit: int = 6) -> list[str]:
    """The query's distinctive words, names first, for search APIs that match every word.
    LDR writes long questions; kiwix-serve and arXiv return nothing for those, and find the
    right pages from two to four of their words (measured on the PC, 2026-11-13)."""
    words = [w.rstrip(".-") for w in re.findall(r"[A-Za-z0-9][\w.+-]*", query)]
    words = [w for w in dict.fromkeys(words) if w and w.lower() not in STOP and len(w) > 1]
    names = [w for w in words if _technical(w)]
    return (names + [w for w in words if w not in names])[:limit]


def pick_live(query: str, allowed: tuple[str, ...] | list[str] = LIVE_SOURCES) -> list[str]:
    """Two or three live sources for this query: papers for research questions, GitHub and
    Stack Exchange for code and tools, Hacker News for news of a release."""
    chosen: list[str] = []
    if ACADEMIC.search(query):
        chosen += ["arxiv", "openalex", "hf_papers"]
    if CODE.search(query):
        chosen += ["github", "stackexchange"]
    if NEWS.search(query):
        chosen.append("hackernews")
    if not chosen:
        chosen = ["openalex", "github", "hackernews"]
    picked = [name for name in dict.fromkeys(chosen) if name in allowed]
    if len(picked) > 3:   # a question that is both: one paper index, then the code sources
        picked = [n for n in picked if n in ("openalex", "hf_papers")][:1] + \
                 [n for n in picked if n not in ("arxiv", "openalex", "hf_papers")][:2]
    return picked


def _hit(title: str, url: str, content: str, engine: str, layer: str) -> dict:
    return {"title": " ".join((title or "").split()), "url": url, "content": " ".join((content or "").split()),
            "engine": engine, "layer": layer}


# --- live sources -------------------------------------------------------------------------------

def arxiv(client: httpx.Client, query: str, k: int) -> list[dict]:
    words = keywords(query, 3)
    atom = "{http://www.w3.org/2005/Atom}"
    tries = [" AND ".join(f"all:{w}" for w in words[:n]) for n in (3, 2) if len(words) >= n] or \
            [f"all:{w}" for w in words[:1]]
    for search in tries:
        resp = client.get("https://export.arxiv.org/api/query",
                          params={"search_query": search, "max_results": k, "sortBy": "relevance"})
        resp.raise_for_status()
        entries = ET.fromstring(resp.text).findall(f"{atom}entry")
        if entries:
            return [_hit(e.findtext(f"{atom}title", ""), e.findtext(f"{atom}id", ""),
                         e.findtext(f"{atom}summary", ""), "arxiv", "live") for e in entries]
        GATES["arxiv"].wait()
    return []


def abstract(inverted: dict | None) -> str:
    """OpenAlex keeps abstracts as {word: [positions]}."""
    if not inverted:
        return ""
    places = sorted((pos, word) for word, positions in inverted.items() for pos in positions)
    return " ".join(word for _, word in places)


def openalex(client: httpx.Client, query: str, k: int) -> list[dict]:
    params = {"search": " ".join(keywords(query, 5)), "per_page": k,
              "select": "id,doi,title,abstract_inverted_index,primary_location"}
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    resp = client.get("https://api.openalex.org/works", params=params)
    resp.raise_for_status()
    out = []
    for work in resp.json().get("results", []):
        location = work.get("primary_location") or {}
        url = location.get("landing_page_url") or work.get("doi") or work.get("id") or ""
        out.append(_hit(work.get("title") or "", url, abstract(work.get("abstract_inverted_index")),
                        "openalex", "live"))
    return out


def hf_papers(client: httpx.Client, query: str, k: int) -> list[dict]:
    resp = client.get("https://huggingface.co/api/papers/search", params={"q": " ".join(keywords(query, 4))})
    resp.raise_for_status()
    out = []
    for item in resp.json()[:k]:
        paper = item.get("paper", item) if isinstance(item, dict) else {}
        if paper.get("id"):
            out.append(_hit(paper.get("title", ""), f"https://huggingface.co/papers/{paper['id']}",
                            paper.get("summary", ""), "hf_papers", "live"))
    return out


def github(client: httpx.Client, query: str, k: int) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    words = keywords(query, 4)
    items = []
    for n in range(len(words), max(len(words) - 3, 0), -1):   # GitHub matches every word: drop the last until it finds repos
        if n < len(words):
            GATES["github"].wait()
        resp = client.get("https://api.github.com/search/repositories",
                          params={"q": " ".join(words[:n]), "per_page": k}, headers=headers)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items or n <= 2:
            break
    return [_hit(r.get("full_name", ""), r.get("html_url", ""), r.get("description") or "", "github", "live")
            for r in items]


def stackexchange(client: httpx.Client, query: str, k: int) -> list[dict]:
    params = {"q": " ".join(keywords(query, 6)), "site": "stackoverflow", "pagesize": k,
              "order": "desc", "sort": "relevance", "filter": "withbody"}
    if os.environ.get("STACKEXCHANGE_KEY"):
        params["key"] = os.environ["STACKEXCHANGE_KEY"]
    resp = client.get("https://api.stackexchange.com/2.3/search/advanced", params=params)
    resp.raise_for_status()
    data = resp.json()
    if data.get("backoff"):   # the API asks callers to wait this long before the next call
        GATES["stackexchange"].gap_s = max(GATES["stackexchange"].gap_s, float(data["backoff"]))
    return [_hit(html_to_text(q.get("title", "")), q.get("link", ""), html_to_text(q.get("body", ""))[:1500],
                 "stackexchange", "live") for q in data.get("items", [])]


def hackernews(client: httpx.Client, query: str, k: int) -> list[dict]:
    resp = client.get("https://hn.algolia.com/api/v1/search",
                      params={"query": " ".join(keywords(query, 6)), "hitsPerPage": k, "tags": "story"})
    resp.raise_for_status()
    out = []
    for hit in resp.json().get("hits", []):
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        out.append(_hit(hit.get("title", ""), url, html_to_text(hit.get("story_text") or ""),
                        "hackernews", "live"))
    return out


LIVE = {"arxiv": arxiv, "openalex": openalex, "hf_papers": hf_papers, "github": github,
        "stackexchange": stackexchange, "hackernews": hackernews}


# --- offline and bonus layers --------------------------------------------------------------------

def kiwix(client: httpx.Client, base_url: str, books: list[str], query: str, k: int) -> list[dict]:
    """kiwix-serve's full-text search over the given ZIM files at once (all English, as it
    requires), ranked by kiwix-serve. Its snippets are weak, so callers fetch the article
    itself, also from kiwix-serve on this PC."""
    words = keywords(query, 3)
    root = None
    for n in range(len(words), 0, -1):   # kiwix-serve matches every word: drop the last until it finds pages
        params = [("books.name", book) for book in books] + [("pattern", " ".join(words[:n])), ("format", "xml"),
                                                             ("pageLength", str(k))]
        resp = client.get(f"{base_url.rstrip('/')}/search", params=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.find(".//item") is not None:
            break
    if root is None:
        return []
    out = []
    for item in root.iter("item"):
        link = item.findtext("link") or ""
        book = link.split("/content/", 1)[-1].split("/", 1)[0] if "/content/" in link else ""
        out.append(_hit(item.findtext("title", ""), urljoin(base_url.rstrip("/") + "/", link.lstrip("/")),
                        html_to_text(item.findtext("description") or ""), f"kiwix:{book}" if book else "kiwix",
                        "offline"))
    return out[:k]


def searxng(client: httpx.Client, base_url: str, query: str, k: int) -> list[dict]:
    resp = client.get(f"{base_url.rstrip('/')}/search", params={"q": query, "format": "json"}, timeout=10.0)
    resp.raise_for_status()
    return [_hit(h.get("title", ""), h["url"], h.get("content", ""), "searxng", "bonus")
            for h in resp.json().get("results", []) if h.get("url")][:k]


# --- cache and router ----------------------------------------------------------------------------

@dataclass
class Router:
    kiwix_url: str = ""
    kiwix_books: list[str] = field(default_factory=list)
    live: list[str] = field(default_factory=lambda: list(LIVE_SOURCES))
    searxng_url: str = ""          # empty: no bonus layer
    cache_dir: Path | None = None
    cache_days: float = 7.0
    per_source: int = 2
    _resolved: list[str] | None = field(default=None, init=False, repr=False)

    def books(self, client: httpx.Client) -> list[str]:
        """The configured books as kiwix-serve names them. kiwix_books may give a name without
        its date ("devdocs_en_python"); the newest served file starting with it is used, so a
        monthly ZIM refresh needs no config change. Asked once per Router."""
        if self._resolved is None:
            served: list[str] = []
            try:
                resp = client.get(f"{self.kiwix_url.rstrip('/')}/catalog/v2/entries", params={"count": "-1"})
                resp.raise_for_status()
                served = sorted(set(re.findall(r"/content/([^/\"?#]+)", resp.text)))
            except (httpx.HTTPError, ValueError):
                pass   # kiwix-serve not running: the offline layer is simply empty this time
            if not served:
                return []
            picked = []
            for want in self.kiwix_books:
                matches = [name for name in served if name == want or name.startswith(want + "_")]
                if matches:
                    picked.append(matches[-1])   # names end in _YYYY-MM, so the last is the newest
            self._resolved = picked
        return self._resolved

    def _cached(self, name: str, query: str, k: int, ask) -> tuple[list[dict], str]:
        """(hits, outcome). Outcome is "ok", "cached", "empty" or "error: ..."."""
        path = None
        if self.cache_dir:
            scope = ",".join(self._resolved or []) if name == "kiwix" else ""   # new ZIMs: new results
            digest = hashlib.sha256(f"{CACHE_VERSION}\n{name}\n{scope}\n{' '.join(query.lower().split())}\n{k}".encode()).hexdigest()[:24]
            path = Path(self.cache_dir) / f"{name}-{digest}.json"
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if time.time() - saved["at"] < self.cache_days * 86400:
                    return saved["hits"], "cached"
            except (OSError, ValueError, KeyError):
                pass
        try:
            hits = ask()
        except (httpx.HTTPError, ET.ParseError, ValueError, KeyError, TypeError) as exc:
            return [], f"error: {type(exc).__name__}: {str(exc)[:200]}"
        if path and hits:   # an empty answer may be a block; ask again next time
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"at": time.time(), "query": query, "hits": hits}), encoding="utf-8")
        return hits, "ok" if hits else "empty"

    def jobs(self, query: str, k: int, offline: bool, client: httpx.Client) -> list[tuple[str, object]]:
        """(name, search taking the client) for every source this query goes to, offline first."""
        out = []
        books = self.books(client) if self.kiwix_url and self.kiwix_books else []
        if books:   # one search over every book, so kiwix-serve ranks them against each other
            out.append(("kiwix", lambda c: kiwix(c, self.kiwix_url, books, query, k)))
        if not offline:
            for name in pick_live(query, self.live):
                out.append((name, (lambda n=name: lambda c: LIVE[n](c, query, k))()))
            if self.searxng_url:
                out.append(("searxng", lambda c: searxng(c, self.searxng_url, query, k)))
        return out

    def search(self, query: str, k: int, *, offline: bool = False, transport=None) -> tuple[list[dict], list[dict]]:
        """(merged hits, one status row per source asked)."""
        with httpx.Client(timeout=httpx.Timeout(TIMEOUT_S, connect=5.0), follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}, transport=transport) as client:
            jobs = self.jobs(query, k, offline, client)
            if not jobs:
                return [], []

            def run(job):
                name, ask = job
                started = time.perf_counter()

                def gated():
                    GATES[name].wait()
                    return ask(client)
                hits, outcome = self._cached(name, query, k, gated)
                return name, hits, {"source": name, "outcome": outcome, "hits": len(hits),
                                    "s": round(time.perf_counter() - started, 2)}
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                answers = list(pool.map(run, jobs))
        merged = merge([on_topic(query, hits) for _, hits, _ in answers], k, self.per_source)
        return merged, [status for _, _, status in answers]


def on_topic(query: str, hits: list[dict]) -> list[dict]:
    """Drop hits that mention none of the query's names (AWS, Q4_K_M, Self-RAG, ...) in their
    title or snippet, so a keyword search that fell back to common words ("pillars") can't
    fill the slots with unrelated pages. Queries without names keep every hit."""
    names = [w.lower() for w in keywords(query, 6) if _technical(w)]
    if not names:
        return hits
    parts = {p for name in names for p in re.split(r"[-.]", name) if len(p) >= 3}
    pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, sorted(set(names) | parts, key=len, reverse=True)))
                         + r")(?![a-z0-9])")
    return [h for h in hits if pattern.search(f"{h.get('title', '')} {h.get('content', '')}".lower())]


def merge(lists: list[list[dict]], k: int, per_source: int = 2, rrf: int = 60) -> list[dict]:
    """Reciprocal rank fusion over the sources' lists (earlier lists win ties), at most
    `per_source` hits from each source and one hit per URL."""
    scored = []
    for order, hits in enumerate(lists):
        for rank, hit in enumerate(hits[:per_source]):
            scored.append((1 / (rrf + rank + 1), -order, -rank, hit))
    out, seen = [], set()
    for *_, hit in sorted(scored, key=lambda s: s[:3], reverse=True):
        if hit["url"] and hit["url"] not in seen:
            seen.add(hit["url"])
            out.append(hit)
    return out[:k]
