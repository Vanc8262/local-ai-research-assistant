"""Shared pieces for the suites: the run context and the result record."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..client import ChatResult
from ..config import Config, ModelCfg

# A chat function takes (messages, tools, max_tokens) and returns a ChatResult.
ChatFn = Callable[..., ChatResult]


@dataclass
class SuiteResult:
    name: str
    score: float | None          # 0..1, or None when the suite has no pass/fail score
    passed: int = 0
    total: int = 0
    metrics: dict = field(default_factory=dict)
    items: list[dict] = field(default_factory=list)
    skipped: str | None = None   # reason, when the suite did not run


@dataclass
class Ctx:
    """What a suite needs to run: a way to call the model and somewhere to put files."""
    cfg: Config
    model: ModelCfg
    run_dir: Path
    chat_fn: ChatFn
    client: object | None = None           # the ChatClient, for suites that need extras
    limit: int | None = None               # run only the first N items of each suite
    calls: list[ChatResult] = field(default_factory=list)       # since the last take_calls()
    suite_calls: list[ChatResult] = field(default_factory=list) # since the suite started

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int | None = None) -> ChatResult:
        suffix = self.model.system_suffix
        if suffix and any(m["role"] == "system" for m in messages):
            messages = [{**m, "content": f"{m['content']}\n{suffix}"} if m["role"] == "system" else m
                        for m in messages]
        elif suffix:
            messages = [{"role": "system", "content": suffix}, *messages]
        result = self.chat_fn(messages, tools=tools, max_tokens=max_tokens)
        self.calls.append(result)
        self.suite_calls.append(result)
        return result

    def take_calls(self) -> list[ChatResult]:
        calls, self.calls = self.calls, []
        return calls


def median(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(statistics.median(clean), 3) if clean else None


def speed_metrics(calls: list[ChatResult]) -> dict:
    """Speed summary over every model call a suite made."""
    ok = [c for c in calls if not c.error]
    return {
        "calls": len(calls),
        "call_errors": len(calls) - len(ok),
        "median_ttft_s": median([c.ttft_s for c in ok]),
        "median_ttfa_s": median([c.ttfa_s for c in ok]),
        "median_gen_tps": median([c.gen_tps for c in ok]),
        "median_total_s": median([c.total_s for c in ok]),
        "completion_tokens": sum(c.completion_tokens or 0 for c in ok),
        "tokens_estimated": any(c.tokens_estimated for c in ok),
        "reasoning_observed": any(c.reasoning for c in ok),
        "truncated_replies": sum(c.finish_reason == "length" for c in ok),
    }
