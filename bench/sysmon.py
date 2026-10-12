"""Memory and GPU snapshots. GPU numbers come from nvidia-smi; if it is missing
the GPU fields are simply absent."""
from __future__ import annotations

import shutil
import subprocess
import threading

import psutil

MB = 1024 * 1024
GPU_FIELDS = ("name", "memory.total", "memory.used", "memory.free", "utilization.gpu", "temperature.gpu")


def gpu_snapshot() -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip().splitlines()[0]
        name, total, used, free, util, temp = [part.strip() for part in out.split(",")]
        return {"gpu": name, "vram_total_mb": int(total), "vram_used_mb": int(used),
                "vram_free_mb": int(free), "gpu_util_pct": int(util), "gpu_temp_c": int(temp)}
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        return None


def ram_snapshot() -> dict:
    vm, swap = psutil.virtual_memory(), psutil.swap_memory()
    return {"ram_total_mb": vm.total // MB, "ram_used_mb": (vm.total - vm.available) // MB,
            "ram_available_mb": vm.available // MB, "swap_used_mb": swap.used // MB}


def snapshot() -> dict:
    data = ram_snapshot()
    data.update(gpu_snapshot() or {})
    return data


def top_processes(count: int = 8) -> list[dict]:
    rows = []
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            rows.append({"name": proc.info["name"], "rss_mb": proc.info["memory_info"].rss // MB})
        except (psutil.Error, AttributeError, TypeError):
            continue
    return sorted(rows, key=lambda row: row["rss_mb"], reverse=True)[:count]


class PeakSampler:
    """Samples memory in the background and keeps the worst values seen."""

    def __init__(self, interval_s: float = 1.0) -> None:
        self.interval_s = interval_s
        self.peak: dict = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> None:
        now = snapshot()
        for key in ("vram_used_mb", "ram_used_mb", "swap_used_mb", "gpu_temp_c"):
            if key in now:
                self.peak[f"peak_{key}"] = max(self.peak.get(f"peak_{key}", 0), now[key])
        if "ram_available_mb" in now:
            self.peak["min_ram_available_mb"] = min(
                self.peak.get("min_ram_available_mb", now["ram_available_mb"]), now["ram_available_mb"])

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample()

    def __enter__(self) -> "PeakSampler":
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._sample()
