"""The research bench attempt ledger (results/bench_ledger.csv) and the test-split retest cap.

Every test-split run is recorded here before its first question, and the file is committed, so
the cap can't be dodged by deleting a run folder: runs/ is git-ignored, this file is not. The
rules (evals/bench_v1.toml, rule 1): an identical candidate key never runs the test split twice,
and one candidate GGUF gets at most MAX_TEST_ATTEMPTS test runs per bench version (the first
plus two retests after a miss). A run that ended in an error still counts: it was an attempt.
Dev-split runs are free and never written here. Rows end in a plain newline, like the other CSVs in
results/, so the committed file diffs cleanly on Windows too.
"""
from __future__ import annotations

import csv
from pathlib import Path

DEFAULT_LEDGER = "results/bench_ledger.csv"   # repo-relative; the only ledger the test split accepts
LEDGER_FIELDS = ("bench_version", "candidate", "candidate_key", "gguf_sha256", "git_commit", "split",
                 "eval_id", "status", "started_utc", "note")
MAX_TEST_ATTEMPTS = 3
FINAL_STATUSES = ("passed", "missed", "error")


class RetestLimit(Exception):
    """The test split refuses this candidate under the retest cap."""


def read_ledger(ledger: Path) -> list[dict]:
    path = Path(ledger)
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _test_rows(ledger: Path, version: int) -> list[dict]:
    return [r for r in read_ledger(ledger) if r.get("split") == "test" and r.get("bench_version") == str(version)]


def check_attempt(ledger: Path, version: int, gguf_sha256: str, key: str) -> None:
    rows = _test_rows(ledger, version)
    if any(r.get("candidate_key") == key for r in rows):
        raise RetestLimit(f"candidate key {key} already ran the test split of bench v{version}; "
                          "change the pipeline or the tier settings for a retest")
    same_model = [r for r in rows if r.get("gguf_sha256") == gguf_sha256]
    if len(same_model) >= MAX_TEST_ATTEMPTS:
        raise RetestLimit(f"GGUF {gguf_sha256[:12]} already has {len(same_model)} test runs on bench v{version} "
                          f"(at most {MAX_TEST_ATTEMPTS}: the first and two retests)")


def record_attempt(ledger: Path, row: dict) -> None:
    """Append one attempt with status "started"; writes the header if the file is new or empty."""
    path = Path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    new = not text.strip()
    with path.open("w" if new else "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS, extrasaction="ignore", lineterminator="\n")
        if new:
            writer.writeheader()
        elif not text.endswith("\n"):   # a hand-edited file without a final newline: don't glue rows together
            handle.write("\n")
        writer.writerow({**{field: "" for field in LEDGER_FIELDS}, **row, "status": "started"})


def finish_attempt(ledger: Path, eval_id: str, status: str) -> None:
    """Set the attempt's status. An attempt already marked "error" (a run ended without a checked
    report, or the eval raised) stays "error": the scorecard's passed or missed doesn't erase that."""
    if status not in FINAL_STATUSES:
        raise ValueError(f"attempt status must be one of {', '.join(FINAL_STATUSES)}, not {status!r}")
    rows = read_ledger(ledger)
    matches = [r for r in rows if r.get("eval_id") == eval_id]
    if not matches:
        raise ValueError(f"no attempt for {eval_id} in {ledger}")
    for row in matches:
        if row.get("status") != "error":
            row["status"] = status
    with Path(ledger).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
