"""Score the local decider against labelled cases (evals/decide_cases.toml).

For each tier this reports accuracy per use case, how long a decision takes, which
method answered (logprobs or greedy), and how confident the model was when it was
right and when it was wrong. That last pair is what thresholds get set from: a
threshold is only useful if wrong answers come with lower confidence than right ones.
"""
from __future__ import annotations

import json
import statistics
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .decide import Decider, Decision, Question, choice, noul, score


@dataclass
class Case:
    id: str
    use: str
    priority: int
    state: str
    question: Question
    expect: str


def load_cases(path: str | Path) -> list[Case]:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    cases = []
    for entry in raw.get("case", []):
        kind = entry["kind"]
        if kind == "choice":
            question = choice(entry["instructions"], entry["options"])
        elif kind == "score":
            question = score(entry["instructions"], entry["levels"])
        elif kind == "noul":
            question = noul(entry["statement"])
        else:
            raise ValueError(f"case {entry.get('id')}: unknown kind {kind!r}")
        expect = str(entry["expect"]).lower() if kind == "noul" else str(entry["expect"])
        if expect not in question.options:
            raise ValueError(f"case {entry['id']}: expect {expect!r} is not one of {list(question.options)}")
        cases.append(Case(entry["id"], entry["use"], int(entry.get("priority", 1)), entry["state"],
                          question, expect))
    return cases


def run_check(decider: Decider, cases: list[Case], tier: str) -> dict:
    rows = []
    for case in cases:
        decision: Decision = decider.ask(case.state, case.question, purpose=f"check:{case.id}", tier=tier)
        rows.append({"id": case.id, "use": case.use, "priority": case.priority, "kind": case.question.kind,
                     "expect": case.expect, "correct": decision.answer == case.expect,
                     "p_expected": decision.probabilities.get(case.expect, 0.0), **decision.to_record()})
    return {"tier": tier, "debias": decider.debias, "rows": rows, "summary": summarise(rows)}


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def summarise(rows: list[dict]) -> dict:
    answered = [r for r in rows if not r["error"]]
    right = [r for r in answered if r["correct"]]
    wrong = [r for r in answered if not r["correct"]]
    uses = {}
    for use in dict.fromkeys(r["use"] for r in rows):
        group = [r for r in rows if r["use"] == use]
        uses[use] = {"priority": group[0]["priority"], "cases": len(group),
                     "correct": sum(r["correct"] for r in group)}
    latencies = [r["latency_s"] for r in answered]
    return {
        "cases": len(rows), "answered": len(answered), "correct": len(right),
        "accuracy": round(len(right) / len(rows), 3) if rows else None,
        "methods": {m: sum(r["method"] == m for r in answered) for m in dict.fromkeys(r["method"] for r in answered)},
        "median_s": round(statistics.median(latencies), 3) if latencies else None,
        "confidence_right": _mean([r["confidence"] for r in right]),
        "confidence_wrong": _mean([r["confidence"] for r in wrong]),
        "low_mass": sum(r["mass"] < 0.5 for r in answered),
        "uses": uses,
        "errors": [f"{r['id']}: {r['error']}" for r in rows if r["error"]],
    }


def check_markdown(results: list[dict]) -> str:
    lines = ["# Decider check", ""]
    for result in results:
        s = result["summary"]
        lines += [f"## {result['tier']}" + (" (debiased)" if result["debias"] else ""), "",
                  f"- Correct: {s['correct']} of {s['cases']} ({s['accuracy']:.0%})" if s["accuracy"] is not None
                  else "- No cases",
                  f"- Methods: {', '.join(f'{m} {n}' for m, n in s['methods'].items()) or 'none'}",
                  f"- Median time per decision: {s['median_s']} s",
                  f"- Mean confidence when right: {s['confidence_right']}, when wrong: {s['confidence_wrong']}",
                  f"- Decisions where under half the model's weight fell on an option letter: {s['low_mass']}",
                  "", "| Use case | Priority | Correct |", "| --- | --- | --- |"]
        lines += [f"| {use} | P{u['priority']} | {u['correct']} of {u['cases']} |" for use, u in s["uses"].items()]
        wrong = [r for r in result["rows"] if not r["correct"]]
        if wrong:
            lines += ["", "| Case | Expected | Got | p(expected) | Confidence | Note |", "| --- | --- | --- | --- | --- | --- |"]
            lines += [f"| {r['id']} | {r['expect']} | {r['answer'] or '-'} | {r['p_expected']:.2f} | "
                      f"{r['confidence']:.2f} | {(r['error'] or r['raw'])[:60].replace('|', '/')} |" for r in wrong]
        lines.append("")
    return "\n".join(lines)


def save(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    path.with_suffix(".md").write_text(check_markdown(results), encoding="utf-8")
