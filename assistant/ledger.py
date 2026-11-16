"""The research bench attempt ledger (results/bench_ledger.csv) and the test-split retest cap.

Every test-split run is recorded here before its first question, and the file is committed, so
the cap can't be dodged by deleting a run folder: runs/ is git-ignored, this file is not. The
rules (evals/bench_v1.toml, rule 1): an identical candidate key never runs the test split twice,
and one candidate GGUF gets at most MAX_TEST_ATTEMPTS test runs per bench version (the first
plus two retests after a miss). A run that ended in an error still counts: it was an attempt.
Dev-split runs are free and never written here. Rows end in a plain newline, like the other CSVs in
results/, so the committed file diffs cleanly on Windows too.

Only "started" changes: passed, missed and error are final (AttemptFinal otherwise), so a miss can't
be turned into a pass. Each write goes to a temporary file beside the ledger that then replaces it,
so a ledger held open elsewhere (Excel on Windows) raises OSError and is left as it was.
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path

DEFAULT_LEDGER = "results/bench_ledger.csv"   # repo-relative; the only ledger the test split accepts
LEDGER_FIELDS = ("bench_version", "candidate", "candidate_key", "gguf_sha256", "git_commit", "split",
                 "eval_id", "status", "started_utc", "note")
MAX_TEST_ATTEMPTS = 3
FINAL_STATUSES = ("passed", "missed", "error")


class RetestLimit(Exception):
    """The test split refuses this candidate under the retest cap."""


class AttemptFinal(ValueError):
    """An attempt already has another final status; final statuses never change."""


def _same_hash(a: str | None, b: str | None) -> bool:
    """sha256 hex digests compared as PowerShell's Get-FileHash (upper case) and Python print them."""
    return (a or "").strip().lower() == (b or "").strip().lower()


def _write_atomic(path: Path, text: str) -> None:
    """Write text to a temporary file in the same folder, then move it over path in one step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _rows_text(rows: list[dict], header: bool = True) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=LEDGER_FIELDS, extrasaction="ignore", lineterminator="\n")
    if header:
        writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def read_ledger(ledger: Path) -> list[dict]:
    path = Path(ledger)
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def committed_eval_ids(committed_ledger: str | None) -> set[str]:
    """The eval ids in the ledger's text as committed at HEAD (git show HEAD:results/bench_ledger.csv)."""
    if not committed_ledger:
        return set()
    return {r.get("eval_id", "") for r in csv.DictReader(io.StringIO(committed_ledger))}


def _test_rows(ledger: Path, version: int) -> list[dict]:
    return [r for r in read_ledger(ledger) if r.get("split") == "test" and r.get("bench_version") == str(version)]


def check_attempt(ledger: Path, version: int, gguf_sha256: str, key: str) -> None:
    rows = _test_rows(ledger, version)
    if any(r.get("candidate_key") == key for r in rows):
        raise RetestLimit(f"candidate key {key} already ran the test split of bench v{version}; "
                          "change the pipeline or the tier settings for a retest")
    same_model = [r for r in rows if _same_hash(r.get("gguf_sha256"), gguf_sha256)]
    if len(same_model) >= MAX_TEST_ATTEMPTS:
        raise RetestLimit(f"GGUF {gguf_sha256[:12]} already has {len(same_model)} test runs on bench v{version} "
                          f"(at most {MAX_TEST_ATTEMPTS}: the first and two retests)")


def record_attempt(ledger: Path, row: dict) -> None:
    """Append one attempt with status "started"; writes the header if the file is new or empty.
    The rows already there are kept byte for byte (the committed-prefix check compares them)."""
    path = Path(ledger)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if not text.strip():
        text = _rows_text([])
    elif not text.endswith("\n"):   # a hand-edited file without a final newline: don't glue rows together
        text += "\n"
    text += _rows_text([{**{field: "" for field in LEDGER_FIELDS}, **row, "status": "started"}], header=False)
    _write_atomic(path, text)


def finish_attempts(ledger: Path, statuses: dict[str, str]) -> None:
    """Set the final status of several attempts in one write, or of none: every eval id must be in
    the ledger, and an attempt that already has a final status keeps it (the same status again is a
    no-op; another one raises AttemptFinal before anything is written)."""
    for status in statuses.values():
        if status not in FINAL_STATUSES:
            raise ValueError(f"attempt status must be one of {', '.join(FINAL_STATUSES)}, not {status!r}")
    rows = read_ledger(ledger)
    changed = False
    for eval_id, status in statuses.items():
        matches = [r for r in rows if r.get("eval_id") == eval_id]
        if not matches:
            raise ValueError(f"no attempt for {eval_id} in {ledger}")
        for row in matches:
            now = row.get("status", "")
            if now == status:
                continue
            if now in FINAL_STATUSES:
                raise AttemptFinal(f"attempt {eval_id} is already {now} in {ledger}; a final status is not "
                                   f"changed to {status}")
            row["status"] = status
            changed = True
    if changed:
        _write_atomic(Path(ledger), _rows_text(rows))


def finish_attempt(ledger: Path, eval_id: str, status: str) -> None:
    """Set one attempt's final status (see finish_attempts)."""
    finish_attempts(ledger, {eval_id: status})
