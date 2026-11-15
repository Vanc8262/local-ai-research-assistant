"""The research bench spec (evals/bench_v1.toml): what is frozen, and how to check it.

The spec pins the questions, the referee model (by GGUF hash), the candidates, the fit limits and
the pass line, so a score can be traced to one bench version. A frozen spec must carry the
referee's sha256, otherwise "frozen" would not mean the same referee next time.
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import Tier
from .manifest import sha256_file

REFEREE_CAVEAT = "A referee pass means the referee accepted the report, not that every sentence is true."
SPLITS = ("dev", "test")


class BenchSpecError(ValueError):
    pass


@dataclass
class RefereePin:
    tier: str
    gguf_env: str
    sha256: str
    prompt_version: int
    min_supported_recall: float
    min_not_supported_recall: float


@dataclass
class CandidateRef:
    tier: str
    gguf_env: str


@dataclass
class FitLimits:
    min_free_ram_mb: int
    max_report_minutes: float
    max_error_share: float
    context: int


@dataclass
class PassLine:
    min_facts_right: float
    max_errors_through: float


@dataclass
class BenchSpec:
    version: int
    frozen: bool
    frozen_on: str
    questions: dict[str, list[str]]  # split -> question file paths
    referee: RefereePin
    candidates: dict[str, CandidateRef]
    fit: FitLimits
    pass_line: PassLine
    runs: int
    search: str
    tavily_fallback: bool


def _section(data: Mapping, name: str) -> Mapping:
    value = data.get(name)
    if not isinstance(value, Mapping):
        raise BenchSpecError(f"bench spec: missing section [{name}]")
    return value


def _build(cls, table: Mapping, where: str):
    """Make a dataclass from a table; every field is required, unknown keys are ignored."""
    try:
        return cls(**{name: table[name] for name in cls.__dataclass_fields__})
    except KeyError as exc:
        raise BenchSpecError(f"bench spec: missing key {exc.args[0]!r} in {where}") from None


def load_bench(path: str | Path) -> BenchSpec:
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    questions = _section(data, "questions")
    for split in questions:
        if split not in SPLITS:
            raise BenchSpecError(f"bench spec: unknown split {split!r} in [questions] (only {', '.join(SPLITS)})")
    candidates = _section(data, "candidates")
    top = {}
    for key in ("version", "frozen", "frozen_on", "runs", "search", "tavily_fallback"):
        if key not in data:
            raise BenchSpecError(f"bench spec: missing key {key!r}")
        top[key] = data[key]
    spec = BenchSpec(
        questions={split: list(paths) for split, paths in questions.items()},
        referee=_build(RefereePin, _section(data, "referee"), "[referee]"),
        candidates={name: _build(CandidateRef, table, f"[candidates.{name}]")
                    for name, table in candidates.items()},
        fit=_build(FitLimits, _section(data, "fit"), "[fit]"),
        pass_line=_build(PassLine, _section(data, "pass_line"), "[pass_line]"),
        **top,
    )
    if spec.frozen and not spec.referee.sha256:
        raise BenchSpecError("bench spec: frozen = true needs the referee's sha256 in [referee]")
    return spec


def check_referee_pin(spec: BenchSpec, env: Mapping[str, str], hasher=sha256_file) -> None:
    """Fail unless the referee GGUF is the pinned one (or, with no pin yet, at least exists)."""
    var = spec.referee.gguf_env
    value = env.get(var, "")
    if not value:
        raise BenchSpecError(f"referee GGUF: environment variable {var} is not set")
    gguf = Path(value)
    if not gguf.is_file():
        raise BenchSpecError(f"referee GGUF: file from {var} does not exist: {gguf}")
    if spec.referee.sha256 and hasher(gguf) != spec.referee.sha256:
        raise BenchSpecError(f"referee GGUF: hash of {gguf} differs from the pinned sha256")


def tier_settings_hash(tier: Tier) -> str:
    settings = {"model": tier.model, "temperature": tier.temperature,
                "max_tokens": tier.max_tokens, "extra_body": tier.extra_body}
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def candidate_key(gguf_sha256: str, git_commit: str, tier: Tier) -> str:
    """GGUF + pipeline commit + tier settings: the identity the test-split retest cap counts."""
    return f"{gguf_sha256[:12]}-{git_commit[:9]}-{tier_settings_hash(tier)}"
