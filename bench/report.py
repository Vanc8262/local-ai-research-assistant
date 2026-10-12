"""Turn the run folders into one table: a row per run and suite."""
from __future__ import annotations

import csv
from pathlib import Path

from .results import load_runs

COLUMNS = ["run_id", "model", "tag", "context", "suite", "score_pct", "passed", "total",
           "manual_avg_1_to_5", "median_ttft_s", "median_gen_tps", "peak_vram_mb",
           "min_ram_available_mb", "note"]
SUITE_ORDER = ["tools", "code", "research", "vision", "speed", "headroom"]


def _note(suite: str, record: dict) -> str:
    metrics = record.get("metrics", {})
    if record.get("skipped"):
        return f"skipped: {record['skipped']}"
    if suite == "tools":
        categories = ", ".join(f"{name} {count}" for name, count in
                               (metrics.get("pass_by_category") or {}).items())
        return f"valid call format {metrics.get('format_valid_rate', 0):.0%}; {categories}"
    if suite == "code":
        return (f"{metrics.get('passed_first_try')} passed on the first try "
                f"({metrics.get('attempts_allowed')} attempts allowed)")
    if suite == "research":
        return (f"fact recall {metrics.get('mean_fact_recall')}, citation accuracy "
                f"{metrics.get('mean_citation_accuracy')} (automatic part)")
    if suite == "speed":
        return (f"generation {metrics.get('generate_tps')} tok/s; long input first token "
                f"{metrics.get('long_input_ttft_s')} s")
    if suite == "headroom":
        return "; ".join(metrics.get("warnings") or []) or "no warnings"
    return ""


def _with_truncation(note: str, metrics: dict) -> str:
    cut = metrics.get("truncated_replies")
    return f"{note}; {cut} replies cut off by max_tokens".lstrip("; ") if cut else note


def build_rows(results_dir: Path) -> list[dict]:
    rows = []
    for run in load_runs(results_dir):
        meta = run["meta"]
        for suite in sorted(run["summary"], key=lambda s: SUITE_ORDER.index(s) if s in SUITE_ORDER else 99):
            record = run["summary"][suite]
            metrics = record.get("metrics", {})
            score = record.get("score")
            rows.append({
                "run_id": run["run_id"],
                "model": meta.get("model_label") or meta.get("model"),
                "tag": meta.get("tag") or "",
                "context": meta.get("context_length") or "",
                "suite": suite,
                "score_pct": "" if score is None else round(score * 100, 1),
                "passed": record.get("passed", ""),
                "total": record.get("total", ""),
                "manual_avg_1_to_5": run["manual"].get(suite, ""),
                "median_ttft_s": _blank(metrics.get("median_ttft_s")),
                "median_gen_tps": _blank(metrics.get("median_gen_tps")),
                "peak_vram_mb": _blank(metrics.get("peak_vram_used_mb", metrics.get("vram_used_mb"))),
                "min_ram_available_mb": _blank(metrics.get("min_ram_available_mb",
                                                           metrics.get("ram_available_mb"))),
                "note": _with_truncation(_note(suite, record), metrics),
            })
    return rows


def _blank(value: object) -> object:
    return "" if value is None else value


def write_report(results_dir: Path) -> tuple[Path, int]:
    rows = build_rows(results_dir)
    csv_path, md_path = results_dir / "summary.csv", results_dir / "summary.md"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Stage 0 benchmark results", "",
             "One row per run and suite. Scores are automatic; `manual_avg_1_to_5` is filled "
             "from each run's manual_scores.csv.", "",
             "| " + " | ".join(COLUMNS) + " |", "|" + " --- |" * len(COLUMNS)]
    for row in rows:
        lines.append("| " + " | ".join(str(row[c]).replace("|", "/") for c in COLUMNS) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, len(rows)
