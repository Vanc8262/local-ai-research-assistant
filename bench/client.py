"""A small client for OpenAI-compatible chat servers (LM Studio, llama.cpp,
and hosted APIs that speak the same protocol).

Requests are streamed so the client can time the first token itself instead of
trusting server-reported numbers, which differ between servers.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field

import httpx

THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
REASONING_TAIL_CHARS = 1500   # reasoning kept in the results for cut-off or empty replies
REPEAT_PROBE_CHARS = 200      # a repeat of this much text at the end counts as a loop


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it


@dataclass
class ChatResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_estimated: bool = False
    ttft_s: float | None = None   # time to the first token of any kind
    ttfa_s: float | None = None   # time to the first answer token (after reasoning)
    total_s: float = 0.0
    error: str | None = None

    @property
    def gen_tps(self) -> float | None:
        """Generated tokens per second, measured after the first token."""
        if not self.completion_tokens or self.ttft_s is None:
            return None
        span = self.total_s - self.ttft_s
        return self.completion_tokens / span if span > 0 else None

    @property
    def prompt_tps(self) -> float | None:
        """Prompt tokens per second. Approximate: includes network and queue time."""
        if not self.prompt_tokens or not self.ttft_s:
            return None
        return self.prompt_tokens / self.ttft_s

    @property
    def no_answer(self) -> bool:
        """True when the model produced neither answer text nor a tool call."""
        return not self.content.strip() and not self.tool_calls

    @property
    def loop_suspected(self) -> bool:
        """True when the end of the reasoning repeats a passage seen earlier in it."""
        probe = self.reasoning[-REPEAT_PROBE_CHARS:].strip()
        return len(probe) >= REPEAT_PROBE_CHARS // 2 and probe in self.reasoning[:-REPEAT_PROBE_CHARS]

    def timing(self) -> dict:
        record = {
            "ttft_s": _round(self.ttft_s),
            "ttfa_s": _round(self.ttfa_s),
            "total_s": _round(self.total_s),
            "gen_tps": _round(self.gen_tps),
            "prompt_tps": _round(self.prompt_tps),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_estimated": self.tokens_estimated,
            "reasoning_chars": len(self.reasoning),
            "finish_reason": self.finish_reason,
        }
        # When a reply is cut off or empty, keep the end of the reasoning so you can
        # see whether the model was looping or still making progress.
        if self.reasoning and (self.finish_reason == "length" or self.no_answer):
            record["loop_suspected"] = self.loop_suspected
            record["reasoning_tail"] = self.reasoning[-REASONING_TAIL_CHARS:]
        return record

    def to_record(self) -> dict:
        record = self.timing()
        record.update(
            content=self.content,
            reasoning=self.reasoning,
            tool_calls=[asdict(call) for call in self.tool_calls],
            error=self.error,
        )
        return record


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def assistant_message(result: ChatResult) -> dict:
    """The assistant turn to append to a conversation. Reasoning is not sent back."""
    message: dict = {"role": "assistant", "content": result.content or ""}
    if result.tool_calls:
        message["tool_calls"] = [
            {"id": call.id, "type": "function",
             "function": {"name": call.name, "arguments": call.arguments}}
            for call in result.tool_calls
        ]
    return message


class ChatClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 900.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        timeout = httpx.Timeout(timeout_s, connect=10.0)
        self.http = httpx.Client(headers=headers, timeout=timeout, transport=transport)

    @property
    def server_root(self) -> str:
        """Server address without the /v1 suffix, for LM Studio's own REST API."""
        return re.sub(r"/v1$", "", self.base_url)

    def close(self) -> None:
        self.http.close()

    # -- OpenAI-compatible chat ------------------------------------------------

    def chat(self, model: str, messages: list[dict], *, tools: list[dict] | None = None,
             temperature: float = 0.0, max_tokens: int = 3000, seed: int | None = None,
             extra_body: dict | None = None) -> ChatResult:
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if seed is not None:
            body["seed"] = seed
        if tools:
            body["tools"] = tools
        body.update(extra_body or {})

        result = ChatResult()
        calls: dict[int, dict] = {}
        started = time.perf_counter()
        try:
            with self.http.stream("POST", f"{self.base_url}/chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    result.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                else:
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        self._absorb(chunk, result, calls, started)
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        result.total_s = time.perf_counter() - started

        result.tool_calls = [
            ToolCall(id=slot["id"] or f"call_{index}", name=slot["name"], arguments=slot["arguments"])
            for index, slot in sorted(calls.items())
        ]
        # Some servers leave reasoning inline as <think>...</think>; split it out.
        inline = THINK_RE.findall(result.content)
        if inline:
            result.reasoning += "".join(inline)
            result.content = THINK_RE.sub("", result.content)
        result.content = result.content.strip()
        if result.completion_tokens is None and not result.error:
            produced = result.content + result.reasoning + "".join(c.arguments for c in result.tool_calls)
            result.completion_tokens = max(1, len(produced) // 4)
            result.tokens_estimated = True
        return result

    @staticmethod
    def _absorb(chunk: dict, result: ChatResult, calls: dict[int, dict], started: float) -> None:
        if chunk.get("error"):
            result.error = json.dumps(chunk["error"])[:500]
            return
        usage = chunk.get("usage")
        if usage:
            result.prompt_tokens = usage.get("prompt_tokens", result.prompt_tokens)
            result.completion_tokens = usage.get("completion_tokens", result.completion_tokens)
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            text = delta.get("content") or ""
            thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if not isinstance(thought, str):
                thought = ""
            tool_deltas = delta.get("tool_calls") or []
            if (text or thought or tool_deltas) and result.ttft_s is None:
                result.ttft_s = time.perf_counter() - started
            if (text or tool_deltas) and result.ttfa_s is None:
                result.ttfa_s = time.perf_counter() - started
            result.content += text
            result.reasoning += thought
            for part in tool_deltas:
                slot = calls.setdefault(part.get("index", 0), {"id": "", "name": "", "arguments": ""})
                function = part.get("function") or {}
                if part.get("id"):
                    slot["id"] = part["id"]
                if function.get("name") and not slot["name"]:
                    slot["name"] = function["name"]
                slot["arguments"] += function.get("arguments") or ""
            if choice.get("finish_reason"):
                result.finish_reason = choice["finish_reason"]

    # -- LM Studio's own REST API (best effort; absent on other servers) --------

    def list_models(self) -> list[dict]:
        """Model details from LM Studio if available, else the plain OpenAI list."""
        for url in (f"{self.server_root}/api/v0/models", f"{self.base_url}/models"):
            try:
                resp = self.http.get(url, timeout=15.0)
                if resp.status_code == 200:
                    data = resp.json().get("data")
                    if isinstance(data, list):
                        return data
            except (httpx.HTTPError, ValueError):
                continue
        return []

    def model_info(self, model: str) -> dict:
        for entry in self.list_models():
            if entry.get("id") == model:
                return entry
        return {}

    def native_chat(self, model: str, prompt: str, *, reasoning: str,
                    temperature: float = 0.0, max_output_tokens: int = 2000) -> dict:
        """One request through LM Studio's /api/v1/chat, which documents a
        `reasoning` setting ("off", "low", "medium", "high", "on") that the
        OpenAI-compatible endpoint does not. Returns the server's own stats."""
        body = {
            "model": model, "input": prompt, "reasoning": reasoning,
            "temperature": temperature, "max_output_tokens": max_output_tokens,
            "stream": False, "store": False,
        }
        started = time.perf_counter()
        try:
            resp = self.http.post(f"{self.server_root}/api/v1/chat", json=body)
        except httpx.HTTPError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        wall = round(time.perf_counter() - started, 3)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}", "wall_s": wall}
        try:
            data = resp.json()
        except ValueError:
            return {"error": "response was not JSON", "wall_s": wall}
        text = ""
        for item in data.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content")
                text += content if isinstance(content, str) else json.dumps(content)
        return {"wall_s": wall, "stats": data.get("stats") or {}, "answer": text.strip()}
