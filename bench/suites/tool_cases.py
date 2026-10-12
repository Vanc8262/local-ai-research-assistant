"""The 30 scripted tool-calling cases.

Each case lists the tool calls that must succeed and patterns the final answer
must contain. `args` are concrete values (also used by the self-test oracle);
`loose` replaces the exact comparison for an argument with a looser check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

MISSING = object()


def contains(text: str) -> Callable[[object], bool]:
    return lambda value: isinstance(value, str) and text.casefold() in value.casefold()


def default_ok(default: str) -> Callable[[object], bool]:
    """Accept the argument being left out or set to its default."""
    return lambda value: value is MISSING or value == default


def anything(value: object) -> bool:
    return value is not MISSING


@dataclass
class Expect:
    tool: str
    args: dict = field(default_factory=dict)
    loose: dict = field(default_factory=dict)


@dataclass
class Case:
    id: str
    category: str               # single | no_tool | arguments | chain | recovery
    prompt: str
    expect: list[Expect] = field(default_factory=list)
    final: list[str] = field(default_factory=list)   # regexes, case-insensitive
    answer: str = "Done."       # what the self-test oracle says at the end
    no_tools: bool = False      # passing requires zero tool calls
    exact: dict = field(default_factory=dict)        # tool -> exact number of successful calls
    fail_first: str | None = None                    # tool whose first call fails once
    oracle: list | None = None  # scripted steps when the default oracle is not enough


CELSIUS = {"unit": default_ok("celsius")}


def event(title: str, date: str, start: str, minutes: int, key: str) -> Expect:
    return Expect("create_event",
                  {"title": title, "date": date, "start_time": start, "duration_minutes": minutes},
                  loose={"title": contains(key)})


CASES: list[Case] = [
    # -- one call, right tool and arguments ------------------------------------
    Case("single-01", "single", "What's the temperature in Tokyo right now, in celsius?",
         [Expect("get_weather", {"city": "Tokyo"}, CELSIUS)], [r"\b21\b"], "It is 21 °C in Tokyo."),
    Case("single-02", "single", "What is ACME trading at?",
         [Expect("get_stock_price", {"ticker": "ACME"})], [r"142\.50?\b"], "ACME is at $142.50."),
    Case("single-03", "single", "Convert 200 EUR to USD.",
         [Expect("convert_currency", {"amount": 200, "from_currency": "EUR", "to_currency": "USD"})],
         [r"\b250\b"], "200 EUR is 250 USD."),
    Case("single-04", "single", "What is 37 * 48 + 19? Use the calculator tool.",
         [Expect("calculator", {"expression": "37 * 48 + 19"}, {"expression": anything})],
         [r"\b1795\b"], "The result is 1795."),
    Case("single-05", "single", "Show me what reports/q3.txt says about Q3 revenue.",
         [Expect("read_file", {"path": "reports/q3.txt"})], [r"\b4200\b"], "Q3 revenue was 4200 units."),
    Case("single-06", "single", "Which files are in the notes directory?",
         [Expect("list_files", {"directory": "notes"}, {"directory": contains("notes")})],
         [r"todo\.md"], "There is one file: notes/todo.md."),
    Case("single-07", "single",
         "Put 'Dentist' on my calendar for 2026-11-03 at 14:30, lasting 45 minutes.",
         [event("Dentist", "2026-11-03", "14:30", 45, "dentist")], [], "Dentist is on your calendar."),
    Case("single-08", "single", "How warm is it in Denver in fahrenheit?",
         [Expect("get_weather", {"city": "Denver", "unit": "fahrenheit"})],
         [r"\b37(\.4)?\b"], "It is 37.4 °F in Denver."),
    Case("single-09", "single", "How much is 5000 JPY in GBP?",
         [Expect("convert_currency", {"amount": 5000, "from_currency": "JPY", "to_currency": "GBP"})],
         [r"\b33\.3"], "5000 JPY is 33.33 GBP."),
    Case("single-10", "single", "Get me the latest GLOBX quote.",
         [Expect("get_stock_price", {"ticker": "GLOBX"})], [r"88\.20?\b"], "GLOBX is at $88.20."),

    # -- no tool is needed; calling one is the failure --------------------------
    Case("notool-01", "no_tool", "What is the capital of France? Answer in one word.",
         final=[r"paris"], answer="Paris", no_tools=True),
    Case("notool-02", "no_tool", "Rewrite this sentence in the passive voice: 'The cat chased the mouse.'",
         final=[r"was chased"], answer="The mouse was chased by the cat.", no_tools=True),
    Case("notool-03", "no_tool", "Explain in one sentence what a unit test is.",
         final=[r"\btest"], answer="A unit test checks one small piece of code in isolation.", no_tools=True),
    Case("notool-04", "no_tool",
         "I might check the weather in Paris later. Don't look anything up now, just reply OK.",
         final=[r"\bok(ay)?\b"], answer="OK", no_tools=True),

    # -- arguments must be converted into the schema's format -------------------
    Case("args-01", "arguments",
         "Book 'Team sync' for November 9th, 2026 at 9am. It runs an hour and a half.",
         [event("Team sync", "2026-11-09", "09:00", 90, "team sync")], [], "Team sync is booked."),
    Case("args-02", "arguments", "whats it like in lagos?? use Fahrenheit pls",
         [Expect("get_weather", {"city": "Lagos", "unit": "fahrenheit"})],
         [r"\b8[78]\b"], "It is 87.8 °F in Lagos."),
    Case("args-03", "arguments", "Convert twelve hundred and fifty dollars to euros.",
         [Expect("convert_currency", {"amount": 1250, "from_currency": "USD", "to_currency": "EUR"})],
         [r"\b1000\b"], "That is 1000 EUR."),
    Case("args-04", "arguments",
         "Add two events: 'Gym' on 2026-11-10 at 07:00 for 60 minutes, and 'Lunch with Sam' "
         "the same day at half past twelve for 45 minutes.",
         [event("Gym", "2026-11-10", "07:00", 60, "gym"),
          event("Lunch with Sam", "2026-11-10", "12:30", 45, "lunch")],
         [], "Both events are added.", exact={"create_event": 2}),

    # -- chains: later calls depend on earlier results --------------------------
    Case("chain-01", "chain", "Read config/settings.json and tell me the temperature in my home city.",
         [Expect("read_file", {"path": "config/settings.json"}),
          Expect("get_weather", {"city": "Oslo"}, CELSIUS)],
         [r"(-|−|minus )\s?2\b"], "It is -2 °C in Oslo."),
    Case("chain-02", "chain",
         "What is the combined price of the stocks on the watchlist in config/settings.json?",
         [Expect("read_file", {"path": "config/settings.json"}),
          Expect("get_stock_price", {"ticker": "ACME"}),
          Expect("get_stock_price", {"ticker": "INITECH"})],
         [r"155\.25"], "Together they cost $155.25."),
    Case("chain-03", "chain",
         "According to reports/q3.txt, by what percent did revenue grow from Q2 to Q3? "
         "Use the calculator for the arithmetic.",
         [Expect("read_file", {"path": "reports/q3.txt"}),
          Expect("calculator", {"expression": "(4200 - 3500) / 3500 * 100"}, {"expression": anything})],
         [r"\b20(\.0+)?\b"], "Revenue grew 20%."),
    Case("chain-04", "chain",
         "Read data/trip.txt, then tell me the trip budget in USD and the temperature at the destination.",
         [Expect("read_file", {"path": "data/trip.txt"}),
          Expect("convert_currency", {"amount": 300000, "from_currency": "JPY", "to_currency": "USD"}),
          Expect("get_weather", {"city": "Tokyo"}, CELSIUS)],
         [r"\b3000\b", r"\b21\b"], "The budget is 3000 USD and it is 21 °C in Tokyo."),
    Case("chain-05", "chain",
         "Find the file in the notes directory and create a calendar event for each dated item in it. "
         "Skip items without a date.",
         [Expect("read_file", {"path": "notes/todo.md"}),
          event("Call plumber", "2026-11-12", "10:00", 30, "plumber"),
          event("Review budget", "2026-11-14", "16:00", 60, "budget")],
         [], "Two events created.", exact={"create_event": 2}),
    Case("chain-06", "chain", "How many whole shares of INITECH can I buy with 100 GBP?",
         [Expect("get_stock_price", {"ticker": "INITECH"}),
          Expect("convert_currency", {"amount": 100, "from_currency": "GBP", "to_currency": "USD"})],
         [r"\b11\b(?!\.\d)"], "You can buy 11 whole shares."),
    Case("chain-07", "chain",
         "Compare the temperature in Paris and Lagos in celsius. What is the difference in degrees?",
         [Expect("get_weather", {"city": "Paris"}, CELSIUS),
          Expect("get_weather", {"city": "Lagos"}, CELSIUS)],
         [r"\b17\b"], "Lagos is 17 degrees warmer."),
    Case("chain-08", "chain",
         "Read config/settings.json. Then convert the price of the first watchlist stock "
         "into the configured currency.",
         [Expect("read_file", {"path": "config/settings.json"}),
          Expect("get_stock_price", {"ticker": "ACME"}),
          Expect("convert_currency", {"amount": 142.5, "from_currency": "USD", "to_currency": "EUR"})],
         [r"\b114(\.0+)?\b"], "ACME costs 114 EUR."),

    # -- recovery: the first attempt fails and the model has to adapt -----------
    Case("recover-01", "recovery", "Open reports/q3_summary.txt and tell me the Q3 revenue.",
         [Expect("read_file", {"path": "reports/q3.txt"})], [r"\b4200\b"], "Q3 revenue was 4200 units."),
    Case("recover-02", "recovery", "What's the price of ticker INITEC? I may have misspelled it.",
         [Expect("get_stock_price", {"ticker": "INITECH"})], [r"12\.75"], "INITECH is at $12.75."),
    Case("recover-03", "recovery", "What's the temperature in Paris in celsius?",
         [Expect("get_weather", {"city": "Paris"}, CELSIUS)], [r"\b14\b"], "It is 14 °C in Paris.",
         fail_first="get_weather",
         oracle=[[("get_weather", {"city": "Paris"})], [("get_weather", {"city": "Paris"})],
                 "It is 14 °C in Paris."]),
    Case("recover-04", "recovery",
         "Schedule 'Review' on 2026-02-30 at 10:00 for 30 minutes. If the calendar rejects "
         "that date, use the last day of that month instead.",
         [event("Review", "2026-02-28", "10:00", 30, "review")], [], "Review is scheduled for 2026-02-28."),
]

assert len(CASES) == 30 and len({case.id for case in CASES}) == 30
