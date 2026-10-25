"""Step-level run log: one JSON line per model call or tool call.

Every step records which tier handled it, token counts and timing, so that a
local-only run and a hybrid run of the same task can be compared line by line.
The log stays on this PC (runs/ is git-ignored).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


class RunLog:
    def __init__(self, runs_dir: Path, run_id: str | None = None, task: str = "") -> None:
        self.run_id = run_id or new_run_id()
        self.path = Path(runs_dir) / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.step_count = 0
        if task:
            self._write({"event": "run_started", "task": task})

    def step(self, kind: str, **fields) -> dict:
        """Append one step. `kind` is "model" or a tool name such as "search"."""
        self.step_count += 1
        record = {"event": "step", "step": self.step_count, "kind": kind, **fields}
        self._write(record)
        return record

    def _write(self, record: dict) -> None:
        record = {"run_id": self.run_id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  **record}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_run(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
