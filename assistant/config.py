"""Load assistant.toml into plain dataclasses."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("local", "cloud")


@dataclass
class Tier:
    name: str
    kind: str                 # "local" (this PC) or "cloud" (leaves the PC)
    base_url: str
    model: str
    label: str = ""
    api_key_env: str = ""     # environment variable holding the key; empty for local servers
    temperature: float = 0.0
    max_tokens: int = 4000
    extra_body: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"tier {self.name!r}: kind must be one of {KINDS}, not {self.kind!r}")
        self.label = self.label or self.name
        self.base_url = self.base_url.rstrip("/")

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else "lm-studio"

    @property
    def ready(self) -> bool:
        """False when the tier needs a key that is not set."""
        return bool(self.api_key)


@dataclass
class Settings:
    default_tier: str = "local"
    backup_tier: str = ""
    runs_dir: Path = Path("runs")
    searxng_url: str = "http://127.0.0.1:8888"
    tiers: dict[str, Tier] = field(default_factory=dict)

    def tier(self, name: str) -> Tier:
        try:
            return self.tiers[name]
        except KeyError:
            raise KeyError(f"unknown tier {name!r}; known: {', '.join(self.tiers)}") from None


def load_settings(path: str | Path = "assistant.toml") -> Settings:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    main = raw.get("assistant", {})
    settings = Settings(
        default_tier=main.get("default_tier", "local"),
        backup_tier=main.get("backup_tier", ""),
        runs_dir=Path(main.get("runs_dir", "runs")),
        searxng_url=main.get("searxng_url", "http://127.0.0.1:8888").rstrip("/"),
    )
    for name, entry in raw.get("tiers", {}).items():
        settings.tiers[name] = Tier(name=name, **entry)
    for name in (settings.default_tier, settings.backup_tier):
        if name:
            settings.tier(name)
    return settings
