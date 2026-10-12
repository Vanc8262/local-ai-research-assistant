"""Writing a run to disk and reading runs back.

One run = one folder under results/:
    meta.json            model, settings, machine
    <suite>.jsonl        one line per test item, with the model's raw output
    summary.json         one entry per suite
    manual_review.md     answers that need a human score
    manual_scores.csv    where those scores go
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict
from pathlib import Path

from .suites.base import SuiteResult

MANUAL_COLUMNS = ["suite", "item", "score_1_to_5", "notes"]
RUBRIC = """Score each answer from 1 to 5 in manual_scores.csv.

| Score | Research summary | Vision |
| --- | --- | --- |
| 5 | Every claim is in the sources and cited correctly; answers the question; reads well | Correct and to the point |
| 4 | Accurate, with a minor omission or one loose citation | Correct, with extra or hedged text |
| 3 | Mostly accurate, but misses a key point or cites the wrong source once | Partly correct |
| 2 | Contains a claim that is not in the sources, or several citation errors | Mostly wrong |
| 1 | Largely unsupported or does not answer the question | Wrong or no answer |
"""


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:60]


class RunWriter:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        self.summary: dict[str, dict] = {}

    def write_meta(self, meta: dict) -> None:
        self._dump("meta.json", meta)

    def add_suite(self, result: SuiteResult) -> None:
        record = asdict(result)
        items = record.pop("items")
        with (self.run_dir / f"{result.name}.jsonl").open("w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        self.summary[result.name] = record
        self._dump("summary.json", self.summary)
        self._write_manual_files(result)

    def _dump(self, name: str, data: dict) -> None:
        (self.run_dir / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def _write_manual_files(self, result: SuiteResult) -> None:
        review = [item for item in result.items if item.get("needs_manual_review")]
        if not review:
            return
        md_path, csv_path = self.run_dir / "manual_review.md", self.run_dir / "manual_scores.csv"
        if not md_path.exists():
            md_path.write_text(f"# Manual review\n\n{RUBRIC}\n", encoding="utf-8")
        with md_path.open("a", encoding="utf-8") as handle:
            for item in review:
                handle.write(f"## {result.name} / {item['id']}\n\n**Question:** {item['question']}\n\n"
                             f"**Answer:**\n\n{item['answer'] or '(no answer)'}\n\n"
                             f"Automatic check: {'pass' if item['passed'] else 'fail'}\n\n")
        new_file = not csv_path.exists()
        with csv_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(MANUAL_COLUMNS)
            for item in review:
                writer.writerow([result.name, item["id"], "", ""])


def read_manual_scores(run_dir: Path) -> dict[str, float]:
    """Average of the filled-in scores per suite, for one run."""
    path = run_dir / "manual_scores.csv"
    if not path.exists():
        return {}
    scores: dict[str, list[float]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                value = float(row.get("score_1_to_5") or "")
            except ValueError:
                continue
            if 1 <= value <= 5:
                scores.setdefault(row["suite"], []).append(value)
    return {suite: round(sum(values) / len(values), 2) for suite, values in scores.items()}


def load_runs(results_dir: Path) -> list[dict]:
    runs = []
    for folder in sorted(p for p in results_dir.glob("*") if p.is_dir()):
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runs.append({"run_id": folder.name, "dir": folder, "meta": meta, "summary": summary,
                     "manual": read_manual_scores(folder)})
    return runs
