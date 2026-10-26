import csv
import json
import sys
from pathlib import Path

import httpx

import fake_model
from assistant.config import Settings, Tier
from assistant.evaluate import Question, load_questions, run_eval

ROOT = Path(__file__).resolve().parent.parent

# Stands in for LDR: one model call through the proxy, then a one-claim report citing [1].
FAKE_LDR = r'''
import json, sys, urllib.request
job = json.load(sys.stdin)
o = job["overrides"]
body = json.dumps({"model": o["llm.model"], "messages": [{"role": "user", "content": job["query"]}]}).encode()
req = urllib.request.Request(o["llm.openai_endpoint.url"] + "/chat/completions", body,
                             {"Content-Type": "application/json"})
json.load(urllib.request.urlopen(req))
result = {"summary": "Python 3.15 adds lazy imports [1].", "sources": [
    {"index": "1", "title": "What's new", "link": "https://docs.python.org/3/whatsnew/3.15.html"}]}
json.dump({"result": result, "error": None, "elapsed_s": 1.0}, open(job["out"], "w"))
'''


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path / "runs", default_tier="local")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://local/v1", model="gemma")
    settings.tiers["gemini"] = Tier(name="gemini", kind="cloud", base_url="http://cloud/v1",
                                    model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY")
    return settings


def transport(seen: list):
    def handler(request):
        if request.url.host in ("local", "cloud"):
            seen.append(request.url.host)
            prompt = json.loads(request.content)["messages"][0]["content"]
            answer = (json.dumps({"verdict": "supported", "quote": "Python 3.15 adds lazy imports."})
                      if "Claim:" in prompt else "research step")
            return httpx.Response(200, content=fake_model.sse({"content": answer}))
        return httpx.Response(200, text="<p>Python 3.15 adds lazy imports.</p>", headers={"content-type": "text/html"})
    return httpx.MockTransport(handler)


def test_eval_runs_each_mode_verifies_locally_and_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "gemini-test-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    script = tmp_path / "fake_ldr.py"
    script.write_text(FAKE_LDR, encoding="utf-8")
    seen = []
    question = Question(id="py", text="What is new in Python 3.15?", expect=[["3.15"], ["lazy import"], ["UTF-8"]])
    out_dir, rows = run_eval(settings_for(tmp_path), [question], ["local", "hybrid"],
                             runner=[sys.executable, str(script)], transport=transport(seen))

    assert [(r["mode"], r["outcome"], r["coverage"], r["accuracy"], r["gate"]) for r in rows] == [
        ("local", "ok", 1.0, 1.0, "pass"), ("hybrid", "ok", 1.0, 1.0, "pass")]
    assert rows[0]["facts"] == round(2 / 3, 3)
    # research went to the mode's tier; the citation check always ran locally
    assert seen == ["local", "local", "cloud", "local"]
    assert rows[0]["local_tokens"] > 0 and rows[0]["cloud_tokens"] == 0
    assert rows[1]["cloud_tokens"] > 0 and rows[1]["local_tokens"] == 0
    with (out_dir / "summary.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 2
    summary = (out_dir / "summary.md").read_text()
    assert "**local:** 1 reports" in summary and "1 pass the gate" in summary


def test_a_mode_that_cannot_run_is_recorded_not_fatal(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    question = Question(id="q", text="q")
    _, rows = run_eval(settings_for(tmp_path), [question], ["hybrid"], runner=["never-run"], transport=transport([]))
    assert rows[0]["outcome"] == "not_run" and "TAVILY_API_KEY" in rows[0]["error"]


def test_shipped_questions_load():
    questions = load_questions(ROOT / "evals" / "research_questions.toml")
    assert len(questions) == 5 and all(q.expect for q in questions)


def test_model_is_reloaded_before_each_question(tmp_path, monkeypatch):
    from assistant.evaluate import reload_model
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    settings = settings_for(tmp_path)
    settings.tiers["local"].lms_key = "gemma-4-e4b-it-qat"
    reloads, calls = [], []
    questions = [Question(id="a", text="a"), Question(id="b", text="b")]
    run_eval(settings, questions, ["hybrid"], reload=True, runner=["never-run"], transport=transport([]),
             reloader=lambda s, tier: reloads.append(tier))
    assert reloads == [None, None]

    reload_model(settings, run=lambda cmd, **kw: calls.append(cmd[1:]))
    assert calls == [["unload", "--all"], ["load", "gemma-4-e4b-it-qat", "--context-length", "32768",
                                           "--gpu", "max", "--identifier", "gemma", "-y"]]
