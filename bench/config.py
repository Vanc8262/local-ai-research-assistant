"""Load bench.toml into plain dataclasses."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelCfg:
    id: str
    label: str = ""
    vision: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    system_suffix: str = ""
    extra_body: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.id


@dataclass
class Config:
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    timeout_s: float = 900.0
    temperature: float = 0.0
    max_tokens: int = 3000
    seed: int | None = 42
    speed_repeats: int = 3
    code_attempts: int = 2
    context_length: int | None = 8192
    results_dir: Path = Path("results")
    models: dict[str, ModelCfg] = field(default_factory=dict)

    def model(self, model_id: str) -> ModelCfg:
        """Settings for a model id; ids missing from the file get the defaults."""
        return self.models.get(model_id) or ModelCfg(id=model_id)


def load_config(path: str | Path = "bench.toml") -> Config:
    cfg = Config()
    path = Path(path)
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        server = raw.get("server", {})
        defaults = raw.get("defaults", {})
        for key in ("base_url", "api_key", "timeout_s"):
            if key in server:
                setattr(cfg, key, server[key])
        for key in ("temperature", "max_tokens", "seed", "speed_repeats",
                    "code_attempts", "context_length"):
            if key in defaults:
                setattr(cfg, key, defaults[key])
        if "results_dir" in raw:
            cfg.results_dir = Path(raw["results_dir"])
        for entry in raw.get("models", []):
            model = ModelCfg(**entry)
            cfg.models[model.id] = model
    cfg.base_url = os.environ.get("BENCH_BASE_URL", cfg.base_url).rstrip("/")
    cfg.api_key = os.environ.get("BENCH_API_KEY", cfg.api_key)
    return cfg
