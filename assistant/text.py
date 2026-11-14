"""Text helpers shared by the verifier and the search layers: page text from HTML, and the
passages of a text that share most words with a claim or query.

This module imports nothing from the assistant package, so web_pages.py can use it inside
LDR's venv (see ldr_runner.py) without pulling in the gateway or the model client.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

STOPWORDS = set("""the and for with that this from into over than then they them their there these those
which while where when what about also have has had been being were will would could should can
such more most many much some other only just each both very like using used uses new""".split())
PASSAGE_CHARS, PASSAGE_STEP, PASSAGES = 700, 500, 3


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.+_-]{2,}", text.lower()) if w not in STOPWORDS}


def best_passages(claim: str, source: str) -> list[str]:
    """The windows of the source that share most words with the claim. A source that fits
    in two windows, such as a ~1,000-character codex chunk, is passed whole."""
    if len(source) <= 2 * PASSAGE_CHARS:
        return [source]
    words = keywords(claim)
    starts = list(range(0, len(source) - PASSAGE_CHARS, PASSAGE_STEP)) + [len(source) - PASSAGE_CHARS]
    windows = [source[start:start + PASSAGE_CHARS] for start in starts]   # the last one reaches the end
    scored = sorted(windows, key=lambda w: sum(word in w.lower() for word in words), reverse=True)
    return scored[:PASSAGES]
