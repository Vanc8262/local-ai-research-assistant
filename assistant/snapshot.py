"""Search snapshots: record what the web searches returned during an eval, then replay them.

The 2026-11-14 eval review found that results moved with web state nobody controlled
(SearXNG engines suspended, Tavily filling in, a cache moved by hand), so a change to the
checker or the writer could not be told apart from a change in what the searches returned.

`record` saves every page search a research run makes (query, and the results with their page
passages) to <folder>/searches.jsonl. `replay` answers each search from that file and never
touches the network. The writer's queries vary from run to run, so a replayed query that was
never recorded gets the recorded query sharing the most keywords with it (at least
MIN_OVERLAP of the smaller set), or nothing. Counts of exact, nearest and missed answers are
kept, so a replay can say how close it was.

Covered: the page search that local and offline modes use (web_pages.search_pages, through
the router and the Tavily fallback). Not covered: hybrid mode, whose Tavily search runs inside
LDR, and codex mode, which reads the local index and needs no snapshot.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .text import STOPWORDS

MODES = ("record", "replay")
MIN_OVERLAP = 0.5


def _words(query: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() or c in "._-" else " " for c in query).split()
            if len(w) > 2 and w not in STOPWORDS}


class SearchSnapshot:
    def __init__(self, folder: str | Path, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"snapshot mode must be one of {MODES}, not {mode!r}")
        self.folder, self.mode = Path(folder), mode
        self.path = self.folder / "searches.jsonl"
        self.counts = {"recorded": 0, "exact": 0, "nearest": 0, "missed": 0}
        self._lock = threading.Lock()
        self._saved: list[dict] = []
        if mode == "replay":
            if not self.path.exists():
                raise FileNotFoundError(f"no recorded searches at {self.path}")
            self._saved = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

    def wrap(self, search):
        """`search(text) -> results`, recorded or replayed."""
        def recorded(text: str) -> list[dict]:
            results = search(text)
            with self._lock:
                self.folder.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"query": text, "results": results}, ensure_ascii=False) + "\n")
                self.counts["recorded"] += 1
            return results
        return recorded if self.mode == "record" else self.replay

    def replay(self, text: str) -> list[dict]:
        wanted = " ".join(text.lower().split())
        with self._lock:
            for entry in self._saved:
                if " ".join(entry["query"].lower().split()) == wanted:
                    self.counts["exact"] += 1
                    return entry["results"]
            words, best, best_score = _words(text), None, 0.0
            for entry in self._saved:
                theirs = _words(entry["query"])
                if words and theirs:
                    score = len(words & theirs) / min(len(words), len(theirs))
                    if score > best_score:
                        best, best_score = entry, score
            if best is not None and best_score >= MIN_OVERLAP:
                self.counts["nearest"] += 1
                return best["results"]
            self.counts["missed"] += 1
            return []
