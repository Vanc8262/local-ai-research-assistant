"""The knowledge codex: a local library of papers and books the research tools can index.

codex/catalog.toml lists what belongs in it, one [[item]] per document, each from
its author's or publisher's own free download. `fetch` downloads the PDFs into
<codex_dir>/<topic>/<id>.pdf and records each in <codex_dir>/manifest.csv with its
source, licence and SHA-256, so a citation to a codex file traces back to where
it came from. The codex lives outside the repo and counts as private: only local
tiers read it.
"""
from __future__ import annotations

import csv
import hashlib
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .runlog import RunLog

TOPICS = ("python", "software-engineering", "cloud", "ml", "ai-llm", "llm-agents", "rag",
          "local-models", "data-science")
KINDS = ("pdf", "html")
MANIFEST_FIELDS = ("id", "topic", "title", "authors", "year", "url", "licence", "file", "bytes",
                   "sha256", "downloaded_at")
USER_AGENT = "local-ai-research-assistant/0.1 (personal research library; one request at a time)"
MAX_BYTES = 200 * 2**20   # no single book or paper in the catalog comes near this


class CatalogError(ValueError):
    """The catalog has an entry the fetcher cannot accept."""


@dataclass
class Item:
    id: str
    topic: str
    title: str
    url: str
    kind: str = "pdf"
    authors: str = ""
    year: int = 0
    licence: str = ""
    bytes: int = 0            # expected size from a HEAD request; 0 if unknown

    def __post_init__(self) -> None:
        if self.topic not in TOPICS:
            raise CatalogError(f"{self.id}: topic must be one of {TOPICS}, not {self.topic!r}")
        if self.kind not in KINDS:
            raise CatalogError(f"{self.id}: kind must be one of {KINDS}, not {self.kind!r}")
        if urlparse(self.url).scheme != "https":
            raise CatalogError(f"{self.id}: only https URLs are fetched, not {self.url!r}")

    def path(self, codex_dir: Path) -> Path:
        return Path(codex_dir) / self.topic / f"{self.id}.pdf"


def load_catalog(path: str | Path) -> list[Item]:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    items = [Item(**entry) for entry in raw.get("item", [])]
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise CatalogError(f"duplicate id {item.id!r}")
        seen.add(item.id)
    return items


def read_manifest(codex_dir: Path) -> dict[str, dict]:
    path = Path(codex_dir) / "manifest.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def write_manifest(codex_dir: Path, rows: dict[str, dict]) -> None:
    path = Path(codex_dir) / "manifest.csv"
    temp = path.with_suffix(".csv.part")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in sorted(rows.values(), key=lambda r: (r["topic"], r["id"])):
            writer.writerow(row)
    temp.replace(path)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(2**20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(items: list[Item], codex_dir: str | Path, log: RunLog, *, only: list[str] | None = None,
          delay_s: float = 3.0, transport=None) -> list[tuple[Item, str]]:
    """Download each PDF item that is not already in the manifest. Returns (item, outcome) pairs.

    An item whose file is present and matches its manifest checksum is skipped, so a
    rerun fetches only what is new or failed. HTML items are left for a later crawler.
    """
    codex_dir = Path(codex_dir)
    codex_dir.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(codex_dir)
    wanted = [item for item in items if not only or item.id in only]
    unknown = set(only or ()) - {item.id for item in items}
    if unknown:
        raise CatalogError(f"not in the catalog: {', '.join(sorted(unknown))}")

    outcomes = []
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                          timeout=httpx.Timeout(120.0, connect=15.0), transport=transport)
    try:
        requested = False
        for item in wanted:
            if item.kind != "pdf":
                outcomes.append((item, "skipped_html"))
                continue
            if _already_fetched(item, codex_dir, manifest.get(item.id)):
                outcomes.append((item, "present"))
                continue
            if requested and delay_s:
                time.sleep(delay_s)   # one request at a time, spaced out, as arXiv asks
            requested = True
            outcome, row = _download(client, item, codex_dir)
            log.step("download", id=item.id, topic=item.topic, url=item.url, outcome=outcome,
                     bytes=row.get("bytes") if row else None, sha256=row.get("sha256") if row else None)
            if row:
                manifest[item.id] = row
                write_manifest(codex_dir, manifest)
            outcomes.append((item, outcome))
    finally:
        client.close()
    return outcomes


def _already_fetched(item: Item, codex_dir: Path, row: dict | None) -> bool:
    path = item.path(codex_dir)
    return bool(row and row["url"] == item.url and path.exists() and sha256_of(path) == row["sha256"])


def _download(client: httpx.Client, item: Item, codex_dir: Path) -> tuple[str, dict | None]:
    path = item.path(codex_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".pdf.part")
    try:
        with client.stream("GET", item.url) as resp:
            if resp.status_code != 200:
                return f"http_{resp.status_code}", None
            size = 0
            with part.open("wb") as handle:
                for block in resp.iter_bytes():
                    size += len(block)
                    if size > MAX_BYTES:
                        raise _TooLarge()
                    handle.write(block)
    except _TooLarge:
        part.unlink(missing_ok=True)
        return "too_large", None
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        return f"error: {type(exc).__name__}", None
    with part.open("rb") as handle:
        is_pdf = handle.read(5) == b"%PDF-"
    if not is_pdf:   # a login page or an HTML landing page instead of the document
        part.unlink()
        return "not_pdf", None
    part.replace(path)
    return "downloaded", {
        "id": item.id, "topic": item.topic, "title": item.title, "authors": item.authors,
        "year": item.year, "url": item.url, "licence": item.licence,
        "file": path.relative_to(codex_dir).as_posix(), "bytes": size, "sha256": sha256_of(path),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class _TooLarge(Exception):
    pass
