"""Fit checks of a bench run: GPU offload, free RAM, minutes per report, and runs without a report."""
import json
import sys

import httpx

from assistant.bench_spec import FitLimits
from assistant.evaluate import Question, run_eval
from assistant.fit import fetch_llama_swap_log, fit_failures, parse_offload

from test_evaluate import FAKE_LDR, settings_for, transport

LIMITS = FitLimits(min_free_ram_mb=2048, max_report_minutes=60, max_error_share=0.10, context=32768)


def ok(**over):
    row = {"outcome": "ok", "research_s": "600", "verify_s": "300", "repair_s": "100", "min_free_ram_mb": "4000"}
    return {**row, **over}


def test_parse_offload():
    log = ("load_tensors: offloaded 30/49 layers to GPU\n"
           "something else\n"
           "load_tensors: offloaded 49/49 layers to GPU\n")
    assert parse_offload(log) == (49, 49)
    assert parse_offload("no such line here") is None
    assert parse_offload("") is None


def test_fetch_llama_swap_log_reads_the_root_logs_endpoint_and_is_empty_on_error():
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        return httpx.Response(200, text="offloaded 3/3 layers to GPU", request=httpx.Request("GET", url))
    assert fetch_llama_swap_log("http://127.0.0.1:8080/v1", get=get) == "offloaded 3/3 layers to GPU"
    assert seen == ["http://127.0.0.1:8080/logs"]

    def missing(url, **kwargs):
        return httpx.Response(404, text="not found", request=httpx.Request("GET", url))

    def down(url, **kwargs):
        raise httpx.ConnectError("refused")
    assert fetch_llama_swap_log("http://127.0.0.1:8080/v1", get=missing) == ""
    assert fetch_llama_swap_log("http://127.0.0.1:8080/v1", get=down) == ""


def test_fit_passes_inside_every_limit():
    rows = [ok() for _ in range(10)]
    assert fit_failures(rows, LIMITS, (49, 49)) == []


def test_fit_ram():
    assert fit_failures([ok(min_free_ram_mb="2048")], LIMITS, (49, 49)) == []   # at the limit is fine
    assert fit_failures([ok(), ok(min_free_ram_mb="2047")], LIMITS, (49, 49)) == ["ram"]


def test_fit_time():
    assert fit_failures([ok(research_s="3000", verify_s="500", repair_s="100")], LIMITS, None) == []  # 60 min
    assert fit_failures([ok(research_s="3000", verify_s="500", repair_s="101")], LIMITS, None) == ["time"]
    # a run that ended without a report is counted under errors, not time
    slow_failure = ok(outcome="no_report", research_s="9000", verify_s="", repair_s="")
    assert "time" not in fit_failures([slow_failure] + [ok() for _ in range(9)], LIMITS, None)


def test_fit_errors():
    ten_percent = [ok(outcome="not_run")] + [ok() for _ in range(9)]
    assert fit_failures(ten_percent, LIMITS, None) == []
    over = [ok(outcome="not_run"), ok(outcome="repair_failed")] + [ok() for _ in range(8)]
    assert fit_failures(over, LIMITS, None) == ["errors"]


def test_fit_gpu():
    assert fit_failures([ok()], LIMITS, (48, 49)) == ["gpu"]
    assert fit_failures([ok()], LIMITS, (49, 49)) == []


def test_gpu_unknown_is_not_a_failure():
    assert fit_failures([ok()], LIMITS, None) == []


def test_run_eval_saves_the_offload_probe_once_after_the_first_question(tmp_path):
    script = tmp_path / "fake_ldr.py"
    script.write_text(FAKE_LDR, encoding="utf-8")
    calls = []

    def probe():
        calls.append(1)
        return (49, 49)
    bench = {"version": 1, "split": "dev", "candidate": "gemma-e4b"}
    questions = [Question(id="a", text="q"), Question(id="b", text="q")]
    out_dir, _ = run_eval(settings_for(tmp_path), questions, ["local"], runner=[sys.executable, str(script)],
                          transport=transport([]), runs=1, candidate_tier="local", bench=bench,
                          offload_probe=probe)
    assert calls == [1]
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bench"]["offload"] == [49, 49]


def test_run_eval_records_an_unknown_offload_and_survives_a_failing_probe(tmp_path):
    script = tmp_path / "fake_ldr.py"
    script.write_text(FAKE_LDR, encoding="utf-8")

    def broken():
        raise RuntimeError("no log")
    out_dir, rows = run_eval(settings_for(tmp_path), [Question(id="a", text="q")], ["local"],
                             runner=[sys.executable, str(script)], transport=transport([]), runs=1,
                             candidate_tier="local", bench={"version": 1}, offload_probe=broken)
    assert rows[0]["outcome"] == "ok"
    assert json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))["bench"]["offload"] is None
