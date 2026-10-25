import json
from pathlib import Path

import httpx
import pytest

import fake_model
from assistant.__main__ import main
from assistant.config import Settings, Tier, load_settings
from assistant.gateway import Gateway, PrivacyError, TierNotReady
from assistant.runlog import RunLog, read_run
from assistant.search import SearchError, search

ROOT = Path(__file__).resolve().parent.parent


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path, default_tier="local")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://local/v1", model="gemma")
    settings.tiers["gemini"] = Tier(name="gemini", kind="cloud", base_url="http://cloud/v1",
                                    model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY")
    return settings


def recording_transport(seen: list):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=fake_model.sse({"content": "ready"}))
    return httpx.MockTransport(handler)


def test_shipped_config_loads():
    settings = load_settings(ROOT / "assistant.toml")
    assert settings.default_tier == "local" and settings.backup_tier == "local-backup"
    assert settings.tier("local").model == "gemma-4-e4b-qat"
    assert settings.tier("gemini").kind == "cloud" and settings.tier("gemini").api_key_env == "GEMINI_API_KEY"
    assert all("127.0.0.1" in t.base_url for t in settings.tiers.values() if t.kind == "local")


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        Tier(name="x", kind="paid", base_url="http://x", model="m")


def test_same_prompt_on_two_tiers_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "secret-123")
    seen = []
    log = RunLog(tmp_path, task="compare")
    gateway = Gateway(settings_for(tmp_path), log, transport=recording_transport(seen))
    messages = [{"role": "user", "content": "Say ready"}]
    for tier in ("local", "gemini"):
        assert gateway.ask(tier, messages, purpose="compare", private=False).content == "ready"

    assert [r.url.host for r in seen] == ["local", "cloud"]
    assert seen[1].headers["authorization"] == "Bearer secret-123"
    steps = [r for r in read_run(log.path) if r["event"] == "step"]
    assert [(s["tier"], s["tier_kind"], s["outcome"]) for s in steps] == [
        ("local", "local", "ok"), ("gemini", "cloud", "ok")]
    for step in steps:
        assert step["completion_tokens"] == 40 and step["gen_tps"] is not None
        assert step["overall_tps"] > 0
        assert step["answer"] == "ready" and step["prompt"] == messages
    assert "secret-123" not in log.path.read_text(encoding="utf-8")


def test_private_request_never_reaches_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "secret-123")
    seen = []
    log = RunLog(tmp_path)
    gateway = Gateway(settings_for(tmp_path), log, transport=recording_transport(seen))
    with pytest.raises(PrivacyError):
        gateway.ask("gemini", [{"role": "user", "content": "def private(): ..."}])   # private by default
    assert seen == []
    (step,) = read_run(log.path)
    assert step["outcome"] == "blocked" and "prompt" not in step


def test_missing_key_is_reported_not_sent(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_GEMINI_KEY", raising=False)
    seen = []
    gateway = Gateway(settings_for(tmp_path), RunLog(tmp_path), transport=recording_transport(seen))
    with pytest.raises(TierNotReady):
        gateway.ask("gemini", [{"role": "user", "content": "hi"}], private=False)
    assert seen == []


def test_search_parses_and_logs(tmp_path):
    payload = {"results": [{"title": "RTX 3060", "url": "https://example.org/a", "content": "12 GB",
                            "engine": "duckduckgo"}] * 3}
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=payload)

    log = RunLog(tmp_path)
    hits = search("http://searx", "rtx 3060", log, limit=2, transport=httpx.MockTransport(handler))
    assert seen["params"] == {"q": "rtx 3060", "format": "json"}
    assert [h.url for h in hits] == ["https://example.org/a"] * 2
    (step,) = read_run(log.path)
    assert step["kind"] == "search" and step["hits"] == 2 and step["outcome"] == "ok"


def test_search_error_when_json_disabled(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="Forbidden"))
    with pytest.raises(SearchError):
        search("http://searx", "q", RunLog(tmp_path), transport=transport)


def test_cli_ask_without_public_skips_cloud(tmp_path, monkeypatch, capsys):
    config = tmp_path / "assistant.toml"
    config.write_text(f'''
[assistant]
default_tier = "gemini"
runs_dir = "{(tmp_path / 'runs').as_posix()}"
[tiers.gemini]
kind = "cloud"
base_url = "http://cloud/v1"
model = "g"
api_key_env = "TEST_GEMINI_KEY"
''', encoding="utf-8")
    monkeypatch.setenv("TEST_GEMINI_KEY", "k")
    assert main(["--config", str(config), "ask", "hello"]) == 1
    out = capsys.readouterr().out
    assert "blocked: tier 'gemini' leaves this PC" in out and "skipped" not in out
    (run,) = (tmp_path / "runs").glob("*.jsonl")
    assert [r.get("outcome") for r in read_run(run)] == [None, "blocked"]
