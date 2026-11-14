"""Build and load the codex search index. Executed by LDR's own Python, not the project's.

LangChain, FAISS and pypdf are installed in LDR's venv, so like ldr_runner.py this
file imports nothing from the assistant package. `assistant codex index` starts it
as a subprocess with the job as JSON on stdin:

    {"codex_dir": "...", "index_dir": "...", "embed_url": "http://127.0.0.1:1234/v1",
     "embed_model": "...", "files": [manifest rows], "out": "path/index.json"}

Each PDF page becomes a document whose source is file:///<pdf>#page=<n>, split into
~1000-character chunks and embedded by LM Studio. A Markdown or text file ("text" items)
becomes one document per heading section, file:///<md>#section=<n>, cut by
`split_text`, which never splits a table row and repeats a table's header in each piece. The index is built beside the old
one and swapped in only when complete. ldr_runner.py calls `load_retriever` to give
LDR the index as its "codex" search engine.

FAISS saves its docstore as a pickle, so load_retriever only ever opens the index
this file wrote into the codex folder.
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
BATCH = 32
META = "index.json"


def embeddings(url: str, model: str):
    from langchain_openai import OpenAIEmbeddings

    class NomicEmbeddings(OpenAIEmbeddings):
        """nomic-embed-text expects task prefixes on documents and queries."""
        def embed_documents(self, texts, chunk_size=None, **kwargs):
            return super().embed_documents(["search_document: " + t for t in texts], chunk_size, **kwargs)

        def embed_query(self, text, **kwargs):
            return super().embed_query("search_query: " + text, **kwargs)

    cls = NomicEmbeddings if "nomic" in model else OpenAIEmbeddings
    return cls(model=model, base_url=url, api_key="lm-studio", check_embedding_ctx_length=False,
               chunk_size=BATCH)


def load_retriever(index_dir: str, url: str, model: str, k: int = 8):
    """Vector search and keyword (BM25) search over the same chunks, merged by reciprocal
    rank. Vector search finds passages that mean the same as the question; BM25 finds the
    exact terms and figures (a "48GB" or a "Q4_K_M") that embeddings blur. The vector side
    uses maximal marginal relevance (relevance weighted 0.7) so one page does not fill
    every slot."""
    from langchain_community.retrievers import BM25Retriever
    from langchain_community.vectorstores import FAISS
    store = FAISS.load_local(index_dir, embeddings(url, model), allow_dangerous_deserialization=True)
    vector = store.as_retriever(search_type="mmr", search_kwargs={"k": k, "fetch_k": 4 * k, "lambda_mult": 0.7})
    chunks = list(store.docstore._dict.values())   # in document order: file, page, position
    keyword = BM25Retriever.from_documents(chunks, k=k, preprocess_func=terms)
    return hybrid_retriever(vector, keyword, k, chunks)


IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[a-z]+\d[a-z0-9]*")


def terms(text: str) -> list[str]:
    """Words and numbers, lower-cased, with numbers split from units: "48GB" -> 48, gb.
    Names such as Q4_K_M, NF4 or FP16 are also kept whole, so a search for Q4_K_M ranks
    the chunks that name it above any chunk holding a q, a 4, a k and an m."""
    text = text.lower()
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", text) + IDENTIFIER_RE.findall(text)


def fuse(rankings: list[list], k: int, rrf: int = 60) -> list:
    """Reciprocal rank fusion: a chunk scores 1/(rrf + rank) in each list it appears in."""
    scores, docs = {}, {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking):
            key = (doc.metadata.get("source"), doc.page_content)
            scores[key] = scores.get(key, 0.0) + 1 / (rrf + rank + 1)
            docs.setdefault(key, doc)
    return [docs[key] for key in sorted(scores, key=scores.get, reverse=True)[:k]]


def join(left: str, right: str) -> str:
    """Append a chunk to the one before it, dropping the overlap the splitter repeated."""
    for size in range(min(len(left), len(right), 2 * CHUNK_OVERLAP), 20, -1):
        if left.endswith(right[:size]):
            return left + right[size:]
    return left + "\n" + right


def widen(hits: list, chunks: list) -> list:
    """Each hit with the chunk before and after it from the same file, so a table or list
    cut by a chunk or page boundary reaches the model whole. A hit already inside an
    earlier hit's window is dropped. The citation stays the hit's own page."""
    where = {(c.metadata.get("source"), c.page_content): i for i, c in enumerate(chunks)}
    out, used = [], set()
    for hit in hits:
        i = where.get((hit.metadata.get("source"), hit.page_content))
        if i is None:
            out.append(hit)
            continue
        if i in used:
            continue
        span = [j for j in (i - 1, i, i + 1) if 0 <= j < len(chunks)
                and chunks[j].metadata.get("codex_id") == hit.metadata.get("codex_id")]
        used.update(span)
        text = chunks[span[0]].page_content
        for j in span[1:]:
            text = join(text, chunks[j].page_content)
        out.append(type(hit)(page_content=text, metadata=hit.metadata))
    return out


PER_DOCUMENT = 3


def pick(hits: list, k: int, per_document: int = PER_DOCUMENT) -> list:
    """The first k hits, at most `per_document` from any one file. Widening drops hits that
    fall inside an earlier hit's window, so the caller fuses 2k candidates and this tops the
    list back up to k; the cap leaves room for a second source on the same question."""
    out, count = [], {}
    for hit in hits:
        doc = hit.metadata.get("codex_id") or hit.metadata.get("source")
        if count.get(doc, 0) < per_document:
            count[doc] = count.get(doc, 0) + 1
            out.append(hit)
        if len(out) == k:
            break
    return out


def hybrid_retriever(vector, keyword, k: int, chunks: list | None = None):
    from typing import Any

    from langchain_core.retrievers import BaseRetriever

    class HybridRetriever(BaseRetriever):
        vector: Any
        keyword: Any
        k: int
        chunks: Any

        def _get_relevant_documents(self, query, *, run_manager=None):
            return pick(widen(fuse([self.vector.invoke(query), self.keyword.invoke(query)], 2 * self.k),
                              self.chunks), self.k)

    return HybridRetriever(vector=vector, keyword=keyword, k=k, chunks=chunks or [])


HEADING_RE = re.compile(r"^#{1,6} ", re.M)


def sections(text: str) -> list[str]:
    """A Markdown file cut at its headings, each heading kept with the text under it."""
    starts = [m.start() for m in HEADING_RE.finditer(text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    parts = [text[a:b].strip() for a, b in zip(starts, starts[1:] + [len(text)])]
    return [p for p in parts if p]


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def blocks(text: str) -> list[tuple[str, list[str]]]:
    """("table", rows) for each run of table rows, ("text", lines) for everything else,
    split at blank lines."""
    out: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        kind = "table" if _is_row(line) else "text"
        if not line.strip():
            out.append(("text", []))
        elif out and out[-1][0] == kind and out[-1][1]:
            out[-1][1].append(line)
        else:
            out.append((kind, [line]))
    return [(kind, lines) for kind, lines in out if lines]


def split_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Chunks of about `size` characters that never cut a table row. A table too long for
    one chunk is split between rows, and each piece starts with the table's header (its
    first row and the |---| line under it), so a value is never separated from the column
    it belongs to. A paragraph too long for one chunk is cut at line, then word, breaks."""
    pieces: list[str] = []
    for kind, lines in blocks(text):
        if kind == "table":
            header = lines[:2] if len(lines) > 1 and set(lines[1].replace("|", "").strip()) <= set("-: ") else lines[:1]
            body, piece = lines[len(header):], list(header)
            for row in body:
                if len(piece) > len(header) and len("\n".join(piece + [row])) > size:
                    pieces.append("\n".join(piece))
                    piece = list(header)
                piece.append(row)
            pieces.append("\n".join(piece))
        else:
            para = "\n".join(lines)
            while len(para) > size:
                cut = max(para.rfind("\n", 0, size), para.rfind(" ", 0, size))
                cut = cut if cut > size // 2 else size
                pieces.append(para[:cut].rstrip())
                para = para[cut:].lstrip()
            pieces.append(para)
    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _metadata(row: dict, path: Path, title: str, anchor: str, number: int) -> dict:
    return {"title": title, "source": f"{path.resolve().as_uri()}#{anchor}={number}",
            "codex_id": row["id"], "page": number, "author": row.get("authors", ""),
            "date": row.get("year", ""), "origin_url": row.get("url", ""),
            "licence": row.get("licence", "")}


def text_chunks(codex_dir: Path, row: dict) -> tuple[int, list]:
    """(sections, chunks) of a Markdown or text file, already cut by split_text."""
    from langchain_core.documents import Document
    path = codex_dir / row["file"]
    parts = sections(path.read_text(encoding="utf-8"))
    name = row.get("title") or path.stem
    chunks = [Document(page_content=chunk,
                       metadata=_metadata(row, path, f"{name} (section {number})", "section", number))
              for number, part in enumerate(parts, start=1) for chunk in split_text(part)]
    return len(parts), chunks


def page_documents(codex_dir: Path, row: dict) -> list:
    from langchain_core.documents import Document
    from pypdf import PdfReader
    path = codex_dir / row["file"]
    docs = []
    for number, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            docs.append(Document(page_content=text, metadata={
                "title": f"{row.get('title') or path.stem} (p. {number})",
                "source": f"{path.resolve().as_uri()}#page={number}",
                "codex_id": row["id"], "page": number, "author": row.get("authors", ""),
                "date": row.get("year", ""), "origin_url": row.get("url", ""),
                "licence": row.get("licence", ""),
            }))
    return docs


def build(job: dict) -> dict:
    from langchain_community.vectorstores import FAISS
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    codex_dir, index_dir = Path(job["codex_dir"]), Path(job["index_dir"])
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    embedder = embeddings(job["embed_url"], job["embed_model"])
    store, files = None, []
    for row in job["files"]:
        started = time.perf_counter()
        if row["file"].endswith(".pdf"):
            pages = page_documents(codex_dir, row)
            chunks = splitter.split_documents(pages)
        else:
            pages, chunks = text_chunks(codex_dir, row)
        if chunks:
            if store is None:
                store = FAISS.from_documents(chunks, embedder)
            else:
                store.add_documents(chunks)
        files.append({"id": row["id"], "file": row["file"], "sha256": row.get("sha256", ""),
                      "pages": pages if isinstance(pages, int) else len(pages), "chunks": len(chunks),
                      "elapsed_s": round(time.perf_counter() - started, 1)})
        print(f"indexed {row['file']}: {files[-1]['pages']} pages, {len(chunks)} chunks", file=sys.stderr, flush=True)
    if store is None:
        raise ValueError("no text found in any codex PDF")

    meta = {"embed_model": job["embed_model"], "chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP,
            "files": files, "chunks": sum(f["chunks"] for f in files)}
    part = index_dir.with_name(index_dir.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    store.save_local(str(part))
    (part / META).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    shutil.rmtree(index_dir, ignore_errors=True)
    part.rename(index_dir)
    return meta


def main() -> int:
    job = json.load(sys.stdin)
    started = time.perf_counter()
    error, meta = None, {}
    try:
        meta = build(job)
    except Exception as exc:   # report any failure to the caller instead of a traceback
        error = f"{type(exc).__name__}: {exc}"
    record = {**meta, "error": error, "elapsed_s": round(time.perf_counter() - started, 1)}
    with open(job["out"], "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
