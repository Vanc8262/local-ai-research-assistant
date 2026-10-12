"""Code-editing suite: the model gets a small file, its tests and a task, and
returns the whole updated file. The harness runs the tests to score it.

Model-written code is executed, so each attempt runs in a throwaway directory
in a separate process with a time limit.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .base import Ctx, SuiteResult

TASKS_DIR = Path(__file__).resolve().parent.parent / "data" / "code_tasks"
SYSTEM = ("You are a careful Python developer. Make the requested change and reply with the "
          "complete updated file in a single ```python code block. Do not edit the tests.")
FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
TEST_TIMEOUT_S = 60


@dataclass
class Task:
    id: str
    instruction: str
    source_name: str
    source: str
    test_name: str
    tests: str
    solution: str


def load_tasks() -> list[Task]:
    tasks = []
    for folder in sorted(p for p in TASKS_DIR.iterdir() if p.is_dir()):
        test_file = next(folder.glob("test_*.py"))
        source_file = folder / test_file.name.removeprefix("test_")
        tasks.append(Task(
            id=folder.name,
            instruction=(folder / "task.md").read_text(encoding="utf-8").strip(),
            source_name=source_file.name, source=source_file.read_text(encoding="utf-8"),
            test_name=test_file.name, tests=test_file.read_text(encoding="utf-8"),
            solution=(folder / "solution.py").read_text(encoding="utf-8"),
        ))
    return tasks


def build_prompt(task: Task) -> str:
    return (f"Task:\n{task.instruction}\n\n"
            f"File `{task.source_name}`:\n```python\n{task.source}```\n\n"
            f"Tests in `{task.test_name}` (do not edit):\n```python\n{task.tests}```")


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except (SyntaxError, ValueError):
        return False


def extract_code(reply: str) -> tuple[str | None, bool]:
    """Return (code, followed_format). Prefers the last fenced block that parses."""
    blocks = [body for lang, body in FENCE_RE.findall(reply) if lang.lower() in ("", "python", "py")]
    for body in reversed(blocks):
        if body.strip() and _parses(body):
            return body, True
    if blocks:
        return blocks[-1], True       # fenced but not valid Python; let the tests report it
    if reply.strip() and "def " in reply and _parses(reply):
        return reply, False           # bare code without a fence
    return None, False


def run_tests(task: Task, code: str) -> tuple[bool, str]:
    """Run the task's tests against `code` in a temporary directory."""
    workdir = Path(tempfile.mkdtemp(prefix="bench_code_"))
    try:
        (workdir / task.source_name).write_text(code, encoding="utf-8")
        (workdir / task.test_name).write_text(task.tests, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider",
                 task.test_name],
                cwd=workdir, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=TEST_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return False, f"tests did not finish within {TEST_TIMEOUT_S} seconds"
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output[-1500:]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_task(task: Task, chat, attempts: int) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_prompt(task)}]
    item = {"id": task.id, "passed": False, "passed_first_try": False, "attempts_used": 0,
            "format_valid": True, "reason": "", "replies": []}
    for attempt in range(1, attempts + 1):
        item["attempts_used"] = attempt
        result = chat(messages)
        if result.error:
            item["reason"] = result.error
            break
        item["replies"].append(result.content)
        code, followed_format = extract_code(result.content)
        item["format_valid"] = item["format_valid"] and followed_format
        if code is None:
            output = "The reply did not contain a Python code block."
        else:
            passed, output = run_tests(task, code)
            if passed:
                item.update(passed=True, passed_first_try=attempt == 1, reason="")
                break
        item["reason"] = output.splitlines()[-1] if output else "tests failed"
        messages.append({"role": "assistant", "content": result.content})
        messages.append({"role": "user", "content":
                         f"The tests failed:\n```\n{output}\n```\n"
                         "Reply with the complete corrected file in a single ```python code block."})
    return item


def run(ctx: Ctx) -> SuiteResult:
    tasks = load_tasks()
    if ctx.limit:
        tasks = tasks[: ctx.limit]
    items = []
    for task in tasks:
        item = run_task(task, ctx.chat, max(1, ctx.cfg.code_attempts))
        item["timing"] = [call.timing() for call in ctx.take_calls()]
        items.append(item)
        print(f"  {'PASS' if item['passed'] else 'FAIL'}  {task.id}  "
              f"(attempts: {item['attempts_used']})  {item['reason']}")
    passed = sum(item["passed"] for item in items)
    metrics = {
        "passed_first_try": sum(item["passed_first_try"] for item in items),
        "attempts_allowed": max(1, ctx.cfg.code_attempts),
        "format_valid_rate": round(sum(i["format_valid"] for i in items) / len(items), 3),
    }
    return SuiteResult("code", passed / len(items), passed, len(items), metrics, items)
