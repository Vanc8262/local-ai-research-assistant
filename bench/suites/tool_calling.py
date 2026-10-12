"""Tool-calling suite: runs each scripted case as a short agent loop against the
mock tools and scores it with a script."""
from __future__ import annotations

import json
import re

from ..client import assistant_message
from .base import Ctx, SuiteResult, speed_metrics
from .tool_cases import CASES, MISSING, Case, Expect
from .tool_world import TOOLS, World, validate_args

SYSTEM = ("You are a helpful assistant with access to tools. Use a tool when you need it to "
          "answer, and answer directly when you do not. All data comes from a small test "
          "environment, so rely on tool results rather than memory. Today's date is 2026-11-01.")
MAX_TURNS = 8
LEAK_RE = re.compile(r"<tool_call>|<function=|\"arguments\"\s*:|\"parameters\"\s*:", re.IGNORECASE)


def _strict(expected: object, actual: object) -> bool:
    if actual is MISSING:
        return False
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) \
            and abs(actual - expected) < 0.01
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().casefold() == expected.casefold()
    return actual == expected


def call_matches(expect: Expect, call: dict) -> bool:
    if call["name"] != expect.tool or not call["ok"]:
        return False
    for key in set(expect.args) | set(expect.loose):
        actual = call["args"].get(key, MISSING)
        check = expect.loose.get(key)
        if not (check(actual) if check else _strict(expect.args[key], actual)):
            return False
    return True


def normalize_answer(text: str) -> str:
    """Drop thousands separators so 3,000 and 3000 compare equal."""
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)


def check_case(case: Case, calls: list[dict], final: str) -> tuple[bool, str]:
    if case.no_tools and calls:
        return False, f"called {calls[0]['name']} when no tool was needed"
    used: set[int] = set()
    for expect in case.expect:
        match = next((i for i, call in enumerate(calls)
                      if i not in used and call_matches(expect, call)), None)
        if match is None:
            return False, f"no successful {expect.tool} call with the expected arguments"
        used.add(match)
    for tool, count in case.exact.items():
        made = sum(1 for call in calls if call["name"] == tool and call["ok"])
        if made != count:
            return False, f"expected exactly {count} successful {tool} calls, got {made}"
    answer = normalize_answer(final)
    for pattern in case.final:
        if not re.search(pattern, answer, re.IGNORECASE):
            return False, f"final answer does not match /{pattern}/"
    return True, ""


def run_case(case: Case, chat) -> dict:
    world = World(fail_first=case.fail_first)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": case.prompt}]
    calls: list[dict] = []
    final, error, turns, leaked = "", None, 0, False

    for turns in range(1, MAX_TURNS + 1):
        result = chat(messages, tools=TOOLS)
        if result.error:
            error = result.error
            break
        if not result.tool_calls:
            final = result.content
            leaked = bool(LEAK_RE.search(final))
            break
        messages.append(assistant_message(result))
        for tool_call in result.tool_calls:
            try:
                args = json.loads(tool_call.arguments or "{}")
                problem = validate_args(tool_call.name, args)
            except json.JSONDecodeError:
                args, problem = {}, "arguments were not valid JSON"
            output = {"error": f"Invalid call: {problem}"} if problem \
                else world.execute(tool_call.name, args)
            calls.append({"name": tool_call.name, "args": args if isinstance(args, dict) else {},
                          "valid": problem is None, "ok": "error" not in output, "result": output})
            messages.append({"role": "tool", "tool_call_id": tool_call.id,
                             "content": json.dumps(output)})
    else:
        error = f"no final answer after {MAX_TURNS} turns"

    passed, reason = (False, error) if error else check_case(case, calls, final)
    return {
        "id": case.id, "category": case.category, "passed": passed, "reason": reason,
        "format_valid": all(call["valid"] for call in calls) and not leaked,
        "leaked_call_text": leaked, "turns": turns, "calls": calls, "final": final,
    }


def run(ctx: Ctx) -> SuiteResult:
    cases = CASES[: ctx.limit] if ctx.limit else CASES
    items = []
    for case in cases:
        item = run_case(case, ctx.chat)
        item["timing"] = [call.timing() for call in ctx.take_calls()]
        items.append(item)
        print(f"  {'PASS' if item['passed'] else 'FAIL'}  {case.id}  {item['reason']}")

    passed = sum(item["passed"] for item in items)
    by_category: dict[str, list[bool]] = {}
    for item in items:
        by_category.setdefault(item["category"], []).append(item["passed"])
    metrics = {
        "format_valid_rate": round(sum(i["format_valid"] for i in items) / len(items), 3),
        "pass_by_category": {name: f"{sum(vals)}/{len(vals)}" for name, vals in by_category.items()},
    }
    return SuiteResult("tools", passed / len(items), passed, len(items), metrics, items)
