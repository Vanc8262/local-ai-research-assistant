"""The Stage 2 eval: research questions run local-only and hybrid, each report checked.

For every question in evals/research_questions.toml and every mode, it runs
`run_research`, `verify_run` and `repair_run`, and scores the repaired report on:

- citation coverage and accuracy, from the verifier;
- expected facts, the share of the question's `expect` groups the report mentions;
- time and tokens per tier, from the run log.

The gate for Stage 2, on the repaired report (GATE_* below; Van set these on 2026-11-14 after
the eval review, reports/eval-review-2026-11-14.md):

- every claim cited (coverage 100%), at least 95% of checked claims supported, no claim left
  unchecked, and no citation to a missing source;
- at least 6 claims, so a near-empty report can't pass;
- at least 80% of claims supported before repair, because repair removes failures and so
  raises accuracy by construction;
- at most 15% of the claims, and at most 5 claims, removed or rewritten to get there;
- at least 75% of the question's expected facts in the report body (not the title, which is
  the question, and not the Sources list). A fact matches as whole words, so "security" no
  longer matches "insecurity".

Every question runs `runs` times (2 by default), because one run swings a lot.

The support check is the local model's verdict plus a second opinion from the decider
(assistant/decide.py): a "supported" it doubts drops to "partial". Free RAM is sampled
every 15 seconds into ram.csv. Results go to runs/eval-<id>/summary.csv and summary.md, and
manifest.json records what the eval ran with (assistant/manifest.py).

With `--bench`, the eval runs one candidate on one split of the frozen research bench
(evals/bench_v1.toml) and is judged by the pinned referee and `scorecard` instead of this
gate; see "Research bench" in the README.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from .sysmon import Timeline

from .config import Settings
from .decide import Decider
from .gateway import Gateway
from .manifest import build_manifest, write_manifest
from .repair import RepairError, repair_run
from .research import run_research
from .runlog import RunLog, new_run_id, read_run
from .snapshot import SearchSnapshot
from .stats import paired, wilson
from .verify import verify_run

# Modes: (label, tier, search, public). Hybrid writes with Gemini and searches with Tavily.
MODES = {
    "local": (None, "searxng", False),
    "hybrid": ("gemini", "tavily", True),
    "codex": (None, "codex", False),   # local, from the codex papers; only questions marked codex = true
    "offline": (None, "offline", False),   # local, Kiwix copies only: nothing leaves the PC (reported, not gated)
}
GATE_COVERAGE, GATE_ACCURACY, GATE_MAX_REMOVED, GATE_MIN_FACTS = 1.0, 0.95, 0.15, 0.75
GATE_MIN_CLAIMS = 6             # claims in the repaired report
GATE_MIN_ACCURACY_BEFORE = 0.8  # supported share of checked claims before repair
GATE_MAX_CHANGED = 5            # claims removed or rewritten by repair; the share cap is GATE_MAX_REMOVED
RUNS = 2
RELOAD_EVERY = 40   # verification model calls between reloads; a 58-claim report makes ~100
FIELDS = ("question", "mode", "run", "outcome", "research_s", "verify_s", "repair_s", "sources", "claims", "coverage",
          "accuracy", "supported", "partial", "unsupported", "uncited", "facts", "claims_before",
          "coverage_before", "accuracy_before", "facts_before", "cited", "rewritten", "removed",
          "removed_share", "changed", "changed_share", "unchecked", "gate_failures", "doubted", "decider_errors", "min_free_ram_mb", "local_tokens", "cloud_tokens",
          "gate", "run_id", "error", "tavily_sources", "offline_sources", "live_sources", "bonus_sources",
          "snapshot_recorded", "snapshot_exact", "snapshot_nearest", "snapshot_missed")


@dataclass
class Question:
    id: str
    text: str
    topic: str = ""
    expect: list[list[str]] = None
    codex: bool = False     # the codex holds primary sources for it, so it also runs in codex mode
    public: bool = False    # holds nothing private: may run in hybrid mode and use the Tavily fallback

    def facts_found(self, report: str, exact: bool = True) -> float | None:
        """Share of the expected facts the report's body states. The title (the question
        itself) and the Sources list don't count, or a fact named in the question or in a
        source title would score for free."""
        if not self.expect:
            return None
        body = report_body(report)
        if exact:
            hits = sum(any(fact_pattern(term).search(body) for term in group) for group in self.expect)
        else:   # the substring match used up to 2026-11-14, for rescoring old evals side by side
            hits = sum(any(term.lower() in body.lower() for term in group) for group in self.expect)
        return round(hits / len(self.expect), 3)


def report_body(report: str) -> str:
    """The report without its "# <question>" title line and its "## Sources" list."""
    lines = report.split("\n## Sources", 1)[0].splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines)


def fact_pattern(term: str) -> re.Pattern:
    """A fact as whole words, case-insensitive, with an optional plural "s"."""
    return re.compile(rf"(?<!\w){re.escape(term)}s?(?!\w)", re.IGNORECASE)


def gate_failures(after: dict, before: dict, *, removed: int, rewritten: int, facts: float | None) -> list[str]:
    """Why a repaired report fails the Stage 2 gate; empty when it passes. `after` and
    `before` are verification summaries of the repaired and the original report."""
    claims_before = before["claims"] or 0
    changed = removed + rewritten
    checks = [
        ((after["citation_coverage"] or 0) >= GATE_COVERAGE, "coverage"),
        ((after["citation_accuracy"] or 0) >= GATE_ACCURACY, "accuracy"),
        (not after.get("unchecked"), "unchecked"),
        (not after["dangling_citations"], "dangling"),
        ((after["claims"] or 0) >= GATE_MIN_CLAIMS, "few_claims"),
        ((before["citation_accuracy"] or 0) >= GATE_MIN_ACCURACY_BEFORE, "accuracy_before"),
        ((changed / claims_before if claims_before else 0) <= GATE_MAX_REMOVED, "changed_share"),
        (changed <= GATE_MAX_CHANGED, "changed_count"),
        (facts is None or facts >= GATE_MIN_FACTS, "facts"),
    ]
    return [name for ok, name in checks if not ok]


def passes_old_gate(after: dict, removed_share: float, facts: float | None) -> bool:
    """The gate as it was up to 2026-11-14, kept so `rescore` can show both side by side."""
    return (after["citation_coverage"] or 0) >= GATE_COVERAGE and (after["citation_accuracy"] or 0) >= GATE_ACCURACY \
        and not after["dangling_citations"] and removed_share <= GATE_MAX_REMOVED \
        and (facts is None or facts >= GATE_MIN_FACTS)


def load_questions(path: str | Path) -> list[Question]:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return [Question(**entry) for entry in raw.get("question", [])]


def reload_model(settings: Settings, tier_name: str | None = None, *, run=subprocess.run, post=None) -> None:
    """Drop the loaded model so the next call starts from a fresh load.

    LM Studio: unload everything, then `lms load` the tier's model at the research context.
    llama-swap: ask it to unload; it starts the model again on the next request, with the
    flags in services/llama-swap/config.yaml.

    Under LM Studio, llama-server's host memory grows through a long session (6 to 12 GB),
    because LM Studio leaves its 8 GiB prompt cache on. The llama-swap config turns that cache
    off (-cram 0), which held it flat at about 4.6 GB in the 2026-11-11 test.
    """
    tier = settings.tier(tier_name or settings.default_tier)
    if settings.model_server == "llama-swap":
        _llama_swap_unload(tier.base_url, post or httpx.post)
        return
    lms = shutil.which("lms") or str(Path("~/.lmstudio/bin/lms.exe").expanduser())
    run([lms, "unload", "--all"], check=False, capture_output=True)
    run([lms, "load", tier.lms_key or tier.model, "--context-length", str(settings.research.context_window),
         "--gpu", "max", "--identifier", tier.model, "-y"], check=True, capture_output=True)


def _llama_swap_unload(base_url: str, post) -> None:
    root = base_url.removesuffix("/v1")
    response = post(f"{root}/api/models/unload", timeout=60)
    if response.status_code == 404:   # older llama-swap builds
        response = post(f"{root}/unload", timeout=60)
    response.raise_for_status()


def run_eval(settings: Settings, questions: list[Question], modes: list[str], *,
             verify_tier: str | None = None, reload: bool = False, runner=None, transport=None,
             reloader=reload_model, runs: int = RUNS, ram_interval_s: float = 15.0,
             questions_path: str | Path | None = None, snapshot_dir: str | Path | None = None,
             snapshot_mode: str = "record", candidate_tier: str | None = None, bench: dict | None = None,
             eval_id: str | None = None,
             offload_probe: Callable[[], tuple[int, int] | None] | None = None) -> tuple[Path, list[dict]]:
    """snapshot_dir: record the page searches of local and offline modes there, one folder per
    question and mode (snapshot_mode "record"), or answer them from an earlier recording
    ("replay"; see assistant/snapshot.py).

    candidate_tier: the research bench candidate (a local tier). It writes in the local, codex and
    offline modes, and unless verify_tier names another tier it also checks, decides and repairs,
    so the score is the candidate's whole pipeline. Hybrid still writes with Gemini (a reference
    row) and checks with the candidate. bench is saved in manifest.json under "bench". eval_id
    names the folder (eval-<id>), so a caller can record the attempt before the first question.

    offload_probe: called once after the first question, while the candidate is still loaded, for
    (layers on the GPU, layers in the model) or None when unknown; saved in manifest.json as
    bench.offload for the fit check (assistant/fit.py). A probe that raises counts as unknown.
    Only a bench eval (bench given) is probed."""
    if candidate_tier is not None and settings.tier(candidate_tier).kind != "local":
        raise ValueError(f"candidate tier {candidate_tier!r} is not local; bench candidates run on this PC")
    verify_tier = verify_tier or candidate_tier
    out_dir = Path(settings.runs_dir) / f"eval-{eval_id or new_run_id()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, modes=modes, runs=runs, question_ids=[q.id for q in questions],
                              questions_path=questions_path, bench=bench)
    write_manifest(out_dir, manifest)
    rows = []
    with Timeline(out_dir / "ram.csv", interval_s=ram_interval_s) as ram:
        for number, question in enumerate(questions):
            for run in range(1, runs + 1):
                if reload:
                    reloader(settings, verify_tier)
                for mode in modes:
                    if mode == "codex" and not question.codex:
                        continue
                    if MODES[mode][2] and not question.public:
                        continue   # a private question never runs on a cloud mode
                    ram.label = f"{question.id} {mode} run {run}"
                    ram.reset_low()
                    snapshot = None
                    if snapshot_dir and MODES[mode][1] in ("searxng", "offline"):
                        snapshot = SearchSnapshot(Path(snapshot_dir) / f"{question.id}-{mode}", snapshot_mode)
                    row = _one(settings, question, mode, verify_tier, runner, transport,
                               (lambda: reloader(settings, verify_tier)) if reload else None, ram, snapshot,
                               writer_tier=candidate_tier)
                    ram.sample()
                    rows.append({**row, "run": run, "min_free_ram_mb": ram.low})
                    write_summary(out_dir, rows, ram.lowest)   # after every run, so a long eval can be read as it goes
            if number == 0 and offload_probe is not None and bench is not None:
                _save_offload(out_dir, manifest, offload_probe)
    write_summary(out_dir, rows, ram.lowest)
    return out_dir, rows


def _save_offload(out_dir: Path, manifest: dict, probe) -> None:
    try:
        found = probe()
    except Exception:   # noqa: BLE001 - the GPU check is then unknown; the eval goes on
        found = None
    # a copy, so the caller's bench dict stays as it gave it
    manifest["bench"] = {**manifest["bench"], "offload": list(found) if found else None}
    write_manifest(out_dir, manifest)


def _one(settings, question: Question, mode: str, verify_tier, runner, transport, reload, ram,
         snapshot: SearchSnapshot | None = None, writer_tier: str | None = None) -> dict:
    tier, search, public = MODES[mode]
    tier = tier or writer_tier   # a mode with no tier of its own writes with the candidate, else the default
    log = RunLog(settings.runs_dir, task=f"eval {question.id} ({mode}): {question.text}")
    row = {"question": question.id, "mode": mode, "run_id": log.run_id}
    label, started = ram.label, time.perf_counter()
    ram.label = f"{label}: research"
    try:
        # A public question's local mode may use the Tavily fallback when SearXNG returns
        # nothing. The model still runs locally; tavily_sources records the use.
        result = run_research(settings, question.text, log, tier=tier, search=search, public=public and question.public,
                              runner=runner, transport=transport, public_search=question.public,
                              snapshot=snapshot)
    except Exception as exc:   # a blocked or unready mode is a result too; keep going
        return {**row, "outcome": "not_run", "error": f"{type(exc).__name__}: {exc}"}
    if snapshot is not None:
        row.update({f"snapshot_{key}": value for key, value in snapshot.counts.items()})
    row["research_s"] = round(time.perf_counter() - started, 1)
    row["sources"], row["tavily_sources"] = len(result.sources), result.tavily_sources
    for layer in ("offline", "live", "bonus"):
        row[f"{layer}_sources"] = result.layer_sources.get(layer, 0)
    if search != "codex" and result.summary and not result.sources:   # blocked search, not a bad report
        return {**row, "outcome": "no_sources", "error": "the search returned nothing", **_tokens(log)}
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
    changed = repair.count("removed") + repair.count("rewritten")
    failed = gate_failures(summary, repair.before, removed=repair.count("removed"),
                           rewritten=repair.count("rewritten"), facts=facts)
    row.update(outcome="ok", claims=summary["claims"], coverage=coverage, accuracy=accuracy,
               supported=summary["supported"], partial=summary["partial"],
               unsupported=summary["unsupported"], uncited=summary["uncited"], facts=facts,
               doubted=repair.before["doubted"], decider_errors=summary["decider_errors"],
               claims_before=repair.before["claims"], coverage_before=repair.before["citation_coverage"],
               accuracy_before=repair.before["citation_accuracy"], facts_before=facts_before,
               cited=repair.count("cited"), rewritten=repair.count("rewritten"), removed=repair.count("removed"),
               removed_share=removed_share, changed=changed,
               changed_share=round(changed / repair.before["claims"], 3) if repair.before["claims"] else 0,
               unchecked=summary.get("unchecked", 0), gate_failures=" ".join(failed),
               gate="fail" if failed else "pass", error=result.error, **_tokens(log))
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
             f"Gate: coverage {GATE_COVERAGE:.0%}, accuracy at least {GATE_ACCURACY:.0%} (and at least "
             f"{GATE_MIN_ACCURACY_BEFORE:.0%} before repair), no unchecked claims, at least {GATE_MIN_CLAIMS} claims, "
             f"at most {GATE_MAX_REMOVED:.0%} and at most {GATE_MAX_CHANGED} claims removed or rewritten, "
             f"at least {GATE_MIN_FACTS:.0%} of expected facts in the report body. "
             "\"Before\" columns are the report as written. \"Doubted\" counts passes the decider overruled. "
             "A pass shows the checker accepts the report, not that every sentence is true.", "",
             "| Question | Mode | Run | Outcome | Sources | Claims before | Coverage before | Accuracy before | "
             "Facts before | Doubted | Cited | Rewritten | Removed | Claims | Coverage | Accuracy | Facts | Research | "
             "Verify | Repair | Min free RAM | Gate |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    gate_cell = lambda r: r.get("gate", "-") + (f" ({r['gate_failures']})" if r.get("gate_failures") else "")
    for r in rows:
        lines.append(f"| {r['question']} | {r['mode']} | {r.get('run', 1)} | {r.get('outcome', '')} | "
                     f"{r.get('sources', '-')} | "
                     f"{r.get('claims_before', '-')} | {pct(r.get('coverage_before'))} | {pct(r.get('accuracy_before'))} | "
                     f"{pct(r.get('facts_before'))} | {r.get('doubted', '-')} | {r.get('cited', '-')} | {r.get('rewritten', '-')} | "
                     f"{r.get('removed', '-')} | {r.get('claims', '-')} | {pct(r.get('coverage'))} | "
                     f"{pct(r.get('accuracy'))} | {pct(r.get('facts'))} | {r.get('research_s', '-')} s | "
                     f"{r.get('verify_s', '-')} s | {r.get('repair_s', '-')} s | {r.get('min_free_ram_mb', '-')} MB | "
                     f"{gate_cell(r)} |")
    for mode in dict.fromkeys(r["mode"] for r in rows):
        done = [r for r in rows if r["mode"] == mode and r.get("outcome") == "ok"]
        if done:
            mean = lambda key: sum(float(r[key] or 0) for r in done) / len(done)
            lines.append(f"\n**{mode}:** {len(done)} reports, mean coverage {mean('coverage'):.0%} "
                         f"(before repair {mean('coverage_before'):.0%}), mean accuracy {mean('accuracy'):.0%} "
                         f"(before {mean('accuracy_before'):.0%}), mean facts {mean('facts'):.0%} "
                         f"(before {mean('facts_before'):.0%}), mean claims removed {mean('removed_share'):.0%}, "
                         f"{sum(r['gate'] == 'pass' for r in done)} pass the gate.")
        used = [r for r in done if int(r.get("tavily_sources") or 0)]
        if used:
            lines.append(f"Tavily fallback (SearXNG returned nothing): {len(used)} of these reports used it, "
                         f"for {sum(int(r['tavily_sources']) for r in used)} sources in all.")
        layered = {layer: sum(int(r.get(f"{layer}_sources") or 0) for r in done) for layer in ("offline", "live", "bonus")}
        total = sum(int(r.get("sources") or 0) for r in done)
        if any(layered.values()) and total:
            lines.append(f"Sources by search layer: offline {layered['offline']}, free APIs {layered['live']}, "
                         f"SearXNG {layered['bonus']}, Tavily {sum(int(r.get('tavily_sources') or 0) for r in done)} "
                         f"(of {total}; offline and free APIs together "
                         f"{(layered['offline'] + layered['live']) / total:.0%}).")
        both = {}
        for r in done:
            both.setdefault(r["question"], []).append(r["gate"] == "pass")
        lines.append(f"Questions that passed on every run: {sum(all(v) for v in both.values())} of {len(both)}.")
        passed = sum(r["gate"] == "pass" for r in done)
        if done:
            low, high = wilson(passed, len(done))
            lines.append(f"Pass rate {passed}/{len(done)}, 95% Wilson interval {low:.0%} to {high:.0%}. Runs of the same "
                         "question are not independent, so with more than one run per question the true interval is wider.")
    gated = [m for m in dict.fromkeys(r["mode"] for r in rows) if any(r["mode"] == m and r.get("outcome") == "ok" for r in rows)]
    for i, mode_a in enumerate(gated):
        for mode_b in gated[i + 1:]:
            test = paired(rows, mode_a, mode_b)
            if test:
                lines.append(f"\n**{mode_a} vs {mode_b}, paired by question and run:** {test['pairs']} pairs; both pass "
                             f"{test['both']}, neither {test['neither']}, only {mode_a} {test['only_a']}, only {mode_b} "
                             f"{test['only_b']}. Exact McNemar p = {test['p']} (under 0.05 is a real difference).")
    if lowest_ram:
        lines.append(f"\n**Lowest free RAM:** {lowest_ram[0]} MB, during {lowest_ram[1] or 'setup'}. "
                     "Samples every 15 s are in ram.csv.")
    return "\n".join(lines) + "\n"


def rescore(eval_dir: Path, questions: list[Question], runs_dir: Path | None = None) -> list[dict]:
    """Score a finished eval again from its saved reports, with no model calls. Each report
    gets three gates: as run; the old gate with expected facts counted on the body only; and
    the current gate (GATE_* above, whole-word facts). The expected facts come from
    `questions`, so a question file edited since the eval scores the new facts. Reads each
    run's report.md, report.original.md and repair.json; writes rescore.csv and rescore.md
    next to summary.csv.

    A report edited after its run would score differently from the live eval; nothing here
    can tell."""
    eval_dir = Path(eval_dir)
    runs_dir = Path(runs_dir) if runs_dir else eval_dir.parent
    by_id = {q.id: q for q in questions}
    with (eval_dir / "summary.csv").open(encoding="utf-8") as handle:
        old_rows = list(csv.DictReader(handle))
    rows = []
    for old in old_rows:
        row = {key: old.get(key, "") for key in ("question", "mode", "run", "outcome", "facts", "gate")}
        row.update(facts_body="", facts_new="", gate_body="", gate_new="", gate_failures="")
        run_dir, question = runs_dir / old.get("run_id", ""), by_id.get(old["question"])
        if old.get("outcome") == "ok" and question and (run_dir / "repair.json").exists():
            repair = json.loads((run_dir / "repair.json").read_text(encoding="utf-8"))
            actions = [change["action"] for change in repair["changes"]]
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            row["facts_body"] = question.facts_found(report, exact=False)
            row["facts_new"] = question.facts_found(report)
            row["gate_body"] = "pass" if passes_old_gate(repair["after"], float(old.get("removed_share") or 0),
                                                         row["facts_body"]) else "fail"
            failed = gate_failures(repair["after"], repair["before"], removed=actions.count("removed"),
                                   rewritten=actions.count("rewritten"), facts=row["facts_new"])
            row["gate_new"], row["gate_failures"] = ("fail" if failed else "pass"), " ".join(failed)
        rows.append(row)
    fields = ("question", "mode", "run", "outcome", "facts", "facts_body", "facts_new",
              "gate", "gate_body", "gate_new", "gate_failures")
    with (eval_dir / "rescore.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (eval_dir / "rescore.md").write_text(rescore_markdown(eval_dir.name, rows), encoding="utf-8")
    return rows


def rescore_markdown(name: str, rows: list[dict]) -> str:
    pct = lambda v: "-" if v in (None, "") else f"{float(v):.0%}"
    lines = [f"# Rescore of {name}", "",
             "\"As run\" is the eval's own result. \"Body only\" is the old gate with expected facts counted "
             "on the report body (no title, no Sources list). \"Current\" is today's gate: no unchecked claims, "
             f"at least {GATE_MIN_CLAIMS} claims, at least {GATE_MIN_ACCURACY_BEFORE:.0%} accuracy before repair, "
             f"at most {GATE_MAX_REMOVED:.0%} and {GATE_MAX_CHANGED} claims removed or rewritten, whole-word facts. "
             "A pass shows the checker accepts the report, not that every sentence is true.", "",
             "| Question | Mode | Run | Facts as run | Facts body only | Facts current | Gate as run | "
             "Gate body only | Gate current | Fails on |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['question']} | {r['mode']} | {r['run']} | {pct(r['facts'])} | {pct(r['facts_body'])} | "
                     f"{pct(r['facts_new'])} | {r['gate'] or '-'} | {r['gate_body'] or '-'} | {r['gate_new'] or '-'} | "
                     f"{r['gate_failures'] or '-'} |")
    lines.append("")
    for mode in dict.fromkeys(r["mode"] for r in rows):
        done = [r for r in rows if r["mode"] == mode]
        count = lambda key: sum(r[key] == "pass" for r in done)
        lines.append(f"**{mode}:** of {len(done)}, {count('gate')} passed as run, {count('gate_body')} with "
                     f"body-only facts, {count('gate_new')} under the current gate.")
    return "\n".join(lines) + "\n"
