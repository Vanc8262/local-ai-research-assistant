import json
import threading
from contextlib import contextmanager

import httpx
import pytest

import fake_model
from assistant.config import Settings, Tier
from assistant.gateway import Gateway
from assistant.proxy import Proxy, make_server
from assistant.runlog import RunLog, read_run
from bench.client import ChatClient


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path, default_tier="local")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://local/v1", model="gemma")
    settings.tiers["gemini"] = Tier(name="gemini", kind="cloud", base_url="http://cloud/v1",
                                    model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY")
    return settings


def upstream(seen: list, cloud_status: int = 200):
    """Both tiers answer "ready" (or a tool call when tools are sent); the cloud can be made to fail."""
    def handler(request):
        seen.append(request)
        if request.url.host == "cloud" and cloud_status != 200:
            return httpx.Response(cloud_status, text="quota exceeded")
        body = json.loads(request.content)
        answer = ({"tool_calls": [("web_search", '{"q": "kv cache"}')]} if body.get("tools")
                  else {"content": "ready"})
        return httpx.Response(200, content=fake_model.sse(answer))
    return httpx.MockTransport(handler)


@contextmanager
def running(tmp_path, *, private=True, cloud_status=200):
    seen = []
    log = RunLog(tmp_path, task="proxy test")
    gateway = Gateway(settings_for(tmp_path), log, transport=upstream(seen, cloud_status))
    server = make_server(Proxy(settings_for(tmp_path), gateway, private=private), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}/v1", seen, log
    finally:
        server.shutdown()
        server.server_close()
        gateway.close()


def steps(log):
    return [r for r in read_run(log.path) if r["event"] == "step"]


def test_plain_request_goes_through_the_gateway(tmp_path):
    with running(tmp_path) as (url, seen, log):
        resp = httpx.post(f"{url}/chat/completions",
                          json={"model": "local", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "ready"
    assert body["usage"]["completion_tokens"] == 40
    assert [r.url.host for r in seen] == ["local"]
    (step,) = steps(log)
    assert step["tier"] == "local" and step["purpose"] == "proxy" and step["outcome"] == "ok"


def test_streamed_answer_reads_back_with_an_openai_client(tmp_path):
    with running(tmp_path) as (url, seen, log):
        client = ChatClient(url)
        result = client.chat("gemma", [{"role": "user", "content": "hi"}])   # streams; model id resolves to a tier
        client.close()
    assert result.error is None and result.content == "ready"
    assert result.completion_tokens == 40 and result.finish_reason == "stop"


def test_tool_calls_pass_through(tmp_path):
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
    with running(tmp_path) as (url, seen, log):
        resp = httpx.post(f"{url}/chat/completions", json={
            "model": "local", "messages": [{"role": "user", "content": "find"}], "tools": tools})
    assert json.loads(seen[0].content)["tools"] == tools
    message = resp.json()["choices"][0]["message"]
    assert message["tool_calls"][0]["function"] == {"name": "web_search", "arguments": '{"q": "kv cache"}'}
    assert resp.json()["choices"][0]["finish_reason"] == "tool_calls"


def test_private_session_refuses_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "k")
    with running(tmp_path) as (url, seen, log):
        resp = httpx.post(f"{url}/chat/completions",
                          json={"model": "gemini", "messages": [{"role": "user", "content": "secret code"}]})
    assert resp.status_code == 403 and resp.json()["error"]["type"] == "privacy_blocked"
    assert seen == []
    assert [s["outcome"] for s in steps(log)] == ["blocked"]


@pytest.mark.parametrize("key", ["k", None])
def test_public_cloud_failure_falls_back_to_local(tmp_path, monkeypatch, key):
    if key:
        monkeypatch.setenv("TEST_GEMINI_KEY", key)
    else:
        monkeypatch.delenv("TEST_GEMINI_KEY", raising=False)
    with running(tmp_path, private=False, cloud_status=429) as (url, seen, log):
        resp = httpx.post(f"{url}/chat/completions",
                          json={"model": "gemini", "messages": [{"role": "user", "content": "public q"}]})
    assert resp.status_code == 200 and resp.json()["model"] == "local"
    logged = [(s["tier"], s["outcome"], s["purpose"]) for s in steps(log)]
    first = "error" if key else "not_ready"
    assert logged == [("gemini", first, "proxy"), ("local", "ok", "proxy (fallback from gemini)")]


def test_unknown_model_and_model_list(tmp_path):
    with running(tmp_path) as (url, seen, log):
        missing = httpx.post(f"{url}/chat/completions",
                             json={"model": "gpt-9", "messages": [{"role": "user", "content": "hi"}]})
        models = httpx.get(f"{url}/models").json()
    assert missing.status_code == 404 and "local" in missing.json()["error"]["message"]
    assert [m["id"] for m in models["data"]] == ["local", "gemini"]
    assert seen == []
