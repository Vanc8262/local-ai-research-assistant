"""Why does the decider overrule good passes? Re-ask it about the checks it judged in an eval.

For every check in the given evals that the decider judged (decider_p set), before repair, this
rebuilds the exact passages the decider saw (from the run's cached pages; nothing is fetched)
and asks the decider again in several ways:

    current    the production question, recording the first tokens and letter mass
    reversed   the same, with the options in the other order (letter-position bias)
    quote      the state is the checker's quote alone, not three passages
    both       the passages, then the checker's quote marked as the supporting sentence
    reworded   passages, with the statement "The passages say this, in the same or other words"
    greedy     the production question, reading the text the model writes (up to 40 tokens)

It also takes a seeded sample of checks the decider let through (p >= 0.8) as controls.
Output: results/decider_probe_<date>/probe.jsonl and cases.csv (claim, quote, passages, for
labelling). Runs on local tiers only; nothing leaves the PC.

    .venv\\Scripts\\python.exe scripts\\decider_probe.py runs\\eval-20261114-195649-7fd789 runs\\eval-20261114-050849-f454a6
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant.config import load_settings          # noqa: E402
from assistant.decide import Decider, noul, build_messages, NO_THINKING   # noqa: E402
from assistant.gateway import Gateway               # noqa: E402
from assistant.runlog import RunLog                 # noqa: E402
from assistant.text import best_passages            # noqa: E402
from assistant.verify import MIN_DECIDER_P, source_text   # noqa: E402


def offline_client() -> httpx.Client:
    """Pages come from the run's cache; a missing page stays missing instead of being fetched."""
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(599)))


def passages_for(claim: dict, check: dict, sources: dict, run_dir: Path, client) -> list[str]:
    """The passages verify.check_citation / _combined_check handed the decider."""
    if check.get("combined"):
        blocks = []
        for index in claim["citations"]:
            if index not in sources:
                continue
            page, snippet, text = source_text(sources[index], index, client, run_dir)
            if text:
                parts = (best_passages(claim["text"], page)[:2] if page else []) + ([snippet] if snippet else [])
                blocks += [f"[{index}] {part}" for part in parts]
        return blocks
    source = sources.get(check["index"])
    if source is None:
        return []
    page, snippet, _ = source_text(source, check["index"], client, run_dir)
    passages = best_passages(claim["text"], page) if page else []
    if snippet:
        passages.append(snippet)
    return passages


def collect(eval_dirs: list[Path]) -> list[dict]:
    client, cases = offline_client(), []
    for eval_dir in eval_dirs:
        with (eval_dir / "summary.csv").open(encoding="utf-8") as handle:
            rows = [r for r in csv.DictReader(handle) if r.get("outcome") == "ok"]
        for row in rows:
            run_dir = eval_dir.parent / row["run_id"]
            path = run_dir / "verification.original.json"
            if not path.exists():
                path = run_dir / "verification.json"
            if not path.exists():
                continue
            sources = {str(s.get("index") or n): s for n, s in
                       enumerate(json.loads((run_dir / "sources.json").read_text(encoding="utf-8")), start=1)}
            removed = set()
            repair = run_dir / "repair.json"
            if repair.exists():
                for change in json.loads(repair.read_text(encoding="utf-8"))["changes"]:
                    removed.add((change["action"], change["before"]))
            for claim in json.loads(path.read_text(encoding="utf-8"))["claims"]:
                fate = next((a for a, before in removed if before == claim["text"]), "kept")
                for check in claim["checks"]:
                    if check.get("decider_p") is None:
                        continue
                    cases.append({
                        "eval": eval_dir.name, "run_id": row["run_id"], "question": row["question"],
                        "mode": row["mode"], "claim": claim["text"], "index": check["index"],
                        "combined": bool(check.get("combined")), "quote": check.get("quote", ""),
                        "verdict": check["verdict"], "reason": check.get("reason", ""),
                        "stored_p": check["decider_p"], "repair": fate,
                        "passages": passages_for(claim, check, sources, run_dir, client)})
    client.close()
    return cases


def ask(decider: Decider, state: str, statement: str, reverse: bool = False) -> dict:
    question = noul(statement)
    if reverse:
        question.options = dict(reversed(list(question.options.items())))
    d = decider.ask(state, question, purpose="decider-probe")
    return {"p": d.p_true, "mass": d.mass, "raw": d.raw[:200], "error": d.error, "method": d.method}


def greedy_text(decider: Decider, state: str, statement: str) -> str:
    messages = build_messages(state, noul(statement), ["true", "false"])
    result = decider.gateway.ask(decider.tier_name, messages, purpose="decider-probe (text)", private=True,
                                 max_tokens=40, temperature=0.0, extra_body=NO_THINKING)
    return (result.error or result.content or "")[:200]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evals", nargs="+", type=Path)
    parser.add_argument("--controls", type=int, default=40)
    parser.add_argument("--tier", default=None)
    parser.add_argument("--out", type=Path, default=Path("results") / f"decider_probe_{date.today():%Y-%m-%d}")
    args = parser.parse_args()

    cases = collect(args.evals)
    doubted = [c for c in cases if c["stored_p"] < MIN_DECIDER_P]
    passed = [c for c in cases if c["stored_p"] >= MIN_DECIDER_P]
    controls = random.Random(20261115).sample(passed, min(args.controls, len(passed)))
    for c in doubted:
        c["group"] = "doubted"
    for c in controls:
        c["group"] = "control"
    picked = doubted + controls
    print(f"{len(cases)} decider checks; {len(doubted)} doubted, {len(controls)} controls of {len(passed)} let through")

    settings = load_settings()
    log = RunLog(settings.runs_dir, task="decider probe")
    gateway = Gateway(settings, log)
    decider = Decider(settings, gateway, log, tier=args.tier)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        with (args.out / "probe.jsonl").open("w", encoding="utf-8") as out:
            for n, c in enumerate(picked, start=1):
                state = "Passages from the cited source:\n" + "\n---\n".join(c["passages"])
                statement = f"The passages support this claim: {c['claim']}"
                quote_state = "Sentence from the cited source:\n" + c["quote"]
                both_state = state + "\n\nThe sentence the checker says supports the claim:\n" + c["quote"]
                c["probe"] = {
                    "current": ask(decider, state, statement),
                    "reversed": ask(decider, state, statement, reverse=True),
                    "quote": ask(decider, quote_state, f"The sentence supports this claim: {c['claim']}") if c["quote"] else None,
                    "both": ask(decider, both_state, statement) if c["quote"] else None,
                    "reworded": ask(decider, state, f"The passages say this, in the same or other words: {c['claim']}"),
                    "greedy": greedy_text(decider, state, statement),
                    "state_chars": len(state),
                }
                out.write(json.dumps(c, ensure_ascii=False) + "\n")
                out.flush()
                p = c["probe"]
                print(f"{n}/{len(picked)} {c['group']:<8} stored {c['stored_p']:.2f} now {p['current']['p']:.2f} "
                      f"mass {p['current']['mass']:.2f} rev {p['reversed']['p']:.2f} "
                      f"quote {p['quote']['p'] if p['quote'] else '-'} | {p['greedy'][:40]!r}", flush=True)
    finally:
        decider.close()
        gateway.close()

    with (args.out / "cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["n", "group", "question", "mode", "repair", "stored_p", "claim", "quote", "passages",
                         "label", "labeller", "note"])
        for n, c in enumerate(picked, start=1):
            writer.writerow([n, c["group"], c["question"], c["mode"], c["repair"], c["stored_p"], c["claim"],
                             c["quote"], "\n---\n".join(c["passages"]), "", "", ""])
    print(f"Saved {args.out / 'probe.jsonl'} and {args.out / 'cases.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
