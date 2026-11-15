"""A random sample of the checker's verdicts, for labelling by hand, and the rates the labels give.

The eval review (2026-11-14, finding 8) found nothing estimates how often a false claim passes
the checker, or how often repair removes a true one. `sample` draws claims that passed and
claims that repair removed from finished evals, with a fixed seed, into a CSV:

- passed: a claim in the repaired report that the checker marked supported;
- removed: a claim repair took out (from repair.json), with the checks it failed.

Each row names the cited sources (title and URL, from sources.json) and the checker's quote and
reason. Whoever labels it reads the source itself and fills `label` with "true" (the cited
sources support the claim as written), "false" (they don't), or "unclear", and `labeller` with
who decided. `score` reads the labelled CSV and gives the false-pass rate (false among passed)
and the false-removal rate (true among removed), each with a 95% Wilson interval. A label is a
reader's judgement against the sources, not proof that a claim is true of the world.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from .stats import wilson

FIELDS = ("sample_id", "group", "question", "mode", "run", "run_id", "claim", "sources", "checker_verdict",
          "checker_quote", "checker_reason", "label", "labeller", "note")
LABELS = ("true", "false", "unclear")


def _sources(run_dir: Path) -> dict[str, str]:
    path = run_dir / "sources.json"
    if not path.exists():
        return {}
    return {str(s.get("index") or i): f"{s.get('title', '')} <{s.get('link') or s.get('url', '')}>"
            for i, s in enumerate(json.loads(path.read_text(encoding="utf-8")), start=1)}


def _row(group: str, eval_row: dict, claim: dict, sources: dict, reason: str = "") -> dict:
    checks = claim.get("checks", [])
    quotes = [c["quote"] for c in checks if c.get("quote")]
    reasons = [c["reason"] for c in checks if c.get("reason")]
    return {"group": group, "question": eval_row["question"], "mode": eval_row["mode"], "run": eval_row.get("run", ""),
            "run_id": eval_row["run_id"], "claim": claim["text"],
            "sources": " | ".join(f"[{i}] {sources.get(str(i), '?')}" for i in claim.get("citations", [])),
            "checker_verdict": claim.get("verdict", ""), "checker_quote": " / ".join(quotes)[:600],
            "checker_reason": "; ".join(filter(None, [reason, *reasons]))[:400], "label": "", "labeller": "", "note": ""}


def candidates(eval_dir: Path, runs_dir: Path | None = None) -> list[dict]:
    eval_dir = Path(eval_dir)
    runs_dir = Path(runs_dir) if runs_dir else eval_dir.parent
    with (eval_dir / "summary.csv").open(encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("outcome") == "ok"]
    out = []
    for r in rows:
        run_dir = runs_dir / r["run_id"]
        sources = _sources(run_dir)
        final = run_dir / "verification.json"
        if final.exists():
            for claim in json.loads(final.read_text(encoding="utf-8"))["claims"]:
                if claim.get("verdict") == "supported":
                    out.append(_row("passed", r, claim, sources))
        repair, original = run_dir / "repair.json", run_dir / "verification.original.json"
        if repair.exists():
            before = {}
            if original.exists():
                before = {c["text"]: c for c in json.loads(original.read_text(encoding="utf-8"))["claims"]}
            for change in json.loads(repair.read_text(encoding="utf-8"))["changes"]:
                if change["action"] == "removed":
                    claim = next((c for text, c in before.items() if text and text in change["before"]),
                                 {"text": change["before"], "citations": []})
                    out.append(_row("removed", r, claim, sources, change.get("reason", "")))
    return out


def sample(eval_dirs: list[Path], out: Path, n_passed: int = 40, n_removed: int = 20, seed: int = 20261115,
           runs_dir: Path | None = None) -> list[dict]:
    pool = [row for d in eval_dirs for row in candidates(d, runs_dir)]
    rng = random.Random(seed)
    picked = []
    for group, n in (("passed", n_passed), ("removed", n_removed)):
        members = [r for r in pool if r["group"] == group]
        picked += rng.sample(members, min(n, len(members)))
    for i, row in enumerate(picked, start=1):
        row["sample_id"] = i
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(picked)
    return picked


def score(path: Path) -> dict:
    with Path(path).open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    bad = [r["sample_id"] for r in rows if r["label"] and r["label"] not in LABELS]
    if bad:
        raise ValueError(f"labels must be one of {LABELS}; rows {bad} are not")
    result = {}
    for group, wrong in (("passed", "false"), ("removed", "true")):
        labelled = [r for r in rows if r["group"] == group and r["label"] in ("true", "false")]
        k = sum(r["label"] == wrong for r in labelled)
        result[group] = {"sampled": sum(r["group"] == group for r in rows), "labelled": len(labelled),
                         "unclear": sum(r["group"] == group and r["label"] == "unclear" for r in rows),
                         "wrong": k, "rate": round(k / len(labelled), 3) if labelled else None,
                         "interval": wilson(k, len(labelled))}
    return result


def score_markdown(result: dict) -> str:
    lines = ["# Checker verdicts against labels", ""]
    names = {"passed": "False passes (claims the checker passed that the sources don't support)",
             "removed": "False removals (claims repair removed that the sources do support)"}
    for group, s in result.items():
        if s["rate"] is None:
            lines.append(f"- {names[group]}: nothing labelled.")
            continue
        low, high = s["interval"]
        lines.append(f"- {names[group]}: {s['wrong']} of {s['labelled']} ({s['rate']:.0%}; 95% interval "
                     f"{low:.0%} to {high:.0%}); {s['unclear']} unclear.")
    lines += ["", "A label is a reader's judgement against the cited sources, not proof that a claim is true."]
    return "\n".join(lines) + "\n"
