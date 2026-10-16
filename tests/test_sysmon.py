"""Memory readings must not crash when Windows performance counters are disabled."""
import json

import psutil
import pytest

import bench.__main__ as cli
import fake_model
from bench import sysmon
from bench.client import ChatClient


def broken_swap():
    raise RuntimeError("PdhAddEnglishCounterW failed. Performance counters may be disabled.")


@pytest.fixture
def no_swap_counter(monkeypatch):
    monkeypatch.setattr(psutil, "swap_memory", broken_swap)


def test_snapshot_skips_swap_when_the_counter_fails(no_swap_counter):
    snap = sysmon.snapshot()
    assert "swap_used_mb" not in snap
    assert snap["ram_total_mb"] > 0 and "ram_available_mb" in snap


def test_peak_sampler_works_without_swap(no_swap_counter):
    with sysmon.PeakSampler(interval_s=0.05) as sampler:
        pass
    assert "peak_ram_used_mb" in sampler.peak and "peak_swap_used_mb" not in sampler.peak


def test_run_and_headroom_work_without_swap(no_swap_counter, tmp_path, monkeypatch, capsys):
    (tmp_path / "bench.toml").write_text(
        '[[models]]\nid = "fake-model"\nlabel = "Fake"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BENCH_BASE_URL", raising=False)
    monkeypatch.setattr(cli, "ChatClient",
                        lambda *args, **kwargs: ChatClient(*args, **kwargs, transport=fake_model.transport()))
    assert cli.main(["run", "--model", "fake-model", "--suites", "tools,headroom", "--limit", "1"]) == 0
    assert "swap use not measured" in capsys.readouterr().out
    run_dir = next(p for p in (tmp_path / "results").iterdir() if p.is_dir())
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "swap" not in " ".join(summary["headroom"]["metrics"]["warnings"])
