import json
import sys

import httpx
import pytest

import fake_model
from assistant.config import Settings, Tier
from assistant.gateway import PrivacyError, TierNotReady
from assistant.research import build_overrides, run_research
from assistant.runlog import RunLog, read_run

# Stands in for LDR: makes one model call through the proxy it was given, like LDR's
# openai_endpoint provider would, then writes a result in LDR's shape.
FAKE_LDR = r'''
import json, sys, urllib.request
job = json.load(sys.stdin)
o = job["overrides"]
body = json.dumps({"model": o["llm.model"], "messages": [{"role": "user", "content": job["query"]}]}).encode()
req = urllib.request.Request(o["llm.openai_endpoint.url"] + "/chat/completions", body,
                             {"Content-Type": "application/json"})
answer = json.load(urllib.request.urlopen(req))["choices"][0]["message"]["content"]
echo = o.get("search.engine.web.tavily.api_key", "")
result = {"summary": f"{answer} [1]", "sources": [
    {"index": "1", "title": "Python 3.15 docs", "link": "https://docs.python.org/3/whatsnew/3.15.html",
     "snippet": "lazy imports " + echo}]}
json.dump({"result": result, "error": None, "elapsed_s": 1.0}, open(job["out"], "w"))
'''


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path / "runs", default_tier="local")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://local/v1", model="gemma")
    settings.tiers["gemini"] = Tier(name="gemini", kind="cloud", base_url="http://cloud/v1",
                                    model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY")
    return settings


def fake_runner(tmp_path) -> list[str]:
    script = tmp_path / "fake_ldr.py"
    script.write_text(FAKE_LDR, encoding="utf-8")
    return [sys.executable, str(script)]


def upstream(seen: list):
    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(200, content=fake_model.sse({"content": "Lazy imports are new"}))
    return httpx.MockTransport(handler)


def test_research_runs_ldr_through_the_gateway(tmp_path):
    settings, seen = settings_for(tmp_path), []
    log = RunLog(settings.runs_dir, task="research")
    result = run_research(settings, "What is new in Python 3.15?", log,
                          runner=fake_runner(tmp_path), transport=upstream(seen))

    assert result.error is None and result.summary == "Lazy imports are new [1]"
    assert seen == ["local"]
    report = result.report_path.read_text(encoding="utf-8")
    assert report.startswith("# What is new in Python 3.15?") and "https://docs.python.org/3/whatsnew/3.15.html" in report
    assert json.loads((result.run_dir / "sources.json").read_text())[0]["index"] == "1"
    steps = [r for r in read_run(log.path) if r["event"] == "step"]
    assert [(s["kind"], s.get("tier"), s["outcome"]) for s in steps] == [
        ("model", "local", "ok"), ("research", "local", "ok")]
    assert steps[0]["purpose"] == "proxy" and steps[1]["n_sources"] == 1


def test_private_research_refuses_cloud_tier_and_tavily(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    settings = settings_for(tmp_path)
    for options in ({"tier": "gemini"}, {"search": "tavily"}):
        with pytest.raises(PrivacyError):
            run_research(settings, "q", RunLog(settings.runs_dir), runner=["never-run"], **options)


def test_tavily_needs_its_key_and_never_lands_in_saved_files(tmp_path, monkeypatch):
    settings, seen = settings_for(tmp_path), []
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(TierNotReady):
        run_research(settings, "q", RunLog(settings.runs_dir), search="tavily", public=True, runner=["never-run"])

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    log = RunLog(settings.runs_dir)
    result = run_research(settings, "q", log, search="tavily", public=True,
                          runner=fake_runner(tmp_path), transport=upstream(seen))
    assert result.error is None
    for path in [log.path, *result.run_dir.iterdir()]:
        assert "tvly-secret" not in path.read_text(encoding="utf-8")


def test_missing_ldr_python_is_reported(tmp_path):
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    result = run_research(settings, "q", log, runner=[str(tmp_path / "no-such-python")], transport=upstream([]))
    assert "not found" in result.error
    assert [r["outcome"] for r in read_run(log.path)] == ["error"]


def test_overrides_point_ldr_at_the_proxy_and_stay_local(tmp_path):
    overrides = build_overrides(settings_for(tmp_path), tier="local", search="searxng",
                                proxy_url="http://127.0.0.1:5555/v1", private=True)
    assert overrides["llm.provider"] == "openai_endpoint"
    assert overrides["llm.openai_endpoint.url"] == "http://127.0.0.1:5555/v1"
    assert overrides["llm.require_local_endpoint"] is True and overrides["web.host"] == "127.0.0.1"
    assert "search.engine.web.tavily.api_key" not in overrides
    assert "source" in overrides["general.output_instructions"]   # citation rules for the local writer


def test_show_prints_research_steps(tmp_path, capsys):
    from assistant.__main__ import cmd_show
    settings, log = settings_for(tmp_path), RunLog(tmp_path / "runs", task="r")
    log.step("research", tier="local", search="searxng", outcome="ok", n_sources=4, elapsed_s=89.4)

    class Args:
        run = str(log.path)
    cmd_show(settings, Args())
    out = capsys.readouterr().out
    assert "local + searxng -> 4 sources" in out and "total=89.4 s" in out


def test_the_tavily_fallback_key_goes_to_public_searxng_research_only(tmp_path, monkeypatch):
    script = tmp_path / "echo_ldr.py"
    script.write_text(FAKE_LDR.replace('echo = o.get("search.engine.web.tavily.api_key", "")',
                                       'echo = "key=" + job.get("pages", {}).get("tavily_key", "none")'), encoding="utf-8")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    settings = settings_for(tmp_path)

    private = run_research(settings, "q", RunLog(settings.runs_dir), runner=[sys.executable, str(script)],
                           transport=upstream([]))
    assert "key=none" in private.sources[0]["snippet"]              # private: Tavily is never offered

    log = RunLog(settings.runs_dir)
    public = run_research(settings, "q", log, public=True, runner=[sys.executable, str(script)],
                          transport=upstream([]))
    assert "key=***" in public.sources[0]["snippet"]                # passed, and scrubbed from what is saved
    for path in [log.path, *public.run_dir.iterdir()]:
        assert "tvly-secret" not in path.read_text(encoding="utf-8")


def test_a_public_question_may_use_the_fallback_while_the_prompt_stays_private(tmp_path, monkeypatch):
    script = tmp_path / "echo_ldr.py"
    script.write_text(FAKE_LDR.replace('echo = o.get("search.engine.web.tavily.api_key", "")',
                                       'echo = "key=" + job.get("pages", {}).get("tavily_key", "none") '
                                       '+ " local=" + str(o["llm.require_local_endpoint"])'), encoding="utf-8")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    settings = settings_for(tmp_path)
    result = run_research(settings, "q", RunLog(settings.runs_dir), runner=[sys.executable, str(script)],
                          transport=upstream([]), public_search=True)
    assert "key=*** local=True" in result.sources[0]["snippet"]
    with pytest.raises(PrivacyError):   # public_search never unlocks a cloud model
        run_research(settings, "q", RunLog(settings.runs_dir), tier="gemini", runner=["never-run"], public_search=True)
