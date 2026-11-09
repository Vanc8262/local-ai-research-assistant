import csv
import json
import math
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


def decider_reply(request, p_true: float = 0.95):
    """The decider's logprob request: a noul whose option A is "true"."""
    weights = {"A": p_true, "B": 1 - p_true}
    best = max(weights, key=weights.get)
    entry = {"token": best, "logprob": math.log(weights[best]),
             "top_logprobs": [{"token": k, "logprob": math.log(v)} for k, v in weights.items()]}
    return httpx.Response(200, json={"choices": [{"message": {"content": best}, "logprobs": {"content": [entry]}}]})


def transport(seen: list):
    def handler(request):
        if request.url.host == "local" and json.loads(request.content).get("logprobs"):
            return decider_reply(request)
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
    question = Question(id="py", text="What is new in Python 3.15?", expect=[["3.15"], ["lazy import"]])
    out_dir, rows = run_eval(settings_for(tmp_path), [question], ["local", "hybrid"],
                             runner=[sys.executable, str(script)], transport=transport(seen), runs=1)

    assert [(r["mode"], r["outcome"], r["coverage"], r["accuracy"], r["gate"]) for r in rows] == [
        ("local", "ok", 1.0, 1.0, "pass"), ("hybrid", "ok", 1.0, 1.0, "pass")]
    assert rows[0]["facts"] == 1.0
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
    assert reloads == [None, None, None, None]   # before each run of each question

    reload_model(settings, run=lambda cmd, **kw: calls.append(cmd[1:]))
    assert calls == [["unload", "--all"], ["load", "gemma-4-e4b-it-qat", "--context-length", "32768",
                                           "--gpu", "max", "--identifier", "gemma", "-y"]]


FLAGGED_LDR = FAKE_LDR.replace('"Python 3.15 adds lazy imports [1]."',
                               '"Python 3.15 adds lazy imports [1]. It also adds flying cars for every programmer [1]."')


def flagged_transport():
    def handler(request):
        if request.url.host == "local" and json.loads(request.content).get("logprobs"):
            return decider_reply(request)
        if request.url.host == "local":
            prompt = json.loads(request.content)["messages"][0]["content"]
            if "failed a citation check" in prompt:
                answer = {"action": "remove", "sentence": ""}
            elif "flying cars" in prompt:
                answer = {"verdict": "unsupported", "quote": "", "reason": "not stated"}
            elif "Claim:" in prompt:
                answer = {"verdict": "supported", "quote": "Python 3.15 adds lazy imports."}
            else:
                answer = "research step"
            content = answer if isinstance(answer, str) else json.dumps(answer)
            return httpx.Response(200, content=fake_model.sse({"content": content}))
        return httpx.Response(200, text="<p>Python 3.15 adds lazy imports.</p>", headers={"content-type": "text/html"})
    return httpx.MockTransport(handler)


def test_eval_scores_the_repaired_report_and_fails_one_that_lost_too_much(tmp_path):
    script = tmp_path / "fake_ldr.py"
    script.write_text(FLAGGED_LDR, encoding="utf-8")
    question = Question(id="py", text="What is new?", expect=[["flying cars"], ["lazy imports"]])
    out_dir, (row,) = run_eval(settings_for(tmp_path), [question], ["local"],
                               runner=[sys.executable, str(script)], transport=flagged_transport(), runs=1)

    assert (row["accuracy_before"], row["accuracy"]) == (0.5, 1.0)
    assert (row["claims_before"], row["claims"], row["removed"]) == (2, 1, 1)
    assert (row["facts_before"], row["facts"]) == (1.0, 0.5)
    assert row["removed_share"] == 0.5 and row["gate"] == "fail"   # clean, but half the report is gone
    assert "mean claims removed 50%" in (out_dir / "summary.md").read_text(encoding="utf-8")


def test_each_question_runs_twice_codex_only_where_marked_and_ram_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "gemini-test-key")
    script = tmp_path / "fake_ldr.py"
    script.write_text(FAKE_LDR, encoding="utf-8")
    questions = [Question(id="plain", text="q", expect=[["lazy imports"]]),
                 Question(id="paper", text="q", expect=[["lazy imports"]], codex=True)]
    out_dir, rows = run_eval(settings_for(tmp_path), questions, ["local", "codex"],
                             runner=[sys.executable, str(script)], transport=transport([]), ram_interval_s=60)
    assert [(r["question"], r["mode"], r["run"]) for r in rows] == [
        ("plain", "local", 1), ("plain", "local", 2),
        ("paper", "local", 1), ("paper", "codex", 1), ("paper", "local", 2), ("paper", "codex", 2)]
    assert rows[0]["gate"] == "pass" and rows[0]["min_free_ram_mb"] > 0
    assert rows[3]["outcome"] == "not_run"     # no codex index in the test settings
    ram = (out_dir / "ram.csv").read_text(encoding="utf-8").splitlines()
    assert ram[0].startswith("at_s,label,ram_available_mb") and len(ram) >= 3
    summary = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "Questions that passed on every run: 2 of 2." in summary and "Lowest free RAM" in summary


def test_a_clean_report_missing_expected_facts_fails_the_gate(tmp_path):
    script = tmp_path / "fake_ldr.py"
    script.write_text(FAKE_LDR, encoding="utf-8")
    question = Question(id="py", text="q", expect=[["lazy imports"], ["UTF-8"]])   # half the facts
    _, (row,) = run_eval(settings_for(tmp_path), [question], ["local"],
                         runner=[sys.executable, str(script)], transport=transport([]), runs=1)
    assert (row["coverage"], row["accuracy"], row["facts"], row["gate"]) == (1.0, 1.0, 0.5, "fail")
