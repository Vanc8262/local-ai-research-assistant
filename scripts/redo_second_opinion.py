"""Re-run the decider's second opinion and the repair step on a finished eval, and re-gate it.

For each run of the eval that the decider judged, this copies the run's original report,
sources and cached pages into runs/redo-<id>/<run_id>, takes the checker's verdicts from
verification.original.json as they were before the decider (a pass it doubted goes back to
"supported"), asks the decider again with the current code, and runs repair_run. The checker
is not asked again about claims it already judged, so a change in the gate comes from the
second opinion and from repair, not from a different checker verdict.

    --decider quote      the current code: the decider judges the checker's quote
    --decider passages   the code before 2026-11-15: the decider reads the three passages

Running both on the same eval separates the change from repair's own run-to-run spread.
Local tiers only; repair may fetch pages of the report's own sources, as in the eval.

    .venv\\Scripts\\python.exe scripts\\redo_second_opinion.py runs\\eval-20261114-195649-7fd789 --decider quote
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant import verify                                      # noqa: E402
from assistant.config import load_settings                        # noqa: E402
from assistant.decide import Decider                              # noqa: E402
from assistant.evaluate import gate_failures, load_questions      # noqa: E402
from assistant.gateway import Gateway                             # noqa: E402
from assistant.repair import repair_run                           # noqa: E402
from assistant.runlog import RunLog, new_run_id                   # noqa: E402
from assistant.verify import DOUBTED, Check, Claim, citation_client   # noqa: E402

DOUBT_RE = re.compile(re.escape(DOUBTED) + r" \(p [0-9.]+, confidence [0-9.]+\); ")
FIELDS = ("question", "mode", "run", "run_id", "decider", "doubted_before", "doubted_now", "accuracy_before",
          "removed", "rewritten", "claims", "facts", "gate", "gate_failures", "old_gate", "old_gate_failures",
          "old_removed", "old_rewritten")


def passages_mode(original):
    """The second opinion as it was before 2026-11-15: hide the quote, so the passages are used."""
    def ask(check, claim, passages, decider):
        quote, check.quote = check.quote, ""
        try:
            return original(check, claim, passages, decider)
        finally:
            check.quote = quote
    return ask


def undo_decider(entry: dict) -> dict:
    """A check as the checker left it, before the decider's second opinion."""
    entry = dict(entry)
    if DOUBT_RE.search(entry.get("reason", "")):
        entry["verdict"] = "supported"
        entry["reason"] = DOUBT_RE.sub("", entry["reason"])
    entry["decider_p"], entry["decider_error"] = None, ""
    return entry


def prepare(src: Path, dst: Path, decider, client) -> int:
    """Copy the run as written, with the checker's own verdicts and a fresh second opinion.
    Returns how many passes the decider doubts now."""
    dst.mkdir(parents=True)
    for name in ("sources.json",):
        shutil.copyfile(src / name, dst / name)
    if (src / "pages").exists():
        shutil.copytree(src / "pages", dst / "pages")
    report = src / "report.original.md"
    shutil.copyfile(report if report.exists() else src / "report.md", dst / "report.md")
    original = src / "verification.original.json"
    data = json.loads((original if original.exists() else src / "verification.json").read_text(encoding="utf-8"))
    sources = {str(s.get("index") or n): s for n, s in
               enumerate(json.loads((src / "sources.json").read_text(encoding="utf-8")), start=1)}
    doubted = 0
    for claim in data["claims"]:
        checks = []
        for entry in claim["checks"]:
            had_decider = entry.get("decider_p") is not None or DOUBT_RE.search(entry.get("reason", ""))
            entry = undo_decider(entry)
            if had_decider:
                check = Check(**entry)
                obj = Claim(text=claim["text"], citations=claim["citations"])
                passages = _passages(obj, check, sources, dst, client)
                check = verify._second_opinion(check, obj, passages, decider)
                doubted += check.reason.startswith(DOUBTED)
                entry = check.__dict__
            checks.append(entry)
        claim["checks"] = checks
    (dst / "verification.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return doubted


def _passages(claim: Claim, check: Check, sources: dict, run_dir: Path, client) -> list[str]:
    from assistant.text import best_passages
    if check.combined:
        blocks = []
        for index in claim.citations:
            if index in sources:
                page, snippet, text = verify.source_text(sources[index], index, client, run_dir)
                if text:
                    parts = (best_passages(claim.text, page)[:2] if page else []) + ([snippet] if snippet else [])
                    blocks += [f"[{index}] {part}" for part in parts]
        return blocks
    if check.index not in sources:
        return []
    page, snippet, _ = verify.source_text(sources[check.index], check.index, client, run_dir)
    return (best_passages(claim.text, page) if page else []) + ([snippet] if snippet else [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("eval", type=Path)
    parser.add_argument("--decider", choices=("quote", "passages"), default="quote")
    parser.add_argument("--questions", nargs="*", type=Path,
                        default=[Path("evals/heldout_questions.toml"), Path("evals/research_questions.toml")])
    parser.add_argument("--all", action="store_true", help="also runs the decider never judged")
    args = parser.parse_args()
    if args.decider == "passages":
        verify._second_opinion = passages_mode(verify._second_opinion)

    questions = {q.id: q for path in args.questions if path.exists() for q in load_questions(path)}
    with (args.eval / "summary.csv").open(encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("outcome") == "ok"]
    if not args.all:
        rows = [r for r in rows if int(r.get("doubted") or 0) > 0]
    settings = load_settings()
    out_dir = settings.runs_dir / f"redo-{new_run_id()}-{args.decider}"
    out_dir.mkdir(parents=True)
    results = []
    for n, row in enumerate(rows, start=1):
        src, dst = args.eval.parent / row["run_id"], out_dir / row["run_id"]
        log = RunLog(settings.runs_dir, task=f"redo second opinion ({args.decider}) {row['run_id']}")
        gateway = Gateway(settings, log)
        decider = Decider(settings, gateway, log)
        client = citation_client()
        try:
            doubted = prepare(src, dst, decider, client)
            repair = repair_run(dst, gateway, log, decider=decider)
        finally:
            client.close()
            decider.close()
            gateway.close()
        question = questions.get(row["question"])
        facts = question.facts_found((dst / "report.md").read_text(encoding="utf-8")) if question else None
        failed = gate_failures(repair.after, repair.before, removed=repair.count("removed"),
                               rewritten=repair.count("rewritten"), facts=facts)
        results.append({
            "question": row["question"], "mode": row["mode"], "run": row.get("run", ""), "run_id": row["run_id"],
            "decider": args.decider, "doubted_before": row.get("doubted", ""), "doubted_now": doubted,
            "accuracy_before": repair.before["citation_accuracy"], "removed": repair.count("removed"),
            "rewritten": repair.count("rewritten"), "claims": repair.after["claims"], "facts": facts,
            "gate": "fail" if failed else "pass", "gate_failures": " ".join(failed),
            "old_gate": row.get("gate", ""), "old_gate_failures": row.get("gate_failures", ""),
            "old_removed": row.get("removed", ""), "old_rewritten": row.get("rewritten", "")})
        r = results[-1]
        print(f"{n}/{len(rows)} {r['question']} {r['mode']}: doubted {r['doubted_before']} -> {doubted}, "
              f"removed {r['old_removed']} -> {r['removed']}, gate {r['old_gate']} -> {r['gate']} "
              f"({r['gate_failures'] or '-'})", flush=True)
        with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(results)
    print(f"Saved {out_dir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
