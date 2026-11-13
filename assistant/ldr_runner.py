"""Run one Local Deep Research job. Executed by LDR's own Python, not the project's.

LDR lives in a separate venv (it needs Python 3.12+ and ~2.5 GB of dependencies),
so `assistant research` starts this file there as a subprocess. It imports nothing
from the assistant package. The job arrives as JSON on stdin:

    {"query": "...", "mode": "quick" | "report", "overrides": {...}, "out": "path/result.json",
     "codex": {"index_dir": "...", "embed_url": "...", "embed_model": "...", "k": 8,
               "min_sources": 10}}

"codex" is optional. When present, the codex index (see codex_index.py) is handed to
LDR as a retriever named "codex", which the overrides select as the search tool. The
result is written to `out` as JSON, with timing and an error field.

After its search iterations LDR asks the model to pick the relevant results from 200-character
previews, and a small local model keeps only a few: in the 12 November GGUF codex runs, 25
retrieved chunks became 3 distinct ones, and the chunk defining k-quants never reached the
report. "min_sources" tops the model's pick up with the next unpicked results, in retrieval
order and without duplicates, so the write-up sees at least that many distinct chunks.
"""
import json
import sys
import time


def _key(result: dict) -> tuple:
    return (result.get("link") or result.get("url") or "",
            (result.get("full_content") or result.get("snippet") or "")[:500])


def top_up(kept: list, candidates: list, minimum: int) -> list:
    """`kept` without duplicates, then unkept candidates in order until there are `minimum`."""
    out, seen = [], set()
    for result in kept:
        if _key(result) not in seen:
            seen.add(_key(result))
            out.append(result)
    for result in candidates:
        if len(out) >= minimum:
            break
        if _key(result) not in seen:
            seen.add(_key(result))
            out.append(result)
    return out


def keep_at_least(minimum: int) -> None:
    """Wrap LDR's final relevance filter so it never leaves fewer than `minimum` results."""
    from local_deep_research.advanced_search_system.filters.cross_engine_filter import CrossEngineFilter
    original = CrossEngineFilter.filter_results

    def filter_results(self, results, query, reorder=None, reindex=None, start_index=0, **kwargs):
        kept = original(self, results, query, reorder=reorder, reindex=reindex,
                        start_index=start_index, **kwargs)
        out = top_up(kept, results, minimum)
        return self._prepare_and_return(out, reindex=True if reindex is None else reindex,
                                        start_index=start_index)

    CrossEngineFilter.filter_results = filter_results


def main() -> int:
    job = json.load(sys.stdin)
    started = time.perf_counter()
    error = None
    result = {}
    try:
        from local_deep_research.api import generate_report, quick_summary
        kwargs = {"settings_override": job["overrides"]}
        if job.get("codex"):
            from codex_index import load_retriever   # beside this file, which Python puts on sys.path
            codex = job["codex"]
            if codex.get("min_sources"):
                keep_at_least(int(codex["min_sources"]))
            kwargs["retrievers"] = {"codex": load_retriever(codex["index_dir"], codex["embed_url"],
                                                            codex["embed_model"], codex.get("k", 8))}
        if job.get("pages"):
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # for `import assistant.verify`
            from web_pages import load_retriever as load_pages
            kwargs["retrievers"] = {"pages": load_pages(job["pages"]["search_url"])}
        run = generate_report if job.get("mode") == "report" else quick_summary
        result = run(job["query"], **kwargs)
    except Exception as exc:   # report any LDR failure to the caller instead of a traceback
        error = f"{type(exc).__name__}: {exc}"
    record = {"result": result, "error": error, "elapsed_s": round(time.perf_counter() - started, 1)}
    with open(job["out"], "w", encoding="utf-8") as handle:
        json.dump(record, handle, default=str, ensure_ascii=False, indent=2)
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
