"""Memory and GPU snapshots. GPU numbers come from nvidia-smi; if it is missing
the GPU fields are simply absent."""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import threading
import time

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


def swap_used_mb() -> int | None:
    """Swap (page file) in use, or None when the OS will not say.

    On Windows psutil reads this from a performance counter, and that call fails with
    "PdhAddEnglishCounterW failed" when performance counters are disabled or corrupt.
    """
    try:
        return psutil.swap_memory().used // MB
    except (RuntimeError, OSError):
        return None


def ram_snapshot() -> dict:
    vm = psutil.virtual_memory()
    data = {"ram_total_mb": vm.total // MB, "ram_used_mb": (vm.total - vm.available) // MB,
            "ram_available_mb": vm.available // MB}
    swap = swap_used_mb()
    if swap is not None:
        data["swap_used_mb"] = swap
    return data


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


class Timeline:
    """Writes a memory sample every `interval_s` to a CSV file, labelled with what was running.

    Set `label` as the work moves on; `low` is the lowest free RAM seen since the last
    `reset_low()`, so a caller can record the worst moment of each step."""

    FIELDS = ("at_s", "label", "ram_available_mb", "ram_used_mb", "swap_used_mb", "llama_server_mb", "top")

    def __init__(self, path, interval_s: float = 15.0, top: int = 4) -> None:
        self.path, self.interval_s, self.top = path, interval_s, top
        self.label, self.low, self.lowest = "", None, None   # lowest: (mb, label) over the whole run
        self._start = time.perf_counter()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def reset_low(self) -> None:
        """Start a new step: sample now, so even a step shorter than the interval has a low."""
        self.low = None
        self.sample()

    def sample(self) -> dict:
        ram = ram_snapshot()
        procs = top_processes(self.top + 4)
        row = {"at_s": round(time.perf_counter() - self._start), "label": self.label, **ram,
               "llama_server_mb": sum(p["rss_mb"] for p in procs if "llama" in (p["name"] or "").lower()),
               "top": "; ".join(f"{p['name']} {p['rss_mb']}" for p in procs[:self.top])}
        free = ram["ram_available_mb"]
        self.low = free if self.low is None else min(self.low, free)
        if self.lowest is None or free < self.lowest[0]:
            self.lowest = (free, self.label)
        new = not os.path.exists(self.path)
        with open(self.path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDS, extrasaction="ignore")
            if new:
                writer.writeheader()
            writer.writerow(row)
        return row

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.sample()

    def __enter__(self) -> "Timeline":
        self.sample()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.sample()


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
