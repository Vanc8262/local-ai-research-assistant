"""Runs the whole command line against the stand-in model and checks the files."""
import csv
import json

import pytest

import bench.__main__ as cli
import fake_model
from bench.client import ChatClient
from bench.results import read_manual_scores


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "bench.toml").write_text(
        '[defaults]\nspeed_repeats = 2\n\n[[models]]\nid = "fake-model"\nlabel = "Fake"\nvision = true\n'
        'system_suffix = "/no_think"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BENCH_BASE_URL", raising=False)
    monkeypatch.setattr(cli, "ChatClient",
                        lambda *args, **kwargs: ChatClient(*args, **kwargs, transport=fake_model.transport()))
    return tmp_path


def test_full_run_scores_a_perfect_model_as_perfect(project, capsys):
    assert cli.main(["run", "--model", "fake-model", "--tag", "ctx8k"]) == 0
    run_dir = next(p for p in (project / "results").iterdir() if p.is_dir())
    assert run_dir.name.endswith("_fake-model_ctx8k")

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert list(summary) == ["tools", "code", "speed", "research", "vision", "headroom"]
    assert (summary["tools"]["passed"], summary["tools"]["total"], summary["tools"]["score"]) == (30, 30, 1.0)
    assert (summary["code"]["passed"], summary["code"]["total"]) == (10, 10)
    assert summary["code"]["metrics"]["passed_first_try"] == 10
    assert (summary["research"]["passed"], summary["research"]["score"]) == (5, 1.0)
    assert (summary["vision"]["passed"], summary["vision"]["total"]) == (5, 5)
    assert summary["speed"]["score"] is None and summary["speed"]["passed"] == 3
    assert summary["tools"]["metrics"]["format_valid_rate"] == 1.0
    assert summary["tools"]["metrics"]["reasoning_observed"] is True
    assert summary["tools"]["metrics"]["median_gen_tps"] > 0
    assert "min_ram_available_mb" in summary["tools"]["metrics"]
    comparison = summary["speed"]["metrics"]["reasoning_comparison"]
    assert [row["reasoning"] for row in comparison] == ["on", "off"] and all(row["correct"] for row in comparison)
    assert summary["headroom"]["metrics"]["ram_total_mb"] > 0

    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["model_label"] == "Fake" and meta["context_length"] == 8192
    assert meta["server_model_info"]["quantization"] == "Q4_K_M"

    tool_lines = (run_dir / "tools.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(tool_lines) == 30 and json.loads(tool_lines[0])["timing"][0]["completion_tokens"] == 40
    speed_item = json.loads((run_dir / "speed.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(speed_item["runs"]) == 2
    assert len(list((run_dir / "vision_images").glob("*.png"))) == 5

    review = (run_dir / "manual_review.md").read_text(encoding="utf-8")
    assert review.count("## research /") == 5 and review.count("## vision /") == 5
    with (run_dir / "manual_scores.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 10 and rows[0]["score_1_to_5"] == ""

    table = (project / "results" / "summary.md").read_text(encoding="utf-8")
    assert "| tools | 100.0 | 30 | 30 |" in table and "Fake" in table
    assert "Manual scoring" in capsys.readouterr().out


def test_manual_scores_reach_the_report(project):
    assert cli.main(["run", "--model", "fake-model", "--suites", "research", "--limit", "2"]) == 0
    run_dir = next(p for p in (project / "results").iterdir() if p.is_dir())
    assert len((run_dir / "research.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    (run_dir / "manual_scores.csv").write_text(
        "suite,item,score_1_to_5,notes\nresearch,bus-lanes,4,ok\nresearch,sodium-cells,5,\n"
        "research,x,,not scored\nresearch,y,9,out of range\n", encoding="utf-8")
    assert read_manual_scores(run_dir) == {"research": 4.5}
    assert cli.main(["report"]) == 0
    with (project / "results" / "summary.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["suite"] == "research" and row["manual_avg_1_to_5"] == "4.5"


def test_system_suffix_is_appended(project, monkeypatch):
    seen = []
    real = fake_model.handler

    def spy(request):
        if request.url.path.endswith("/chat/completions"):
            seen.append(json.loads(request.content)["messages"][0])
        return real(request)
    monkeypatch.setattr(fake_model, "handler", spy)
    monkeypatch.setattr(cli, "ChatClient", lambda *a, **k: ChatClient(*a, **k, transport=__import__("httpx").MockTransport(spy)))
    assert cli.main(["run", "--model", "fake-model", "--suites", "tools", "--limit", "1"]) == 0
    system = next(m for m in seen if m["role"] == "system")
    assert system["content"].endswith("\n/no_think")


def test_unreachable_server_stops_before_writing_results(project, monkeypatch, capsys):
    import httpx

    def refuse(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(cli, "ChatClient", lambda *a, **k: ChatClient(*a, **k, transport=httpx.MockTransport(refuse)))
    assert cli.main(["run", "--model", "fake-model"]) == 1
    assert not (project / "results").exists()
    assert "did not answer" in capsys.readouterr().out


def test_unknown_suite_and_models_command(project, capsys):
    assert cli.main(["run", "--model", "fake-model", "--suites", "tools,nope"]) == 2
    assert cli.main(["models"]) == 0
    out = capsys.readouterr().out
    assert "Unknown suite(s): nope" in out and "fake-model  state=loaded" in out


def test_headroom_command(project):
    assert cli.main(["headroom", "--model", "fake-model"]) == 0
    run_dir = next(p for p in (project / "results").iterdir() if p.is_dir())
    assert list(json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))) == ["headroom"]


def test_suffix_without_system_message_and_truncation_count(tmp_path):
    from bench.client import ChatResult
    from bench.config import Config, ModelCfg
    from bench.report import _with_truncation
    from bench.suites.base import Ctx, speed_metrics

    seen = []

    def chat_fn(messages, tools=None, max_tokens=None):
        seen.append(messages)
        return ChatResult(content="", finish_reason="length", completion_tokens=5, ttft_s=0.1, total_s=1.1)

    ctx = Ctx(cfg=Config(), model=ModelCfg(id="m", system_suffix="/no_think"), run_dir=tmp_path, chat_fn=chat_fn)
    original = [{"role": "user", "content": [{"type": "text", "text": "look"}]}]
    ctx.chat(original)
    assert seen[0][0] == {"role": "system", "content": "/no_think"} and seen[0][1] is original[0]
    assert len(original) == 1                      # the caller's list is left alone
    metrics = speed_metrics(ctx.suite_calls)
    assert metrics["truncated_replies"] == 1 and metrics["median_gen_tps"] == 5.0
    assert _with_truncation("note", metrics) == "note; 1 replies cut off by max_tokens"
    assert _with_truncation("", metrics) == "1 replies cut off by max_tokens"
    assert _with_truncation("note", {"truncated_replies": 0}) == "note"
    assert _with_truncation("note", metrics, "speed") == "note"


def test_context_option_is_recorded_when_the_server_does_not_report_it(project, monkeypatch):
    import httpx

    def handler(request):
        if request.url.path == "/api/v0/models":
            return httpx.Response(404)
        return fake_model.handler(request)
    monkeypatch.setattr(cli, "ChatClient", lambda *a, **k: ChatClient(*a, **k, transport=httpx.MockTransport(handler)))
    assert cli.main(["run", "--model", "fake-model", "--suites", "headroom", "--context", "16384"]) == 0
    run_dir = next(p for p in (project / "results").iterdir() if p.is_dir())
    assert json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))["context_length"] == 16384


def test_repo_bench_toml_loads_with_per_model_sampling():
    from pathlib import Path

    from bench.config import load_config
    cfg = load_config(Path(__file__).resolve().parent.parent / "bench.toml")
    assert cfg.base_url.startswith("http://127.0.0.1")
    qwen, gemma = cfg.model("qwen3.5-9b"), cfg.model("gemma-4-12b-qat")
    assert qwen.temperature == 0.6 and qwen.extra_body["top_k"] == 20
    assert gemma.temperature == 1.0 and gemma.extra_body["top_k"] == 64
