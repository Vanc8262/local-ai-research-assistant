"""Research-summary suite: the model summarizes a few supplied sources and must
cite them. All sources are fictional, so nothing can be answered from memory.

A script checks what can be checked mechanically (citations exist, planted facts
appear, each fact is cited to the right source). Readability and faithfulness
still need a human, so every answer is also written to the manual review file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Ctx, SuiteResult

DATA = Path(__file__).resolve().parent.parent / "data" / "research_sets.json"
SYSTEM = ("You write short research summaries. Use only the numbered sources you are given; "
          "do not add outside knowledge.")
INSTRUCTIONS = ("Answer the question in at most 150 words. After every sentence that states a "
                "fact, cite the supporting source number in square brackets, like [1] or [2].")
CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
BOUNDARY_RE = re.compile(r"[.!?](?=\s|$|\[)|\n")
TRAILING_CITES_RE = re.compile(r"[.!?]?\s*(?:\[\d+(?:\s*,\s*\d+)*\]\s*)+")
WORD_LIMIT = 150


def build_prompt(item: dict) -> str:
    sources = "\n\n".join(f"[{n}] {src['title']}\n{src['text']}"
                          for n, src in enumerate(item["sources"], start=1))
    return f"Sources:\n\n{sources}\n\nQuestion: {item['question']}\n\n{INSTRUCTIONS}"


def _numbers(text: str) -> list[int]:
    return [int(n) for group in CITE_RE.findall(text) for n in re.split(r"\s*,\s*", group)]


def sentence_citations(answer: str, start: int, end: int) -> list[int]:
    """Source numbers cited in the sentence that contains answer[start:end]."""
    begin = 0
    for match in BOUNDARY_RE.finditer(answer, 0, start):
        begin = match.end()
    close = BOUNDARY_RE.search(answer, end)
    stop = close.start() if close else len(answer)
    trailing = TRAILING_CITES_RE.match(answer, stop)
    if trailing:
        stop = trailing.end()
    return _numbers(answer[begin:stop])


def score_answer(item: dict, answer: str) -> dict:
    cited = _numbers(answer)
    valid = bool(cited) and all(1 <= n <= len(item["sources"]) for n in cited)
    found = correct = 0
    missing = []
    for fact in item["facts"]:
        match = re.search(fact["pattern"], answer, re.IGNORECASE)
        if not match:
            missing.append(fact["pattern"])
            continue
        found += 1
        if fact["source"] in sentence_citations(answer, match.start(), match.end()):
            correct += 1
    recall = found / len(item["facts"])
    accuracy = correct / found if found else 0.0
    words = len(re.sub(CITE_RE, "", answer).split())
    return {
        "citations_valid": valid, "fact_recall": round(recall, 3),
        "citation_accuracy": round(accuracy, 3), "words": words,
        "within_length": words <= WORD_LIMIT * 1.15, "missing_facts": missing,
        "auto_score": round((recall + accuracy) / 2 if valid else recall / 2, 3),
    }


def run(ctx: Ctx) -> SuiteResult:
    sets = json.loads(DATA.read_text(encoding="utf-8"))
    if ctx.limit:
        sets = sets[: ctx.limit]
    items = []
    for entry in sets:
        result = ctx.chat([{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": build_prompt(entry)}])
        item = {"id": entry["id"], "question": entry["question"], "answer": result.content,
                "error": result.error, "needs_manual_review": True}
        item.update(score_answer(entry, result.content))
        item["passed"] = item["auto_score"] >= 0.75 and item["citations_valid"]
        item["timing"] = [call.timing() for call in ctx.take_calls()]
        items.append(item)
        print(f"  {entry['id']}: recall {item['fact_recall']}, citation accuracy "
              f"{item['citation_accuracy']}, {item['words']} words")
    score = sum(item["auto_score"] for item in items) / len(items)
    metrics = {
        "mean_fact_recall": round(sum(i["fact_recall"] for i in items) / len(items), 3),
        "mean_citation_accuracy": round(sum(i["citation_accuracy"] for i in items) / len(items), 3),
        "score_is": "automatic part only; add manual scores",
    }
    return SuiteResult("research", score, sum(i["passed"] for i in items), len(items), metrics, items)
