"""Fit checks for a research bench candidate (spec section 6.4): does it run on this PC at all?

A candidate fails fit, and is listed but not ranked, if on any run it didn't load fully on the GPU,
free RAM fell below the floor, one report took too long end to end, or too many runs ended without
a report. The limits come from the bench spec's [fit] table. Whether the model sat fully on the GPU
is read from llama-server's own load line ("offloaded 49/49 layers to GPU") in the llama-swap log;
when that log can't be read (LM Studio, or an older llama-swap) the GPU check is unknown, shown as
`gpu?`, and not counted as a failure.
"""
from __future__ import annotations

import re

import httpx

from .bench_spec import FitLimits

OFFLOAD = re.compile(r"offloaded (\d+)/(\d+) layers to GPU")


def parse_offload(log_text: str) -> tuple[int, int] | None:
    """(layers on the GPU, layers in the model) from the last load line, or None when absent.
    The last one counts: llama-swap keeps every load since it started."""
    found = OFFLOAD.findall(log_text or "")
    if not found:
        return None
    on_gpu, total = found[-1]
    return int(on_gpu), int(total)


def fetch_llama_swap_log(base_url: str, get=httpx.get) -> str:
    """The llama-swap log (GET <root>/logs, where root is base_url without /v1); "" on any error,
    so a missing log makes the GPU check unknown rather than stopping the eval."""
    root = base_url.removesuffix("/").removesuffix("/v1")
    try:
        response = get(f"{root}/logs", timeout=10)
        return response.text if response.status_code == 200 else ""
    except Exception:   # noqa: BLE001 - any failure only means "unknown"
        return ""


def _number(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def report_seconds(row: dict) -> float:
    """Research, verify and repair time of one run; a stage that didn't run counts 0."""
    return sum(_number(row.get(key)) or 0.0 for key in ("research_s", "verify_s", "repair_s"))


def fit_failures(rows: list[dict], limits: FitLimits, offload: tuple[int, int] | None) -> list[str]:
    """The fit checks that fail, in the order gpu, ram, time, errors; empty when it fits.
    rows are the candidate's summary.csv rows (local mode)."""
    failed = []
    if offload is not None and offload[0] < offload[1]:
        failed.append("gpu")
    lows = [low for low in (_number(r.get("min_free_ram_mb")) for r in rows) if low is not None]
    if lows and min(lows) < limits.min_free_ram_mb:
        failed.append("ram")
    if any(r.get("outcome") == "ok" and report_seconds(r) > limits.max_report_minutes * 60 for r in rows):
        failed.append("time")
    if rows and sum(r.get("outcome") != "ok" for r in rows) / len(rows) > limits.max_error_share:
        failed.append("errors")
    return failed
