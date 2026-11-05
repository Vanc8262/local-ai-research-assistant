"""Build and load the codex search index. Executed by LDR's own Python, not the project's.

LangChain, FAISS and pypdf are installed in LDR's venv, so like ldr_runner.py this
file imports nothing from the assistant package. `assistant codex index` starts it
as a subprocess with the job as JSON on stdin:

    {"codex_dir": "...", "index_dir": "...", "embed_url": "http://127.0.0.1:1234/v1",
     "embed_model": "...", "files": [manifest rows], "out": "path/index.json"}

Each PDF page becomes a document whose source is file:///<pdf>#page=<n>, split into
~1000-character chunks and embedded by LM Studio. The index is built beside the old
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
    keyword = BM25Retriever.from_documents(list(store.docstore._dict.values()), k=k, preprocess_func=terms)
    return hybrid_retriever(vector, keyword, k)


def terms(text: str) -> list[str]:
    """Words and numbers, lower-cased, with numbers split from units: "48GB" -> 48, gb."""
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", text.lower())


def fuse(rankings: list[list], k: int, rrf: int = 60) -> list:
    """Reciprocal rank fusion: a chunk scores 1/(rrf + rank) in each list it appears in."""
    scores, docs = {}, {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking):
            key = (doc.metadata.get("source"), doc.page_content)
            scores[key] = scores.get(key, 0.0) + 1 / (rrf + rank + 1)
            docs.setdefault(key, doc)
    return [docs[key] for key in sorted(scores, key=scores.get, reverse=True)[:k]]


def hybrid_retriever(vector, keyword, k: int):
    from typing import Any

    from langchain_core.retrievers import BaseRetriever

    class HybridRetriever(BaseRetriever):
        vector: Any
        keyword: Any
        k: int

        def _get_relevant_documents(self, query, *, run_manager=None):
            return fuse([self.vector.invoke(query), self.keyword.invoke(query)], self.k)

    return HybridRetriever(vector=vector, keyword=keyword, k=k)


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
        pages = page_documents(codex_dir, row)
        chunks = splitter.split_documents(pages)
        if chunks:
            if store is None:
                store = FAISS.from_documents(chunks, embedder)
            else:
                store.add_documents(chunks)
        files.append({"id": row["id"], "file": row["file"], "sha256": row.get("sha256", ""),
                      "pages": len(pages), "chunks": len(chunks),
                      "elapsed_s": round(time.perf_counter() - started, 1)})
        print(f"indexed {row['file']}: {len(pages)} pages, {len(chunks)} chunks", file=sys.stderr, flush=True)
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
