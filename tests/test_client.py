import json

import httpx

import fake_model
from bench.client import ChatClient, assistant_message


def client_for(handler):
    return ChatClient("http://test/v1", "key", transport=httpx.MockTransport(handler))


def stream(*events, status=200):
    body = "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)
    return lambda request: httpx.Response(status, content=body.encode())


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def test_text_reasoning_and_usage():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, content=fake_model.sse({"content": "Hello there"}, reasoning="Thinking."))

    result = client_for(handler).chat("m", [{"role": "user", "content": "hi"}], temperature=0.2,
                                      max_tokens=50, seed=7, extra_body={"reasoning_effort": "low"})
    assert result.content == "Hello there" and result.reasoning == "Thinking."
    assert result.finish_reason == "stop" and not result.error and not result.tool_calls
    assert (result.prompt_tokens, result.completion_tokens, result.tokens_estimated) == (120, 40, False)
    assert result.ttft_s is not None and result.ttfa_s >= result.ttft_s and result.total_s >= result.ttfa_s
    body = seen["body"]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert (body["model"], body["temperature"], body["max_tokens"], body["seed"]) == ("m", 0.2, 50, 7)
    assert body["reasoning_effort"] == "low" and "tools" not in body
    assert seen["auth"] == "Bearer key"


def test_tool_calls_are_reassembled_from_fragments():
    answer = {"tool_calls": [("get_weather", '{"city": "Paris"}'), ("get_stock_price", '{"ticker": "ACME"}')]}
    result = client_for(lambda r: httpx.Response(200, content=fake_model.sse(answer))).chat(
        "m", [], tools=[{"type": "function"}])
    assert [(c.name, json.loads(c.arguments)) for c in result.tool_calls] == [
        ("get_weather", {"city": "Paris"}), ("get_stock_price", {"ticker": "ACME"})]
    assert result.finish_reason == "tool_calls" and result.content == ""
    message = assistant_message(result)
    assert message["tool_calls"][1]["function"]["name"] == "get_stock_price"
    assert message["tool_calls"][0]["id"] == "call_0" and "reasoning" not in message


def test_inline_think_tags_are_split_out():
    result = client_for(stream(delta(content="<think>step one</think>"), delta(content=" Answer."), "[DONE]")).chat("m", [])
    assert result.content == "Answer." and result.reasoning == "step one"


def test_tokens_are_estimated_when_the_server_sends_no_usage():
    result = client_for(stream(delta(content="x" * 400), "[DONE]")).chat("m", [])
    assert result.tokens_estimated and result.completion_tokens == 100 and result.gen_tps is not None


def test_garbage_lines_are_ignored():
    result = client_for(stream("not json", ": comment", delta(content="ok"), "[DONE]")).chat("m", [])
    assert result.content == "ok" and not result.error


def test_http_error_and_connection_error():
    result = client_for(lambda r: httpx.Response(400, text="model not found")).chat("m", [])
    assert "HTTP 400" in result.error and "model not found" in result.error

    def refuse(request):
        raise httpx.ConnectError("refused")
    result = client_for(refuse).chat("m", [])
    assert "ConnectError" in result.error and result.completion_tokens is None


def test_error_event_in_stream():
    result = client_for(stream({"error": {"message": "context overflow"}})).chat("m", [])
    assert "context overflow" in result.error


def test_models_and_native_chat():
    client = ChatClient("http://test/v1", transport=fake_model.transport())
    assert client.server_root == "http://test"
    assert client.model_info("fake-model")["loaded_context_length"] == 8192
    assert client.model_info("other") == {}
    outcome = client.native_chat("fake-model", "q", reasoning="off")
    assert outcome["answer"] == "17:05" and outcome["stats"]["tokens_per_second"] == 30.0


def test_native_chat_reports_unsupported_setting():
    outcome = client_for(lambda r: httpx.Response(400, text="reasoning not supported")).native_chat(
        "m", "q", reasoning="off")
    assert "HTTP 400" in outcome["error"]


def test_models_fall_back_to_openai_list():
    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "plain"}]})
        return httpx.Response(404)
    assert client_for(handler).list_models() == [{"id": "plain"}]


def test_cut_off_reply_keeps_reasoning_tail_and_flags_a_loop():
    from bench.client import REASONING_TAIL_CHARS, ChatResult
    looping = ChatResult(reasoning="Let me check the edge case again. " * 200, finish_reason="length")
    record = looping.timing()
    assert record["loop_suspected"] is True
    assert len(record["reasoning_tail"]) == REASONING_TAIL_CHARS

    progressing = ChatResult(reasoning="".join(f"step {i}: try value {i * 7}. " for i in range(400)),
                             finish_reason="length")
    assert progressing.timing()["loop_suspected"] is False

    answered = ChatResult(content="done", reasoning="thinking " * 50, finish_reason="stop")
    assert "reasoning_tail" not in answered.timing()

    empty = ChatResult(reasoning="The answer is 20 percent.", finish_reason="stop")
    assert empty.timing()["reasoning_tail"] == "The answer is 20 percent."
