"""Run one Local Deep Research job. Executed by LDR's own Python, not the project's.

LDR lives in a separate venv (it needs Python 3.12+ and ~2.5 GB of dependencies),
so `assistant research` starts this file there as a subprocess. It imports nothing
from the assistant package. The job arrives as JSON on stdin:

    {"query": "...", "mode": "quick" | "report", "overrides": {...}, "out": "path/result.json"}

and the result is written to `out` as JSON, with timing and an error field.
"""
import json
import sys
import time


def main() -> int:
    job = json.load(sys.stdin)
    started = time.perf_counter()
    error = None
    result = {}
    try:
        from local_deep_research.api import generate_report, quick_summary
        if job.get("mode") == "report":
            result = generate_report(job["query"], settings_override=job["overrides"])
        else:
            result = quick_summary(job["query"], settings_override=job["overrides"])
    except Exception as exc:   # report any LDR failure to the caller instead of a traceback
        error = f"{type(exc).__name__}: {exc}"
    record = {"result": result, "error": error, "elapsed_s": round(time.perf_counter() - started, 1)}
    with open(job["out"], "w", encoding="utf-8") as handle:
        json.dump(record, handle, default=str, ensure_ascii=False, indent=2)
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
