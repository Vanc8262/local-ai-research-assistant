"""The Stage 2 eval: research questions run local-only and hybrid, each report checked.

For every question in evals/research_questions.toml and every mode, it runs
`run_research`, `verify_run` and `repair_run`, and scores the repaired report on:

- citation coverage and accuracy, from the verifier;
- expected facts, the share of the question's `expect` groups the report mentions;
- time and tokens per tier, from the run log.

The gate for Stage 2 is every claim cited (coverage 100%) and at least 95% of cited
claims supported, with at most 15% of the claims removed to get there, and at least 75%
of the question's expected facts in the report. Removing a failed claim always raises the
citation scores, which is why the removed share and the facts count too. Every question
runs `runs` times (2 by default), because one run swings a lot.

The support check is the local model's verdict plus a second opinion from the decider
(assistant/decide.py): a "supported" it doubts drops to "partial". Free RAM is sampled
every 15 seconds into ram.csv. Results go to runs/eval-<id>/summary.csv and summary.md.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from bench.sysmon import Timeline

from .config import Settings
from .decide import Decider
from .gateway import Gateway
from .repair import RepairError, repair_run
from .research import run_research
from .runlog import RunLog, new_run_id, read_run
from .verify import verify_run

# Modes: (label, tier, search, public). Hybrid writes with Gemini and searches with Tavily.
MODES = {
    "local": (None, "searxng", False),
    "hybrid": ("gemini", "tavily", True),
    "codex": (None, "codex", False),   # local, from the codex papers; only questions marked codex = true
}
GATE_COVERAGE, GATE_ACCURACY, GATE_MAX_REMOVED, GATE_MIN_FACTS = 1.0, 0.95, 0.15, 0.75
RUNS = 2
RELOAD_EVERY = 40   # verification model calls between reloads; a 58-claim report makes ~100
FIELDS = ("question", "mode", "run", "outcome", "research_s", "verify_s", "repair_s", "sources", "claims", "coverage",
          "accuracy", "supported", "partial", "unsupported", "uncited", "facts", "claims_before",
          "coverage_before", "accuracy_before", "facts_before", "cited", "rewritten", "removed",
          "removed_share", "doubted", "decider_errors", "min_free_ram_mb", "local_tokens", "cloud_tokens",
          "gate", "run_id", "error")


@dataclass
class Question:
    id: str
    text: str
    topic: str = ""
    expect: list[list[str]] = None
    codex: bool = False     # the codex holds primary sources for it, so it also runs in codex mode

    def facts_found(self, report: str) -> float | None:
        if not self.expect:
            return None
        lowered = report.lower()
        hits = sum(any(term.lower() in lowered for term in group) for group in self.expect)
        return round(hits / len(self.expect), 3)


def load_questions(path: str | Path) -> list[Question]:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return [Question(**entry) for entry in raw.get("question", [])]


def reload_model(settings: Settings, tier_name: str | None = None, *, run=subprocess.run) -> None:
    """Unload everything in LM Studio and load the tier's model fresh at the research context.

    llama-server's host memory grows through a long session (8.5 GB in 35 minutes during the
    first eval, probably the prompt cache), and 16 GB of RAM cannot carry that for a full eval.
    """
    tier = settings.tier(tier_name or settings.default_tier)
    lms = shutil.which("lms") or str(Path("~/.lmstudio/bin/lms.exe").expanduser())
    run([lms, "unload", "--all"], check=False, capture_output=True)
    run([lms, "load", tier.lms_key or tier.model, "--context-length", str(settings.research.context_window),
         "--gpu", "max", "--identifier", tier.model, "-y"], check=True, capture_output=True)


def run_eval(settings: Settings, questions: list[Question], modes: list[str], *,
             verify_tier: str | None = None, reload: bool = False, runner=None, transport=None,
             reloader=reload_model, runs: int = RUNS, ram_interval_s: float = 15.0) -> tuple[Path, list[dict]]:
    out_dir = Path(settings.runs_dir) / f"eval-{new_run_id()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with Timeline(out_dir / "ram.csv", interval_s=ram_interval_s) as ram:
        for question in questions:
            for run in range(1, runs + 1):
                if reload:
                    reloader(settings, verify_tier)
                for mode in modes:
                    if mode == "codex" and not question.codex:
                        continue
                    ram.label = f"{question.id} {mode} run {run}"
                    ram.reset_low()
                    row = _one(settings, question, mode, verify_tier, runner, transport,
                               (lambda: reloader(settings, verify_tier)) if reload else None, ram)
                    ram.sample()
                    rows.append({**row, "run": run, "min_free_ram_mb": ram.low})
                    write_summary(out_dir, rows, ram.lowest)   # after every run, so a long eval can be read as it goes
    write_summary(out_dir, rows, ram.lowest)
    return out_dir, rows


def _one(settings, question: Question, mode: str, verify_tier, runner, transport, reload, ram) -> dict:
    tier, search, public = MODES[mode]
    log = RunLog(settings.runs_dir, task=f"eval {question.id} ({mode}): {question.text}")
    row = {"question": question.id, "mode": mode, "run_id": log.run_id}
    label, started = ram.label, time.perf_counter()
    ram.label = f"{label}: research"
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
    ram.label = f"{label}: verify"
    gateway = Gateway(settings, log, transport=transport)
    decider = Decider(settings, gateway, log, tier=verify_tier, transport=transport)
    try:   # verification always runs on a local tier, whatever wrote the report
        options = dict(tier=verify_tier, private=True, transport=transport,
                       reload_every=RELOAD_EVERY if reload else 0, reloader=reload, decider=decider)
        verify_run(result.run_dir, gateway, log, **options)
        row["verify_s"] = round(time.perf_counter() - started, 1)
        facts_before = question.facts_found(result.report_path.read_text(encoding="utf-8"))
        started = time.perf_counter()
        ram.label = f"{label}: repair"
        try:
            repair = repair_run(result.run_dir, gateway, log, **options)
        except RepairError as exc:   # the report is back as it was; keep going with the next one
            return {**row, "outcome": "repair_failed", "error": str(exc), **_tokens(log)}
    finally:
        decider.close()
        gateway.close()
        ram.label = label
    row["repair_s"] = round(time.perf_counter() - started, 1)
    summary = repair.after
    coverage, accuracy = summary["citation_coverage"], summary["citation_accuracy"]
    removed_share = round(repair.count("removed") / repair.before["claims"], 3) if repair.before["claims"] else 0
    facts = question.facts_found(result.report_path.read_text(encoding="utf-8"))
    passed = (coverage or 0) >= GATE_COVERAGE and (accuracy or 0) >= GATE_ACCURACY \
        and not summary["dangling_citations"] and removed_share <= GATE_MAX_REMOVED \
        and (facts is None or facts >= GATE_MIN_FACTS)
    row.update(outcome="ok", claims=summary["claims"], coverage=coverage, accuracy=accuracy,
               supported=summary["supported"], partial=summary["partial"],
               unsupported=summary["unsupported"], uncited=summary["uncited"], facts=facts,
               doubted=repair.before["doubted"], decider_errors=summary["decider_errors"],
               claims_before=repair.before["claims"], coverage_before=repair.before["citation_coverage"],
               accuracy_before=repair.before["citation_accuracy"], facts_before=facts_before,
               cited=repair.count("cited"), rewritten=repair.count("rewritten"), removed=repair.count("removed"),
               removed_share=removed_share, gate="pass" if passed else "fail", error=result.error, **_tokens(log))
    return row


def _tokens(log: RunLog) -> dict:
    """Tokens of the research run (not verification), split by where they were spent."""
    totals = {"local_tokens": 0, "cloud_tokens": 0}
    for step in read_run(log.path):
        if step.get("kind") == "model" and step.get("purpose", "").startswith("proxy"):
            key = "local_tokens" if step.get("tier_kind") == "local" else "cloud_tokens"
            totals[key] += (step.get("prompt_tokens") or 0) + (step.get("completion_tokens") or 0)
    return totals


def write_summary(out_dir: Path, rows: list[dict], lowest_ram: tuple | None = None) -> None:
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "summary.md").write_text(summary_markdown(rows, lowest_ram), encoding="utf-8")


def summary_markdown(rows: list[dict], lowest_ram: tuple | None = None) -> str:
    pct = lambda v: "-" if v in (None, "") else f"{float(v):.0%}"
    lines = ["# Stage 2 research eval", "",
             f"Gate: coverage {GATE_COVERAGE:.0%}, accuracy at least {GATE_ACCURACY:.0%}, "
             f"at most {GATE_MAX_REMOVED:.0%} of claims removed, at least {GATE_MIN_FACTS:.0%} of expected facts. "
             "\"Before\" columns are the report as written. \"Doubted\" counts passes the decider overruled. "
             "A pass shows the checker accepts the report, not that every sentence is true.", "",
             "| Question | Mode | Run | Outcome | Sources | Claims before | Coverage before | Accuracy before | "
             "Facts before | Doubted | Cited | Rewritten | Removed | Claims | Coverage | Accuracy | Facts | Research | "
             "Verify | Repair | Min free RAM | Gate |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['question']} | {r['mode']} | {r.get('run', 1)} | {r.get('outcome', '')} | "
                     f"{r.get('sources', '-')} | "
                     f"{r.get('claims_before', '-')} | {pct(r.get('coverage_before'))} | {pct(r.get('accuracy_before'))} | "
                     f"{pct(r.get('facts_before'))} | {r.get('doubted', '-')} | {r.get('cited', '-')} | {r.get('rewritten', '-')} | "
                     f"{r.get('removed', '-')} | {r.get('claims', '-')} | {pct(r.get('coverage'))} | "
                     f"{pct(r.get('accuracy'))} | {pct(r.get('facts'))} | {r.get('research_s', '-')} s | "
                     f"{r.get('verify_s', '-')} s | {r.get('repair_s', '-')} s | {r.get('min_free_ram_mb', '-')} MB | "
                     f"{r.get('gate', '-')} |")
    for mode in dict.fromkeys(r["mode"] for r in rows):
        done = [r for r in rows if r["mode"] == mode and r.get("outcome") == "ok"]
        if done:
            mean = lambda key: sum(float(r[key] or 0) for r in done) / len(done)
            lines.append(f"\n**{mode}:** {len(done)} reports, mean coverage {mean('coverage'):.0%} "
                         f"(before repair {mean('coverage_before'):.0%}), mean accuracy {mean('accuracy'):.0%} "
                         f"(before {mean('accuracy_before'):.0%}), mean facts {mean('facts'):.0%} "
                         f"(before {mean('facts_before'):.0%}), mean claims removed {mean('removed_share'):.0%}, "
                         f"{sum(r['gate'] == 'pass' for r in done)} pass the gate.")
        both = {}
        for r in done:
            both.setdefault(r["question"], []).append(r["gate"] == "pass")
        lines.append(f"Questions that passed on every run: {sum(all(v) for v in both.values())} of {len(both)}.")
    if lowest_ram:
        lines.append(f"\n**Lowest free RAM:** {lowest_ram[0]} MB, during {lowest_ram[1] or 'setup'}. "
                     "Samples every 15 s are in ram.csv.")
    return "\n".join(lines) + "\n"
