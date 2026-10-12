import json

import pytest

from bench.client import ChatResult, ToolCall
from bench.suites.tool_calling import check_case, run_case
from bench.suites.tool_cases import CASES
from bench.suites.tool_world import TOOLS, World, safe_eval, validate_args
from fake_model import oracle_steps


class Scripted:
    """A chat function that replays fixed steps: a list of tool calls, or final text."""

    def __init__(self, steps):
        self.steps = list(steps)

    def __call__(self, messages, tools=None, max_tokens=None):
        step = self.steps.pop(0) if self.steps else "I have nothing more to add."
        if isinstance(step, str):
            return ChatResult(content=step)
        return ChatResult(tool_calls=[
            ToolCall(id=f"c{i}", name=name, arguments=args if isinstance(args, str) else json.dumps(args))
            for i, (name, args) in enumerate(step)])


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_case_is_passable(case):
    item = run_case(case, Scripted(oracle_steps(case)))
    assert item["passed"], item["reason"]
    assert item["format_valid"]


@pytest.mark.parametrize("case", [c for c in CASES if not c.no_tools], ids=lambda c: c.id)
def test_answering_without_tools_fails_tool_cases(case):
    assert not run_case(case, Scripted([case.answer]))["passed"]


@pytest.mark.parametrize("case", [c for c in CASES if c.no_tools], ids=lambda c: c.id)
def test_needless_tool_call_fails_no_tool_cases(case):
    item = run_case(case, Scripted([[("get_weather", {"city": "Paris"})], case.answer]))
    assert not item["passed"] and "no tool was needed" in item["reason"]


def by_id(case_id):
    return next(c for c in CASES if c.id == case_id)


def test_wrong_argument_fails():
    item = run_case(by_id("single-03"), Scripted(
        [[("convert_currency", {"amount": 200, "from_currency": "USD", "to_currency": "EUR"})], "250"]))
    assert not item["passed"]


def test_wrong_final_answer_fails():
    item = run_case(by_id("single-01"), Scripted([[("get_weather", {"city": "Tokyo"})], "It is 25 degrees."]))
    assert not item["passed"] and "final answer" in item["reason"]


def test_thousands_separator_is_accepted():
    steps = oracle_steps(by_id("chain-04"))[:-1] + ["The budget is 3,000 USD and Tokyo is at 21 °C."]
    assert run_case(by_id("chain-04"), Scripted(steps))["passed"]


def test_fractional_shares_do_not_count_as_whole():
    steps = oracle_steps(by_id("chain-06"))[:-1] + ["You can buy 11.76 shares."]
    assert not run_case(by_id("chain-06"), Scripted(steps))["passed"]


def test_extra_event_fails_exact_count():
    case = by_id("chain-05")
    steps = oracle_steps(case)[:-1] + [[("create_event", {"title": "Buy milk", "date": "2026-11-15",
                                                           "start_time": "09:00", "duration_minutes": 10})], "Done"]
    assert "exactly 2" in run_case(case, Scripted(steps))["reason"]


def test_recovery_after_error_passes_but_giving_up_fails():
    case = by_id("recover-02")
    recovered = run_case(case, Scripted([[("get_stock_price", {"ticker": "INITEC"})],
                                         [("get_stock_price", {"ticker": "INITECH"})], "It is $12.75."]))
    assert recovered["passed"] and recovered["format_valid"]
    gave_up = run_case(case, Scripted([[("get_stock_price", {"ticker": "INITEC"})], "I could not find it."]))
    assert not gave_up["passed"]


def test_transient_failure_needs_a_retry():
    case = by_id("recover-03")
    assert not run_case(case, Scripted([[("get_weather", {"city": "Paris"})], "It is 14 °C."]))["passed"]


def test_malformed_arguments_are_flagged_and_reported_to_the_model():
    item = run_case(by_id("single-02"), Scripted(
        [[("get_stock_price", '{"ticker": "ACME"')], [("get_stock_price", {"ticker": "ACME"})], "$142.50"]))
    assert item["passed"] and not item["format_valid"]
    assert "not valid JSON" in item["calls"][0]["result"]["error"]


def test_tool_call_written_as_text_is_flagged():
    item = run_case(by_id("single-02"), Scripted(['<tool_call>{"name": "get_stock_price"}</tool_call>']))
    assert not item["passed"] and item["leaked_call_text"] and not item["format_valid"]


def test_endless_tool_calls_stop():
    item = run_case(by_id("single-01"), Scripted([[("get_weather", {"city": "Tokyo"})]] * 20))
    assert not item["passed"] and "no final answer" in item["reason"]


def test_model_error_is_reported():
    item = run_case(by_id("single-01"), lambda messages, tools=None, max_tokens=None: ChatResult(error="boom"))
    assert not item["passed"] and item["reason"] == "boom"


@pytest.mark.parametrize("name, args, ok", [
    ("get_weather", {"city": "Paris"}, True),
    ("get_weather", {"city": "Paris", "unit": "kelvin"}, False),
    ("get_weather", {}, False),
    ("get_weather", {"city": "Paris", "country": "FR"}, False),
    ("convert_currency", {"amount": "200", "from_currency": "EUR", "to_currency": "USD"}, False),
    ("create_event", {"title": "x", "date": "2026-11-03", "start_time": "09:00", "duration_minutes": 30.5}, False),
    ("create_event", {"title": "x", "date": "2026-11-03", "start_time": "09:00", "duration_minutes": True}, False),
    ("no_such_tool", {}, False),
    ("list_files", {}, True),
    ("get_weather", ["Paris"], False),
])
def test_validate_args(name, args, ok):
    assert (validate_args(name, args) is None) is ok


def test_world_values():
    world = World()
    assert world.execute("convert_currency", {"amount": 5000, "from_currency": "JPY", "to_currency": "GBP"})["amount"] == 33.33
    assert world.execute("get_weather", {"city": "denver", "unit": "fahrenheit"})["temperature"] == 37.4
    assert "error" in world.execute("create_event", {"title": "x", "date": "2026-02-30", "start_time": "10:00", "duration_minutes": 30})
    assert "error" in world.execute("create_event", {"title": "x", "date": "2026-11-09", "start_time": "9:00", "duration_minutes": 30})
    assert "error" in world.execute("read_file", {"path": "reports/q3_summary.txt"})
    assert world.execute("list_files", {"directory": "notes/"})["files"] == ["notes/todo.md"]
    assert len(world.execute("list_files", {})["files"]) == 4
    assert world.events == []


def test_safe_eval():
    assert safe_eval("37 * 48 + 19") == 1795
    assert safe_eval("(4200 - 3500) / 3500 * 100") == pytest.approx(20)
    for bad in ("__import__('os')", "1/0", "a + 1", "9 ** 999", "len('x')"):
        with pytest.raises(ValueError):
            safe_eval(bad)


def test_tool_schemas_are_well_formed():
    assert len(TOOLS) == 7
    for tool in TOOLS:
        params = tool["function"]["parameters"]
        assert set(params["required"]) <= set(params["properties"])


def test_check_case_needs_distinct_calls_for_each_expectation():
    case = by_id("chain-07")
    paris = {"name": "get_weather", "args": {"city": "Paris"}, "valid": True, "ok": True, "result": {}}
    assert not check_case(case, [paris, paris], "17")[0]
