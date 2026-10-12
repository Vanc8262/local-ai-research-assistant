"""Headroom check: how much RAM and VRAM is left with the model loaded.

Run it with everything the assistant will need running at the same time
(LM Studio with the model loaded, Docker Desktop, SearXNG, a browser). The
answer decides whether 16 GB of RAM is enough.
"""
from __future__ import annotations

import shutil
import subprocess

from .. import sysmon
from .base import Ctx, SuiteResult

LOW_RAM_MB = 2000
LOW_VRAM_MB = 500


def docker_containers() -> list[str] | None:
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True,
                             text=True, timeout=15, check=True).stdout
        return [name for name in out.splitlines() if name.strip()]
    except (subprocess.SubprocessError, OSError):
        return None


def run(ctx: Ctx) -> SuiteResult:
    # One small request makes sure the model is loaded before memory is read.
    warm = ctx.chat([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=600)
    ctx.take_calls()
    snap = sysmon.snapshot()
    containers = docker_containers()
    warnings = []
    if warm.error:
        warnings.append(f"model did not answer: {warm.error}")
    if snap["ram_available_mb"] < LOW_RAM_MB:
        warnings.append(f"only {snap['ram_available_mb']} MB of RAM available")
    if snap["swap_used_mb"] > 2000:
        warnings.append(f"{snap['swap_used_mb']} MB of swap in use; the system is paging")
    if "vram_free_mb" not in snap:
        warnings.append("nvidia-smi not found; VRAM not measured")
    elif snap["vram_free_mb"] < LOW_VRAM_MB:
        warnings.append(f"only {snap['vram_free_mb']} MB of VRAM free; a larger context may not fit")
    if containers is None:
        warnings.append("Docker not reachable; start Docker and SearXNG for a realistic reading")
    metrics = {**snap, "docker_containers": containers, "warnings": warnings,
               "top_processes": sysmon.top_processes()}
    for warning in warnings:
        print(f"  warning: {warning}")
    print(f"  RAM available {snap['ram_available_mb']} MB of {snap['ram_total_mb']} MB; "
          f"VRAM free {snap.get('vram_free_mb', 'n/a')} MB")
    item = {"id": "headroom", "passed": not warnings, "reason": "; ".join(warnings)}
    return SuiteResult("headroom", None, int(not warnings), 1, metrics, [item])
