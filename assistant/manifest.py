"""What an eval ran with, saved as manifest.json in its folder so a result can be traced back.

Records the git commit (and whether the tree had uncommitted changes), Python and OS, the
tiers (no keys: only the name of the environment variable that holds one), the research and
search settings, the search cache version, the question file's hash, and the GGUF files that
llama-swap serves (GEMMA_GGUF, QWEN_GGUF, NOMIC_GGUF; see services/llama-swap/config.yaml)
with their size and sha256. runs/ is git-ignored, so copy the manifest into results/ with the
write-up.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .sources import CACHE_VERSION

GGUF_ENVS = ("GEMMA_GGUF", "QWEN_GGUF", "NOMIC_GGUF")
CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parent.parent).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def gguf_files(env=os.environ, hash_files: bool = True) -> dict:
    files = {}
    for name in GGUF_ENVS:
        path = env.get(name)
        if not path:
            continue
        entry = {"path": path}
        if Path(path).is_file():
            entry["bytes"] = Path(path).stat().st_size
            if hash_files:
                entry["sha256"] = sha256_file(Path(path))
        else:
            entry["missing"] = True
        files[name] = entry
    return files


def build_manifest(settings: Settings, *, modes: list[str], runs: int, question_ids: list[str],
                   questions_path: str | Path | None = None, env=os.environ, hash_files: bool = True) -> dict:
    tiers = {}
    for name, tier in settings.tiers.items():
        tiers[name] = {key: value for key, value in asdict(tier).items() if key != "extra_body"}
        tiers[name]["extra_body"] = tier.extra_body
    manifest = {
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "model_server": settings.model_server,
        "default_tier": settings.default_tier,
        "tiers": tiers,
        "research": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(settings.research).items()},
        "search": asdict(settings.search),
        "search_cache_version": CACHE_VERSION,
        "modes": modes,
        "runs": runs,
        "questions": question_ids,
        "gguf": gguf_files(env, hash_files),
    }
    if questions_path and Path(questions_path).is_file():
        manifest["questions_file"] = {"path": str(questions_path), "sha256": sha256_file(Path(questions_path))}
    return manifest


def write_manifest(out_dir: Path, manifest: dict) -> Path:
    path = Path(out_dir) / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
