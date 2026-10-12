"""A stand-in for the model server, used only by the tests.

It answers every suite correctly from the answer keys, so a full run against it
proves that each test item is passable and that the harness scores it as passed.
"""
from __future__ import annotations

import json
import re

import httpx

from bench.suites import research, vision
from bench.suites.code_editing import load_tasks
from bench.suites.tool_cases import CASES

VISION_ANSWERS = {"bar-highest": "South", "bar-read-value": "180", "line-peak": "April",
                  "table-cell": "33", "terminal-error": "A ValueError, raised on line 17 of summary.py."}


def oracle_steps(case) -> list:
    return case.oracle or [[(e.tool, e.args)] for e in case.expect] + [case.answer]


def _text(message: dict) -> str:
    content = message["content"]
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content if part.get("type") == "text")


def answer_for(body: dict) -> dict:
    """Decide what the perfect model would say for this request body."""
    messages = body["messages"]
    user = next(_text(m) for m in messages if m["role"] == "user")
    if body.get("tools"):
        case = next(c for c in CASES if c.prompt == user)
        step = oracle_steps(case)[sum(1 for m in messages if m["role"] == "assistant")]
        if isinstance(step, str):
            return {"content": step}
        return {"tool_calls": [(name, json.dumps(args)) for name, args in step]}
    for task in load_tasks():
        if task.instruction in user:
            return {"content": f"Here is the fix.\n\n```python\n{task.solution}```\n"}
    for entry in json.loads(research.DATA.read_text(encoding="utf-8")):
        if entry["question"] in user:
            parts = []
            for number, source in enumerate(entry["sources"], start=1):
                parts += [f"{sentence.strip()}. [{number}]"
                          for sentence in re.split(r"\.(?:\s|$)", source["text"]) if sentence.strip()]
            return {"content": " ".join(parts)}
    for spec in vision.build_items():
        if spec["question"] == user:
            return {"content": VISION_ANSWERS[spec["id"]]}
    return {"content": "ready"}


def sse(answer: dict, reasoning: str = "Let me think.") -> bytes:
    """Encode an answer the way a streaming OpenAI-compatible server would."""
    def event(delta: dict, finish: str | None = None) -> str:
        return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"

    out = [event({"role": "assistant"}), event({"reasoning_content": reasoning})]
    content = answer.get("content", "")
    if content:
        half = len(content) // 2
        out += [event({"content": content[:half]}), event({"content": content[half:]})]
    for index, (name, arguments) in enumerate(answer.get("tool_calls", [])):
        half = len(arguments) // 2
        out.append(event({"tool_calls": [{"index": index, "id": f"call_{index}", "type": "function",
                                          "function": {"name": name, "arguments": arguments[:half]}}]}))
        out.append(event({"tool_calls": [{"index": index, "function": {"arguments": arguments[half:]}}]}))
    out.append(event({}, "tool_calls" if answer.get("tool_calls") else "stop"))
    out.append("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 40}}) + "\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out).encode("utf-8")


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v0/models":
        return httpx.Response(200, json={"data": [{"id": "fake-model", "state": "loaded",
                                                   "quantization": "Q4_K_M", "loaded_context_length": 8192}]})
    if path == "/api/v1/chat":
        return httpx.Response(200, json={
            "output": [{"type": "reasoning", "content": "14:10 plus 2:55"},
                       {"type": "message", "content": "17:05"}],
            "stats": {"tokens_per_second": 30.0, "time_to_first_token_seconds": 0.2,
                      "total_output_tokens": 12, "reasoning_output_tokens": 8}})
    if path == "/v1/chat/completions":
        return httpx.Response(200, content=sse(answer_for(json.loads(request.content))),
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(404, text="not found")


def transport() -> httpx.MockTransport:
    return httpx.MockTransport(handler)
