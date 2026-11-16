"""The research bench spec (evals/bench_v1.toml): what is frozen, and how to check it.

The spec pins the questions, the referee model (by GGUF hash), the candidates, the fit limits and
the pass line, so a score can be traced to one bench version. A frozen spec must carry the
referee's sha256 and its prompt hash, otherwise "frozen" would not mean the same referee next time.

Hashes are compared in lower case without surrounding spaces: PowerShell's Get-FileHash prints
upper case. The pin hashes the file the shell's environment variable names; check_served_model
also asks llama-swap which file it actually serves (GET <root>/upstream/<model>/props).
"""
from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields
from pathlib import Path, PureWindowsPath

import httpx

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
    prompt_hash: str = ""              # referee.PROMPT_HASH; may stay empty while frozen = false
    max_referee_failed: float = 0.02   # share of a card's statements the referee could not judge


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
    """Make a dataclass from a table; every field without a default is required, unknown keys are ignored."""
    values = {}
    for field in fields(cls):
        if field.name in table:
            values[field.name] = table[field.name]
        elif field.default is MISSING:
            raise BenchSpecError(f"bench spec: missing key {field.name!r} in {where}")
    return cls(**values)


def normal_hash(value) -> str:
    """A hex digest as compared everywhere: no surrounding spaces, lower case."""
    return str(value or "").strip().lower()


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
    spec.referee.sha256 = normal_hash(spec.referee.sha256)
    spec.referee.prompt_hash = normal_hash(spec.referee.prompt_hash)
    if spec.frozen and not spec.referee.sha256:
        raise BenchSpecError("bench spec: frozen = true needs the referee's sha256 in [referee]")
    if spec.frozen and not spec.referee.prompt_hash:
        raise BenchSpecError("bench spec: frozen = true needs the referee's prompt_hash in [referee] "
                             "(assistant.referee.PROMPT_HASH)")
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
    pinned = normal_hash(spec.referee.sha256)
    if pinned and normal_hash(hasher(gguf)) != pinned:
        raise BenchSpecError(f"referee GGUF: hash of {gguf} differs from the pinned sha256")


def served_model_path(base_url: str, model: str, get=httpx.get) -> str:
    """The model file llama-swap serves for `model`: model_path from llama-server's /props, reached
    through llama-swap at <root>/upstream/<model>/props (root is base_url without /v1). The request
    starts the model if it isn't running. BenchSpecError when it can't be read."""
    url = f"{base_url.removesuffix('/').removesuffix('/v1')}/upstream/{model}/props"
    try:
        response = get(url, timeout=300)   # llama-swap's healthCheckTimeout: a cold load can take minutes
        if response.status_code != 200:
            raise BenchSpecError(f"could not read the served model file from {url}: HTTP {response.status_code}")
        data = response.json()
    except BenchSpecError:
        raise
    except Exception as exc:   # noqa: BLE001 - unreachable, timed out, or not JSON: all "can't tell"
        raise BenchSpecError(f"could not read the served model file from {url}: {type(exc).__name__}: {exc}") from None
    path = data.get("model_path") if isinstance(data, dict) else None
    if not path:
        raise BenchSpecError(f"could not read the served model file from {url}: no model_path in the reply")
    return str(path)


def same_model_file(served: str, expected: str) -> bool:
    """Whether llama-swap's model_path is the file `expected` names: the same file after resolving
    links and case (Windows), or else, both being files here, the same name (any case) and size.
    A served path that is no file on this PC is never the same."""
    try:
        if not (Path(served).is_file() and Path(expected).is_file()):
            return False
        norm = lambda p: os.path.normcase(os.path.realpath(p))
        if norm(served) == norm(expected) or os.path.samefile(served, expected):
            return True
        return (PureWindowsPath(served).name.lower() == PureWindowsPath(expected).name.lower()
                and os.path.getsize(served) == os.path.getsize(expected))
    except OSError:
        return False


def check_served_model(tier: Tier, expected: str, what: str, *, strict: bool, fetch=None) -> str | None:
    """Check that llama-swap serves `expected` for the tier. strict (the referee, and the candidate
    on the test split): raise BenchSpecError when the file differs or can't be read, with no way
    around it. Otherwise return a warning to print (None when it matches). fetch(base_url, model)
    returns the served path (default served_model_path)."""
    fetch = fetch or served_model_path
    try:
        served = fetch(tier.base_url, tier.model)
    except BenchSpecError as exc:
        if strict:
            raise BenchSpecError(f"{what}: {exc}; the bench checks the file llama-swap serves, so it needs "
                                 "llama-swap running (model_server = \"llama-swap\")") from None
        return f"Warning: {what}: {exc}; the served file is not checked"
    if same_model_file(served, expected):
        return None
    message = f"{what}: llama-swap serves {served}, not {expected or '(no file named)'}"
    if strict:
        raise BenchSpecError(message + "; restart llama-swap with the right file")
    return f"Warning: {message}"


def question_file_problems(bench: Mapping, root: Path) -> list[str]:
    """Each question file the eval ran with (manifest bench.question_files, path -> sha256) that is
    gone or changed since. Relative paths are taken from the repo root."""
    recorded = bench.get("question_files")
    if not isinstance(recorded, Mapping) or not recorded:
        return ["no question_files in its manifest's bench block, so its questions can't be checked"]
    problems = []
    for name, sha in recorded.items():
        path = Path(name) if Path(name).is_absolute() else Path(root) / name
        if not path.is_file():
            problems.append(f"question file {name} is gone")
        elif normal_hash(sha256_file(path)) != normal_hash(sha):
            problems.append(f"question file {name} changed after the run (sha256 differs from the manifest)")
    return problems


def tier_settings_hash(tier: Tier) -> str:
    settings = {"model": tier.model, "temperature": tier.temperature,
                "max_tokens": tier.max_tokens, "extra_body": tier.extra_body}
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def candidate_key(gguf_sha256: str, git_commit: str, tier: Tier) -> str:
    """GGUF + pipeline commit + tier settings: the identity the test-split retest cap counts."""
    return f"{gguf_sha256[:12]}-{git_commit[:9]}-{tier_settings_hash(tier)}"
