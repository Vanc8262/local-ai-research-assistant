"""Vision suite: five generated images (charts and screenshots) with one
question each. The images are drawn by this file, so the right answers are known
and the same images are produced on every machine."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .base import Ctx, SuiteResult

W, H = 800, 500
INK, GRID, BAR, LINE = "#1f2933", "#cbd2d9", "#3b6fb6", "#b6483b"


def _font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _canvas(title: str, background: str = "white", ink: str = INK) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), background)
    draw = ImageDraw.Draw(image)
    draw.text((40, 24), title, fill=ink, font=_font(24))
    return image, draw


def bar_chart(title: str, labels: list[str], values: list[int], show_values: bool) -> Image.Image:
    image, draw = _canvas(title)
    left, right, top, bottom = 80, W - 40, 90, H - 70
    top_value = max(values) * 1.15
    for step in range(5):
        y = bottom - (bottom - top) * step / 4
        draw.line([(left, y), (right, y)], fill=GRID)
        draw.text((left - 10, y), f"{top_value * step / 4:.0f}", fill=INK, font=_font(14), anchor="rm")
    slot = (right - left) / len(values)
    for i, (label, value) in enumerate(zip(labels, values)):
        x0, x1 = left + slot * i + slot * 0.2, left + slot * (i + 1) - slot * 0.2
        y = bottom - (bottom - top) * value / top_value
        draw.rectangle([x0, y, x1, bottom], fill=BAR)
        draw.text(((x0 + x1) / 2, bottom + 12), label, fill=INK, font=_font(18), anchor="mt")
        if show_values:
            draw.text(((x0 + x1) / 2, y - 6), str(value), fill=INK, font=_font(18), anchor="mb")
    draw.line([(left, bottom), (right, bottom)], fill=INK, width=2)
    return image


def line_chart(title: str, labels: list[str], values: list[int]) -> Image.Image:
    image, draw = _canvas(title)
    left, right, top, bottom = 80, W - 50, 90, H - 70
    top_value = 30
    for step in range(4):
        y = bottom - (bottom - top) * step / 3
        draw.line([(left, y), (right, y)], fill=GRID)
        draw.text((left - 10, y), f"{top_value * step / 3:.0f}", fill=INK, font=_font(14), anchor="rm")
    gap = (right - left) / (len(values) - 1)
    points = [(left + gap * i, bottom - (bottom - top) * v / top_value) for i, v in enumerate(values)]
    draw.line(points, fill=LINE, width=4)
    for (x, y), label in zip(points, labels):
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=LINE)
        draw.text((x, bottom + 12), label, fill=INK, font=_font(18), anchor="mt")
    draw.line([(left, bottom), (right, bottom)], fill=INK, width=2)
    return image


def table_image(title: str, header: list[str], rows: list[list[str]]) -> Image.Image:
    image, draw = _canvas(title)
    x0, y0, col_w, row_h = 60, 100, 220, 60
    for r, cells in enumerate([header] + rows):
        y = y0 + r * row_h
        if r == 0:
            draw.rectangle([x0, y, x0 + col_w * len(header), y + row_h], fill="#e4e7eb")
        for c, cell in enumerate(cells):
            draw.text((x0 + c * col_w + 16, y + row_h / 2), cell, fill=INK, font=_font(22), anchor="lm")
        draw.line([(x0, y + row_h), (x0 + col_w * len(header), y + row_h)], fill=GRID, width=2)
    return image


def terminal_image(lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (W, H), "#111418")
    draw = ImageDraw.Draw(image)
    for i, text in enumerate(lines):
        draw.text((24, 30 + i * 34), text, fill="#e6e6e6", font=_font(19))
    return image


def build_items() -> list[dict]:
    return [
        {"id": "bar-highest", "question": "Which region has the highest value? Answer with the region name only.",
         "image": bar_chart("Units sold by region", ["North", "South", "East", "West", "Central"],
                            [42, 67, 55, 38, 61], show_values=False),
         "answer": r"\bsouth\b", "reject": r"\b(north|east|west|central)\b"},
        {"id": "bar-read-value", "question": "What is the value for Mar? Answer with the number only.",
         "image": bar_chart("Monthly signups", ["Jan", "Feb", "Mar", "Apr", "May"],
                            [120, 135, 180, 160, 210], show_values=True),
         "answer": r"\b180\b", "reject": r"\b(120|135|160|210)\b"},
        {"id": "line-peak", "question": "In which month does the line reach its highest point? Answer with the month only.",
         "image": line_chart("Support tickets per month", ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
                             [12, 18, 15, 27, 22, 19]),
         "answer": r"\bapr(il)?\b", "reject": r"\b(jan|feb|mar|jun)"},
        {"id": "table-cell", "question": "According to the table, what is the speed for Bravo? Answer with the number only.",
         "image": table_image("Model comparison", ["Model", "Context", "Speed (tok/s)"],
                              [["Alpha", "8K", "41"], ["Bravo", "16K", "33"], ["Cedar", "32K", "27"]]),
         "answer": r"\b33\b", "reject": r"\b(41|27)\b"},
        {"id": "terminal-error",
         "question": "This is a terminal screenshot. What type of error was raised, and on which line of summary.py?",
         "image": terminal_image([
             "$ python run_report.py",
             "Traceback (most recent call last):",
             '  File "run_report.py", line 42, in <module>',
             "    total = summarize(rows)",
             '  File "summary.py", line 17, in summarize',
             '    totals[key] += float(row["amount"])',
             "ValueError: could not convert string to float: 'n/a'",
         ]),
         "answer": r"valueerror(?s:.*)\b17\b|\b17\b(?s:.*)valueerror", "reject": None},
    ]


def to_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def is_correct(item: dict, answer: str) -> bool:
    if not re.search(item["answer"], answer, re.IGNORECASE):
        return False
    return not (item["reject"] and re.search(item["reject"], answer, re.IGNORECASE))


def run(ctx: Ctx) -> SuiteResult:
    if not ctx.model.vision:
        return SuiteResult("vision", None, skipped="model is not marked vision = true in bench.toml")
    image_dir = Path(ctx.run_dir) / "vision_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    specs = build_items()
    if ctx.limit:
        specs = specs[: ctx.limit]
    items = []
    for spec in specs:
        spec["image"].save(image_dir / f"{spec['id']}.png")
        result = ctx.chat([{"role": "user", "content": [
            {"type": "text", "text": spec["question"]},
            {"type": "image_url", "image_url": {"url": to_data_url(spec["image"])}},
        ]}])
        item = {"id": spec["id"], "question": spec["question"], "answer": result.content,
                "error": result.error, "passed": not result.error and is_correct(spec, result.content),
                "needs_manual_review": True,
                "timing": [call.timing() for call in ctx.take_calls()]}
        items.append(item)
        print(f"  {'PASS' if item['passed'] else 'FAIL'}  {spec['id']}  {result.content[:70]!r}")
    passed = sum(item["passed"] for item in items)
    return SuiteResult("vision", passed / len(items), passed, len(items), {}, items)
