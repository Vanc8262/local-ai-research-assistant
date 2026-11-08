import json
import math
import re

import httpx
import pytest

import fake_model
from assistant import decide_check
from assistant.config import Settings, Tier
from assistant.decide import Decider, build_messages, choice, letter_of, noul, score
from assistant.gateway import Gateway, PrivacyError
from assistant.runlog import RunLog, read_run


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path / "runs", default_tier="local")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://local/v1", model="gemma")
    settings.tiers["gemini"] = Tier(name="gemini", kind="cloud", base_url="http://cloud/v1", model="g",
                                    api_key_env="PATH")
    return settings


def top(weights: dict[str, float]) -> list[dict]:
    return [{"token": t, "logprob": math.log(p)} for t, p in weights.items()]


def chat_reply(weights: dict[str, float], lead: list[str] = ()) -> dict:
    """A /chat/completions reply whose first answer token has these alternatives."""
    entries = [{"token": t, "logprob": 0.0, "top_logprobs": [{"token": t, "logprob": 0.0}]} for t in lead]
    best = max(weights, key=weights.get)
    entries.append({"token": best, "logprob": math.log(weights[best]), "top_logprobs": top(weights)})
    return {"choices": [{"message": {"content": best.strip()}, "logprobs": {"content": entries}}]}


def make(tmp_path, handler, **kwargs):
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir, task="test")
    transport = httpx.MockTransport(handler)
    gateway = Gateway(settings, log, transport=transport)
    return Decider(settings, gateway, log, transport=transport, **kwargs), log


def test_letter_of_accepts_common_spellings():
    assert [letter_of(t) for t in ["A", " B", "C.", "**D", "(E)", "a"]] == list("ABCDE") + ["A"]
    assert letter_of("An") == "" and letter_of("\n") == "" and letter_of("12") == ""


def test_prompt_puts_state_first_and_letters_options():
    q = choice("Which team?", {"billing": "money", "technical": "bugs"})
    user = build_messages("my state", q, list(q.options))[1]["content"]
    assert user.startswith("State:\n<<<\nmy state\n>>>")
    assert "A. billing: money" in user and "B. technical: bugs" in user and "A to B" in user


def test_chat_logprobs_give_probabilities_per_option(tmp_path):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat_reply({"B": 0.6, " A": 0.2, "C": 0.1, "Hmm": 0.1}, lead=["\n"]))

    decider, log = make(tmp_path, handler)
    d = decider.ask("ticket text", choice("Which team?", {"billing": "x", "technical": "y", "sales": "z"}))
    assert d.method == "chat" and d.answer == "technical" and not d.error
    assert d.probabilities == pytest.approx({"billing": 2 / 9, "technical": 6 / 9, "sales": 1 / 9}, abs=1e-3)
    assert d.mass == pytest.approx(0.9, abs=1e-3)
    assert 0 < d.confidence < 1
    assert bodies[0]["logprobs"] is True and bodies[0]["temperature"] == 0.0 and bodies[0]["stream"] is False
    step = [r for r in read_run(log.path) if r.get("kind") == "decide"][0]
    assert step["private"] is True and step["method"] == "chat" and step["answer"] == "technical"


def test_noul_and_score_helpers(tmp_path):
    def handler(request):
        prompt = json.loads(request.content)["messages"][1]["content"]
        weights = {"A": 0.8, "B": 0.2} if "statement" in prompt else {"A": 0.1, "B": 0.2, "C": 0.7}
        return httpx.Response(200, json=chat_reply(weights))

    decider, _ = make(tmp_path, handler)
    yes = decider.ask("sky is blue", noul("The sky is blue."))
    assert yes.answer == "true" and yes.p_true == pytest.approx(0.8)
    level = decider.ask("angry text", score("How angry?", ["calm", "cross", "furious"]))
    assert level.answer == "3" and level.level == pytest.approx(0.2 * 0.5 + 0.7 * 1.0)


def test_auto_falls_back_to_responses_then_greedy(tmp_path):
    hits = []

    def handler(request):
        hits.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions") and not body.get("stream"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "B"}}]})   # no logprobs
        if request.url.path.endswith("/responses"):
            return httpx.Response(400, json={"error": "top_logprobs not supported"})
        return httpx.Response(200, content=fake_model.sse({"content": "B"}))              # greedy, streamed

    decider, _ = make(tmp_path, handler)
    d = decider.ask("s", noul("x"))
    assert d.method == "greedy" and d.answer == "false" and d.probabilities["false"] == 1.0
    assert hits == ["/v1/chat/completions", "/v1/responses", "/v1/chat/completions"]
    hits.clear()
    decider.ask("s", noul("x"))                 # the working method is remembered
    assert hits == ["/v1/chat/completions"]


def test_responses_logprobs_are_read(tmp_path):
    def handler(request):
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "A"}}]})
        entry = {"token": "A", "logprob": math.log(0.7), "top_logprobs": top({"A": 0.7, "B": 0.3})}
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "A", "logprobs": [entry]}]}]})

    decider, _ = make(tmp_path, handler)
    d = decider.ask("s", noul("x"))
    assert d.method == "responses" and d.p_true == pytest.approx(0.7)


def test_no_option_letter_is_an_error_not_a_guess(tmp_path):
    decider, _ = make(tmp_path, lambda r: httpx.Response(200, json=chat_reply({"Sure": 0.9, "I": 0.1})))
    d = decider.ask("s", noul("x"))
    assert d.error and d.answer == "" and d.confidence == 0.0


def test_debias_averages_both_orders(tmp_path):
    def handler(request):          # a model that always prefers the first option
        return httpx.Response(200, json=chat_reply({"A": 0.7, "B": 0.3}))

    decider, _ = make(tmp_path, handler, debias=True)
    d = decider.ask("s", noul("x"))
    assert d.probabilities == pytest.approx({"true": 0.5, "false": 0.5})


def test_cloud_tiers_are_refused_and_logged(tmp_path):
    decider, log = make(tmp_path, lambda r: pytest.fail("no request may leave"))
    with pytest.raises(PrivacyError):
        decider.ask("private diff", noul("x"), tier="gemini")
    step = [r for r in read_run(log.path) if r.get("kind") == "decide"][0]
    assert step["outcome"] == "blocked"


def test_eval_cases_load_and_cover_the_priority_use_cases():
    cases = decide_check.load_cases("evals/decide_cases.toml")
    uses = {c.use for c in cases}
    assert {"request routing", "model routing", "eval grading", "bulk labelling", "agent control",
            "map-reduce features", "guardrails"} <= uses
    assert len({c.id for c in cases}) == len(cases)


def test_check_scores_an_oracle_perfectly(tmp_path):
    cases = decide_check.load_cases("evals/decide_cases.toml")
    by_state = {c.state.strip(): c for c in cases}

    def handler(request):          # find the case, then put the weight on its expected option's letter
        prompt = json.loads(request.content)["messages"][1]["content"]
        state = re.search(r"<<<\n(.*?)\n>>>", prompt, re.DOTALL).group(1)
        case = by_state[state]
        letter = "ABCDEFGHIJ"[list(case.question.options).index(case.expect)]
        return httpx.Response(200, json=chat_reply({letter: 0.9, "Z": 0.1}))

    decider, _ = make(tmp_path, handler)
    result = decide_check.run_check(decider, cases, "local")
    s = result["summary"]
    assert s["correct"] == s["cases"] == len(cases) and s["methods"] == {"chat": len(cases)}
    out = tmp_path / "check.json"
    decide_check.save([result], out)
    assert "Correct:" in out.with_suffix(".md").read_text(encoding="utf-8")
