"""Speed suite: time to first token and tokens per second on three fixed
prompts, plus a reasoning on/off comparison through LM Studio's own API."""
from __future__ import annotations

import re
import time

from .base import Ctx, SuiteResult, median

PARAGRAPHS = [
    "The regional water board reviewed pumping schedules for the third quarter and found that "
    "night-time pumping reduced energy cost without affecting reservoir levels.",
    "Field crews replaced forty valve assemblies along the northern main, and the pressure "
    "loggers installed afterwards showed fewer transient spikes during morning demand.",
    "A customer survey mailed to nine thousand households asked about taste, pressure and "
    "billing clarity; billing clarity drew the most written comments.",
    "The capital plan proposes relining the oldest cast-iron sections first, because break "
    "records show those sections account for most emergency repairs.",
    "Laboratory staff added weekly sampling at the two treatment plants and published the "
    "results on the public dashboard within five working days.",
]


def long_document(paragraph_count: int = 60) -> str:
    """A fixed synthetic report of roughly 2,500 tokens, used to time prompt processing."""
    return "\n\n".join(f"Section {i + 1}. {PARAGRAPHS[i % len(PARAGRAPHS)]}"
                       for i in range(paragraph_count))


PROMPTS = [
    {"id": "short", "max_tokens": 1500,
     "text": "Reply with the single word: ready"},
    {"id": "generate", "max_tokens": 2500,
     "text": "Explain how a hash table works, including collisions and resizing, in about 250 words."},
    {"id": "long_input", "max_tokens": 1500,
     "text": long_document() + "\n\nSummarize the report above in exactly three bullet points."},
]
REASONING_PROBE = ("A train leaves at 14:10 and the trip takes 2 hours 55 minutes. "
                   "At what time does it arrive? Answer with the time only.")


def reasoning_comparison(ctx: Ctx) -> list[dict]:
    """Same prompt with reasoning on and off, via LM Studio's /api/v1/chat."""
    rows = []
    if ctx.client is None or not hasattr(ctx.client, "native_chat"):
        return rows
    for setting in ("on", "off"):
        outcome = ctx.client.native_chat(ctx.model.id, REASONING_PROBE, reasoning=setting,
                                         temperature=ctx.cfg.temperature, max_output_tokens=2000)
        row = {"reasoning": setting, **outcome}
        if "answer" in outcome:
            row["correct"] = bool(re.search(r"\b(17[:.]05|5[:.]05\s*p\.?m)", outcome["answer"], re.I))
        rows.append(row)
        note = outcome.get("error") or outcome.get("stats")
        print(f"  reasoning {setting}: {note}")
    return rows


def run(ctx: Ctx) -> SuiteResult:
    repeats = max(1, ctx.cfg.speed_repeats)
    items = []
    for prompt in PROMPTS:
        runs = []
        for repeat in range(repeats):
            # A unique first line stops the server from reusing a cached prompt,
            # which would make later repeats look faster than a real first request.
            nonce = f"Request {time.time_ns()}-{repeat}.\n\n"
            result = ctx.chat([{"role": "user", "content": nonce + prompt["text"]}],
                              max_tokens=prompt["max_tokens"])
            runs.append({**result.timing(), "error": result.error})
        ctx.take_calls()
        ok = [r for r in runs if not r["error"]]
        item = {
            "id": prompt["id"], "runs": runs, "passed": len(ok) == len(runs),
            "median_ttft_s": median([r["ttft_s"] for r in ok]),
            "median_ttfa_s": median([r["ttfa_s"] for r in ok]),
            "median_gen_tps": median([r["gen_tps"] for r in ok]),
            "median_prompt_tps": median([r["prompt_tps"] for r in ok]),
            "median_total_s": median([r["total_s"] for r in ok]),
        }
        items.append(item)
        print(f"  {prompt['id']}: first token {item['median_ttft_s']} s, "
              f"{item['median_gen_tps']} tok/s, total {item['median_total_s']} s")

    by_id = {item["id"]: item for item in items}
    metrics = {
        "repeats": repeats,
        "generate_tps": by_id["generate"]["median_gen_tps"],
        "long_input_ttft_s": by_id["long_input"]["median_ttft_s"],
        "long_input_prompt_tps": by_id["long_input"]["median_prompt_tps"],
        "reasoning_comparison": reasoning_comparison(ctx),
    }
    return SuiteResult("speed", None, sum(i["passed"] for i in items), len(items), metrics, items)
