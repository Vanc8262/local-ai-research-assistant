import pytest

from bench.client import ChatResult
from bench.suites.code_editing import build_prompt, extract_code, load_tasks, run_task, run_tests

TASKS = load_tasks()


def test_there_are_ten_tasks():
    assert len(TASKS) == 10


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_starter_fails_and_solution_passes(task):
    assert not run_tests(task, task.source)[0], "the starter file should fail its tests"
    passed, output = run_tests(task, task.solution)
    assert passed, output


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_prompt_shows_file_and_tests_but_not_solution(task):
    prompt = build_prompt(task)
    assert task.source in prompt and task.tests in prompt and task.instruction in prompt
    assert task.solution not in prompt


def replies(*texts):
    queue = list(texts)
    return lambda messages, tools=None, max_tokens=None: ChatResult(content=queue.pop(0))


def fenced(code):
    return f"Sure.\n\n```python\n{code}```\nDone."


def test_pass_on_first_try():
    task = TASKS[0]
    item = run_task(task, replies(fenced(task.solution)), attempts=2)
    assert item["passed"] and item["passed_first_try"] and item["attempts_used"] == 1


def test_pass_on_retry_uses_test_output():
    task = TASKS[0]
    seen = []

    def chat(messages, tools=None, max_tokens=None):
        seen.append(messages[-1]["content"])
        return ChatResult(content=fenced(task.source if len(seen) == 1 else task.solution))

    item = run_task(task, chat, attempts=2)
    assert item["passed"] and not item["passed_first_try"] and item["attempts_used"] == 2
    assert "The tests failed" in seen[1] and "assert" in seen[1]


def test_single_attempt_does_not_retry():
    task = TASKS[0]
    item = run_task(task, replies(fenced(task.source)), attempts=1)
    assert not item["passed"] and item["attempts_used"] == 1 and item["reason"]


def test_reply_without_code_fails():
    item = run_task(TASKS[0], replies("I would change the range.", "Still no code."), attempts=2)
    assert not item["passed"] and not item["format_valid"]


def test_syntax_error_fails_cleanly():
    item = run_task(TASKS[0], replies(fenced("def chunk(items, size:\n    return\n")), attempts=1)
    assert not item["passed"]


def test_infinite_loop_is_stopped(monkeypatch):
    monkeypatch.setattr("bench.suites.code_editing.TEST_TIMEOUT_S", 3)
    passed, output = run_tests(TASKS[0], "while True:\n    pass\n")
    assert not passed and "did not finish" in output


def test_model_error_is_reported():
    item = run_task(TASKS[0], lambda m, tools=None, max_tokens=None: ChatResult(error="boom"), attempts=2)
    assert not item["passed"] and item["reason"] == "boom"


def test_extract_code_variants():
    assert extract_code("```python\nx = 1\n```") == ("x = 1\n", True)
    assert extract_code("```py\nx = 1\n```")[0] == "x = 1\n"
    assert extract_code("```\nx = 1\n```")[0] == "x = 1\n"
    # the last valid block wins, and non-Python blocks are ignored
    reply = "```python\nold = 1\n```\ntext\n```bash\npytest\n```\n```python\nnew = 2\n```"
    assert extract_code(reply)[0] == "new = 2\n"
    assert extract_code("def f():\n    return 1\n") == ("def f():\n    return 1\n", False)
    assert extract_code("no code here") == (None, False)
    assert extract_code("") == (None, False)
