"""The knowledge codex: a local library of papers and books the research tools can index.

codex/catalog.toml lists what belongs in it, one [[item]] per document, each from
its author's or publisher's own free download. `fetch` downloads the PDFs, and prints
single web pages to PDF with headless Edge or Chrome, into
<codex_dir>/<topic>/<id>.pdf and records each in <codex_dir>/manifest.csv with its
source, licence and SHA-256, so a citation to a codex file traces back to where
it came from. The codex lives outside the repo and counts as private: only local
tiers read it.

`build_index` embeds the downloaded PDFs into <codex_dir>/.index with an embedding
model on LM Studio (see codex_index.py, which runs in LDR's venv), so that
`research --search codex` can cite codex pages.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
import json
import subprocess
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import Settings
from .runlog import RunLog

TOPICS = ("python", "software-engineering", "cloud", "ml", "ai-llm", "llm-agents", "rag",
          "local-models", "data-science")
# pdf: a PDF download; page: one web page, printed to PDF; text: a Markdown or source file kept
# as text (a GitHub pull request is fetched as its title and description); html: a whole doc
# site, skipped
KINDS = ("pdf", "page", "text", "html")
GITHUB_PR_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")
GITHUB_BLOB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
MANIFEST_FIELDS = ("id", "topic", "title", "authors", "year", "url", "licence", "file", "bytes",
                   "sha256", "downloaded_at")
INDEXER = Path(__file__).with_name("codex_index.py")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
USER_AGENT = "local-ai-research-assistant/0.1 (personal research library; one request at a time)"
MAX_BYTES = 200 * 2**20   # no single book or paper in the catalog comes near this
BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "msedge", "chrome", "chromium")


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
        return Path(codex_dir) / self.topic / f"{self.id}.{'md' if self.kind == 'text' else 'pdf'}"


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
    """Download each item that is not already in the manifest. Returns (item, outcome) pairs.

    An item whose file is present and matches its manifest checksum is skipped, so a
    rerun fetches only what is new or failed. A "page" item is printed to PDF by a headless
    browser, so the indexer and citations (file#page=N) treat it like any other PDF. A
    "text" item is saved as Markdown, which keeps tables whole and leaves out page chrome.
    "html" items (whole doc sites) are left for a later crawler.
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
            if item.kind == "html":
                outcomes.append((item, "skipped_html"))
                continue
            if _already_fetched(item, codex_dir, manifest.get(item.id)):
                outcomes.append((item, "present"))
                continue
            if requested and delay_s:
                time.sleep(delay_s)   # one request at a time, spaced out, as arXiv asks
            requested = True
            fetcher = {"pdf": _download, "page": _print_html, "text": _download_text}[item.kind]
            outcome, row = fetcher(client, item, codex_dir)
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
    return _finish(part, item, codex_dir)


def _browser() -> str | None:
    for name in ([os.environ["CODEX_BROWSER"]] if os.environ.get("CODEX_BROWSER") else BROWSERS):
        if Path(name).is_file() or shutil.which(name):
            return name
    return None


def _print_html(client, item: Item, codex_dir: Path) -> tuple[str, dict | None]:
    """Print an HTML page to PDF with headless Edge or Chrome (set CODEX_BROWSER to choose)."""
    browser = _browser()
    if not browser:
        return "no_browser", None
    part = item.path(codex_dir).with_suffix(".pdf.part")
    part.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        "--virtual-time-budget=15000", f"--print-to-pdf={part}", item.url],
                       capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        part.unlink(missing_ok=True)
        return f"error: {type(exc).__name__}", None
    if not part.exists():
        return "print_failed", None
    if part.stat().st_size > MAX_BYTES:
        part.unlink()
        return "too_large", None
    return _finish(part, item, codex_dir)


def text_url(url: str) -> tuple[str, bool]:
    """Where a "text" item's text comes from, and whether that is GitHub's pull request API.
    A pull request's web page is mostly navigation, commit lists and later comments; its
    description is the part worth citing. A GitHub blob page becomes its raw file."""
    if match := GITHUB_PR_RE.match(url):
        owner, repo, number = match.groups()
        return f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}", True
    if match := GITHUB_BLOB_RE.match(url):
        owner, repo, rest = match.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}", False
    return url, False


def _download_text(client: httpx.Client, item: Item, codex_dir: Path) -> tuple[str, dict | None]:
    url, is_pr = text_url(item.url)
    try:
        resp = client.get(url, headers={"Accept": "application/vnd.github+json"} if is_pr else {})
    except httpx.HTTPError as exc:
        return f"error: {type(exc).__name__}", None
    if resp.status_code != 200:
        return f"http_{resp.status_code}", None
    if is_pr:
        try:
            data = resp.json()
        except ValueError:
            return "not_text", None
        text = f"# {data.get('title') or item.title}\n\n{data.get('body') or ''}"
    else:
        text = resp.text
    head = text.lstrip()[:200].lower()
    if not text.strip() or head.startswith(("<!doctype", "<html")):
        return "not_text", None   # a login or landing page instead of the file
    if len(text.encode("utf-8")) > MAX_BYTES:
        return "too_large", None
    path = item.path(codex_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".md.part")
    part.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
    part.replace(path)
    return "downloaded", _row(path, item, codex_dir)


def _row(path: Path, item: Item, codex_dir: Path) -> dict:
    return {
        "id": item.id, "topic": item.topic, "title": item.title, "authors": item.authors,
        "year": item.year, "url": item.url, "licence": item.licence,
        "file": path.relative_to(codex_dir).as_posix(), "bytes": path.stat().st_size,
        "sha256": sha256_of(path), "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _finish(part: Path, item: Item, codex_dir: Path) -> tuple[str, dict | None]:
    path = item.path(codex_dir)
    with part.open("rb") as handle:
        is_pdf = handle.read(5) == b"%PDF-"
    if not is_pdf:   # a login page or an HTML landing page instead of the document
        part.unlink()
        return "not_pdf", None
    part.replace(path)
    return "downloaded", _row(path, item, codex_dir)


class _TooLarge(Exception):
    pass


def index_dir(settings: Settings) -> Path:
    return Path(settings.codex_dir) / ".index"


def embed_url(settings: Settings) -> str:
    """The embedding server. The codex is private, so it must be on this PC."""
    url = settings.research.embed_url or settings.tier(settings.default_tier).base_url
    if urlparse(url).hostname not in LOOPBACK:
        raise ValueError(f"the codex is private; embeddings must come from this PC, not {url}")
    return url


def index_meta(settings: Settings) -> dict:
    path = index_dir(settings) / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def index_is_current(settings: Settings, rows: list[dict]) -> bool:
    meta = index_meta(settings)
    indexed = {f["id"]: f.get("sha256") for f in meta.get("files", [])}
    return (meta.get("embed_model") == settings.research.embed_model
            and indexed == {row["id"]: row["sha256"] for row in rows})


def build_index(settings: Settings, log: RunLog, *, force: bool = False,
                runner: list[str] | None = None) -> dict:
    """Embed every downloaded PDF into the codex index. Returns the indexer's record.

    The index is rebuilt only when the set of PDFs (by checksum) or the embedding
    model has changed, unless force=True. The indexer prints progress per file.
    """
    codex_dir = Path(settings.codex_dir)
    rows = [row for row in read_manifest(codex_dir).values()
            if row["file"].endswith((".pdf", ".md")) and (codex_dir / row["file"]).exists()]
    url = embed_url(settings)
    base = {"n_files": len(rows), "embed_model": settings.research.embed_model}
    if not rows:
        log.step("codex-index", **base, outcome="error", error="no downloaded PDFs")
        return {"error": f"no downloaded PDFs in {codex_dir}; run codex fetch first"}
    if not force and index_is_current(settings, rows):
        log.step("codex-index", **base, outcome="current", chunks=index_meta(settings).get("chunks"))
        return {**index_meta(settings), "error": None, "current": True}

    run_dir = Path(settings.runs_dir) / log.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "codex-index.json"
    job = {"codex_dir": str(codex_dir), "index_dir": str(index_dir(settings)), "embed_url": url,
           "embed_model": settings.research.embed_model, "files": rows, "out": str(out)}
    command = runner or [str(settings.research.ldr_python), str(INDEXER)]
    try:
        # stderr is left on the console: the indexer prints a line per file
        proc = subprocess.run(command, input=json.dumps(job), text=True, encoding="utf-8",
                              stdout=subprocess.PIPE, timeout=settings.research.timeout_s)
        record = (json.loads(out.read_text(encoding="utf-8")) if out.exists()
                  else {"error": f"indexer exited {proc.returncode} without a result"})
    except FileNotFoundError:
        record = {"error": f"LDR's Python was not found at {command[0]} (set [research] ldr_python)"}
    except subprocess.TimeoutExpired:
        record = {"error": f"indexing took longer than {settings.research.timeout_s} s"}
    log.step("codex-index", **base, outcome="error" if record.get("error") else "ok",
             chunks=record.get("chunks"), elapsed_s=record.get("elapsed_s"), error=record.get("error"))
    return record
