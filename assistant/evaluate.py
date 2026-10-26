"""The Stage 2 eval: research questions run local-only and hybrid, each report checked.

For every question in evals/research_questions.toml and every mode, it runs
`run_research` and then `verify_run`, and scores the report on:

- citation coverage and accuracy, from the verifier;
- expected facts, the share of the question's `expect` groups the report mentions;
- time and tokens per tier, from the run log.

The gate for Stage 2 is every claim cited (coverage 100%) and at least 95% of cited
claims supported. Results go to runs/eval-<id>/summary.csv and summary.md.
"""
from __future__ import annotations

import csv
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .gateway import Gateway
from .research import run_research
from .runlog import RunLog, new_run_id, read_run
from .verify import verify_run

# Modes: (label, tier, search, public). Hybrid writes with Gemini and searches with Tavily.
MODES = {
    "local": (None, "searxng", False),
    "hybrid": ("gemini", "tavily", True),
}
GATE_COVERAGE, GATE_ACCURACY = 1.0, 0.95
FIELDS = ("question", "mode", "outcome", "research_s", "verify_s", "sources", "claims", "coverage",
          "accuracy", "supported", "partial", "unsupported", "uncited", "facts", "local_tokens",
          "cloud_tokens", "gate", "run_id", "error")


@dataclass
class Question:
    id: str
    text: str
    topic: str = ""
    expect: list[list[str]] = None

    def facts_found(self, report: str) -> float | None:
        if not self.expect:
            return None
        lowered = report.lower()
        hits = sum(any(term.lower() in lowered for term in group) for group in self.expect)
        return round(hits / len(self.expect), 3)


def load_questions(path: str | Path) -> list[Question]:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return [Question(**entry) for entry in raw.get("question", [])]


def run_eval(settings: Settings, questions: list[Question], modes: list[str], *,
             verify_tier: str | None = None, runner=None, transport=None) -> tuple[Path, list[dict]]:
    out_dir = Path(settings.runs_dir) / f"eval-{new_run_id()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for question in questions:
        for mode in modes:
            rows.append(_one(settings, question, mode, verify_tier, runner, transport))
            write_summary(out_dir, rows)   # after every run, so a long eval can be read as it goes
    return out_dir, rows


def _one(settings, question: Question, mode: str, verify_tier, runner, transport) -> dict:
    tier, search, public = MODES[mode]
    log = RunLog(settings.runs_dir, task=f"eval {question.id} ({mode}): {question.text}")
    row = {"question": question.id, "mode": mode, "run_id": log.run_id}
    started = time.perf_counter()
    try:
        result = run_research(settings, question.text, log, tier=tier, search=search, public=public,
                              runner=runner, transport=transport)
    except Exception as exc:   # a blocked or unready mode is a result too; keep going
        return {**row, "outcome": "not_run", "error": f"{type(exc).__name__}: {exc}"}
    row["research_s"] = round(time.perf_counter() - started, 1)
    row["sources"] = len(result.sources)
    if not result.summary:
        return {**row, "outcome": "no_report", "error": result.error, **_tokens(log)}

    started = time.perf_counter()
    gateway = Gateway(settings, log, transport=transport)
    try:   # verification always runs on a local tier, whatever wrote the report
        summary = verify_run(result.run_dir, gateway, log, tier=verify_tier, private=True,
                             transport=transport).summary()
    finally:
        gateway.close()
    row["verify_s"] = round(time.perf_counter() - started, 1)
    coverage, accuracy = summary["citation_coverage"], summary["citation_accuracy"]
    passed = (coverage or 0) >= GATE_COVERAGE and (accuracy or 0) >= GATE_ACCURACY \
        and not summary["dangling_citations"]
    row.update(outcome="ok", claims=summary["claims"], coverage=coverage, accuracy=accuracy,
               supported=summary["supported"], partial=summary["partial"],
               unsupported=summary["unsupported"], uncited=summary["uncited"],
               facts=question.facts_found(result.report_path.read_text(encoding="utf-8")),
               gate="pass" if passed else "fail", error=result.error, **_tokens(log))
    return row


def _tokens(log: RunLog) -> dict:
    """Tokens of the research run (not verification), split by where they were spent."""
    totals = {"local_tokens": 0, "cloud_tokens": 0}
    for step in read_run(log.path):
        if step.get("kind") == "model" and step.get("purpose", "").startswith("proxy"):
            key = "local_tokens" if step.get("tier_kind") == "local" else "cloud_tokens"
            totals[key] += (step.get("prompt_tokens") or 0) + (step.get("completion_tokens") or 0)
    return totals


def write_summary(out_dir: Path, rows: list[dict]) -> None:
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "summary.md").write_text(summary_markdown(rows), encoding="utf-8")


def summary_markdown(rows: list[dict]) -> str:
    pct = lambda v: "-" if v in (None, "") else f"{float(v):.0%}"
    lines = ["# Stage 2 research eval", "",
             f"Gate: coverage {GATE_COVERAGE:.0%} and accuracy at least {GATE_ACCURACY:.0%}.", "",
             "| Question | Mode | Outcome | Sources | Claims | Coverage | Accuracy | Facts | Research | Verify | Gate |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['question']} | {r['mode']} | {r.get('outcome', '')} | {r.get('sources', '-')} | "
                     f"{r.get('claims', '-')} | {pct(r.get('coverage'))} | {pct(r.get('accuracy'))} | "
                     f"{pct(r.get('facts'))} | {r.get('research_s', '-')} s | {r.get('verify_s', '-')} s | "
                     f"{r.get('gate', '-')} |")
    for mode in dict.fromkeys(r["mode"] for r in rows):
        done = [r for r in rows if r["mode"] == mode and r.get("outcome") == "ok"]
        if done:
            mean = lambda key: sum(float(r[key] or 0) for r in done) / len(done)
            lines.append(f"\n**{mode}:** {len(done)} reports, mean coverage {mean('coverage'):.0%}, "
                         f"mean accuracy {mean('accuracy'):.0%}, mean facts {mean('facts'):.0%}, "
                         f"{sum(r['gate'] == 'pass' for r in done)} pass the gate.")
    return "\n".join(lines) + "\n"
