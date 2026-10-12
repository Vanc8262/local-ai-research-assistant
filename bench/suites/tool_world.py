"""A small fictional world behind the mock tools. Every value is made up and
fixed, so answers can be checked exactly and no model can know them in advance."""
from __future__ import annotations

import ast
import datetime as dt
import json
import operator
import re

WEATHER_C = {"paris": 14, "tokyo": 21, "denver": 3, "lagos": 31, "oslo": -2}
STOCKS = {"ACME": 142.50, "GLOBX": 88.20, "INITECH": 12.75}
USD_PER = {"USD": 1.0, "EUR": 1.25, "GBP": 1.5, "JPY": 0.01}
FILES = {
    "config/settings.json": json.dumps(
        {"home_city": "Oslo", "currency": "EUR", "watchlist": ["ACME", "INITECH"]}, indent=2),
    "reports/q3.txt": "Q3 revenue was 4200 units. Q2 revenue was 3500 units.",
    "data/trip.txt": "Trip to Tokyo on 2026-12-05. Budget: 300000 JPY.",
    "notes/todo.md": ("- 2026-11-12 10:00 Call plumber (30 min)\n"
                      "- 2026-11-14 16:00 Review budget (60 min)\n"
                      "- Buy milk\n"),
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False}}}


TOOLS = [
    _tool("get_weather", "Current temperature for a city.",
          {"city": {"type": "string", "description": "City name, e.g. Paris"},
           "unit": {"type": "string", "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature unit. Defaults to celsius."}},
          ["city"]),
    _tool("get_stock_price", "Latest price in USD for a stock ticker.",
          {"ticker": {"type": "string", "description": "Upper-case ticker symbol, e.g. ACME"}},
          ["ticker"]),
    _tool("convert_currency", "Convert an amount between currencies.",
          {"amount": {"type": "number"},
           "from_currency": {"type": "string", "description": "ISO code: USD, EUR, GBP or JPY"},
           "to_currency": {"type": "string", "description": "ISO code: USD, EUR, GBP or JPY"}},
          ["amount", "from_currency", "to_currency"]),
    _tool("calculator", "Evaluate an arithmetic expression with + - * / ** % and parentheses.",
          {"expression": {"type": "string", "description": "e.g. (4200 - 3500) / 3500 * 100"}},
          ["expression"]),
    _tool("list_files", "List file paths, optionally inside one directory.",
          {"directory": {"type": "string", "description": "Directory name, e.g. notes. Omit for all files."}},
          []),
    _tool("read_file", "Read a text file by its exact path.",
          {"path": {"type": "string", "description": "Path as returned by list_files"}},
          ["path"]),
    _tool("create_event", "Add an event to the calendar.",
          {"title": {"type": "string"},
           "date": {"type": "string", "description": "ISO date, YYYY-MM-DD"},
           "start_time": {"type": "string", "description": "24-hour time, zero-padded HH:MM"},
           "duration_minutes": {"type": "integer"}},
          ["title", "date", "start_time", "duration_minutes"]),
]
SCHEMAS = {tool["function"]["name"]: tool["function"]["parameters"] for tool in TOOLS}

_TYPES = {"string": str, "number": (int, float), "integer": int}


def validate_args(name: str, args: object) -> str | None:
    """Return None if args fit the tool's schema, else a short reason."""
    schema = SCHEMAS.get(name)
    if schema is None:
        return f"unknown tool '{name}'"
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    for key in schema["required"]:
        if key not in args:
            return f"missing required argument '{key}'"
    for key, value in args.items():
        spec = schema["properties"].get(key)
        if spec is None:
            return f"unexpected argument '{key}'"
        expected = _TYPES[spec["type"]]
        if isinstance(value, bool) or not isinstance(value, expected):
            return f"argument '{key}' must be of type {spec['type']}"
        if "enum" in spec and value not in spec["enum"]:
            return f"argument '{key}' must be one of {spec['enum']}"
    return None


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(expression: str) -> float:
    """Arithmetic only; anything else raises ValueError."""
    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise ValueError("exponent too large")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.operand))
        raise ValueError("only numbers and + - * / ** % ( ) are allowed")
    try:
        return walk(ast.parse(expression.replace(",", "").replace("^", "**"), mode="eval").body)
    except (SyntaxError, ZeroDivisionError, TypeError) as exc:
        raise ValueError(str(exc)) from exc


class World:
    """Executes tool calls. `fail_first` names a tool whose first call fails once."""

    def __init__(self, fail_first: str | None = None) -> None:
        self.events: list[dict] = []
        self._pending_failure = fail_first

    def execute(self, name: str, args: dict) -> dict:
        if self._pending_failure == name:
            self._pending_failure = None
            return {"error": "Service temporarily unavailable. Please try the same call again."}
        return getattr(self, f"_{name}")(**args)

    def _get_weather(self, city: str, unit: str = "celsius") -> dict:
        celsius = WEATHER_C.get(city.strip().lower())
        if celsius is None:
            known = ", ".join(sorted(c.title() for c in WEATHER_C))
            return {"error": f"Unknown city '{city}'. Known cities: {known}"}
        temp = celsius if unit == "celsius" else round(celsius * 9 / 5 + 32, 1)
        return {"city": city.strip().title(), "temperature": temp, "unit": unit}

    def _get_stock_price(self, ticker: str) -> dict:
        if ticker not in STOCKS:
            return {"error": f"Unknown ticker '{ticker}'. Known tickers: {', '.join(STOCKS)}"}
        return {"ticker": ticker, "price_usd": STOCKS[ticker]}

    def _convert_currency(self, amount: float, from_currency: str, to_currency: str) -> dict:
        for code in (from_currency, to_currency):
            if code not in USD_PER:
                return {"error": f"Unknown currency '{code}'. Supported codes: {', '.join(USD_PER)}"}
        value = round(amount * USD_PER[from_currency] / USD_PER[to_currency], 2)
        return {"amount": value, "currency": to_currency}

    def _calculator(self, expression: str) -> dict:
        try:
            return {"result": round(safe_eval(expression), 6)}
        except ValueError as exc:
            return {"error": f"Could not evaluate: {exc}"}

    def _list_files(self, directory: str = "") -> dict:
        prefix = directory.strip().strip("/")
        paths = [p for p in FILES if not prefix or p.startswith(prefix + "/")]
        if not paths:
            return {"error": f"No such directory '{directory}'. Directories: config, data, notes, reports"}
        return {"files": paths}

    def _read_file(self, path: str) -> dict:
        if path not in FILES:
            return {"error": f"File not found: {path}. Use list_files to see available files."}
        return {"path": path, "content": FILES[path]}

    def _create_event(self, title: str, date: str, start_time: str, duration_minutes: int) -> dict:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
            dt.date.fromisoformat(date)
        except ValueError:
            return {"error": f"Invalid date '{date}'. Use a real calendar date as YYYY-MM-DD."}
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", start_time):
            return {"error": f"Invalid start_time '{start_time}'. Use zero-padded 24-hour HH:MM."}
        if duration_minutes <= 0:
            return {"error": "duration_minutes must be positive."}
        event = {"title": title, "date": date, "start_time": start_time,
                 "duration_minutes": duration_minutes}
        self.events.append(event)
        return {"status": "created", "event": event}
