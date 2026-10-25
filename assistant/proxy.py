"""An OpenAI-compatible front door to the gateway, for tools that make their own model calls.

Local Deep Research (Stage 2) calls a model many times per report. Pointing it at
this proxy instead of LM Studio sends every one of those calls through Gateway.ask,
so each is logged with its tier and timing and passes the privacy check.

The request's "model" field names a tier ("local", "gemini", ...) or a tier's model.
A proxy session is private or public as a whole: a private session refuses cloud
tiers. In a public session a cloud call that fails (rate limit, outage, missing key)
is retried once on the default local tier, and both attempts are logged.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bench.client import ChatResult, assistant_message

from .config import Settings, Tier
from .gateway import Gateway, PrivacyError, TierNotReady

PURPOSE = "proxy"


class ProxyError(Exception):
    def __init__(self, status: int, message: str, kind: str) -> None:
        super().__init__(message)
        self.status, self.message, self.kind = status, message, kind


class Proxy:
    def __init__(self, settings: Settings, gateway: Gateway, *, private: bool = True) -> None:
        self.settings = settings
        self.gateway = gateway
        self.private = private
        # LM Studio answers one request at a time anyway, and this keeps run-log
        # step numbers in order.
        self._lock = threading.Lock()

    def resolve(self, model: str) -> Tier:
        """Find the tier a request means, by tier name first, then by model id."""
        if model in self.settings.tiers:
            return self.settings.tiers[model]
        for tier in self.settings.tiers.values():
            if tier.model == model:
                return tier
        raise ProxyError(404, f"unknown model {model!r}; use a tier name: {', '.join(self.settings.tiers)}",
                         "model_not_found")

    def models(self) -> dict:
        return {"object": "list", "data": [
            {"id": name, "object": "model", "owned_by": tier.kind, "label": tier.label}
            for name, tier in self.settings.tiers.items()]}

    def complete(self, body: dict) -> tuple[Tier, ChatResult]:
        """Answer one chat request, falling back to the local tier if a public cloud call fails."""
        if not isinstance(body.get("messages"), list):
            raise ProxyError(400, "request needs a messages list", "invalid_request_error")
        tier = self.resolve(str(body.get("model", self.settings.default_tier)))
        options = {"purpose": PURPOSE, "private": self.private,
                   "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
                   "tools": body.get("tools")}
        with self._lock:
            try:
                result = self.gateway.ask(tier.name, body["messages"], **options)
            except PrivacyError as exc:
                raise ProxyError(403, str(exc), "privacy_blocked") from None
            except TierNotReady as exc:
                if not self._can_fall_back(tier):
                    raise ProxyError(503, str(exc), "tier_not_ready") from None
                result = ChatResult(error=str(exc))
            if result.error and self._can_fall_back(tier):
                options["purpose"] = f"{PURPOSE} (fallback from {tier.name})"
                tier = self.settings.tier(self.settings.default_tier)
                result = self.gateway.ask(tier.name, body["messages"], **options)
        if result.error:
            raise ProxyError(502, result.error, "upstream_error")
        return tier, result

    def _can_fall_back(self, tier: Tier) -> bool:
        return tier.kind == "cloud" and self.settings.default_tier != tier.name


def completion(tier: Tier, result: ChatResult) -> dict:
    """The non-streaming OpenAI response body."""
    return {
        "id": f"chatcmpl-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
        "model": tier.name,
        "choices": [{"index": 0, "message": assistant_message(result),
                     "finish_reason": _finish(result)}],
        "usage": _usage(result),
    }


def stream_events(tier: Tier, result: ChatResult) -> list[bytes]:
    """The same answer as server-sent events, sent in one piece once it is complete."""
    head = {"id": f"chatcmpl-{time.time_ns()}", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": tier.name}
    delta: dict = {"role": "assistant", "content": result.content or ""}
    if result.tool_calls:
        delta["tool_calls"] = [
            {"index": index, "id": call.id, "type": "function",
             "function": {"name": call.name, "arguments": call.arguments}}
            for index, call in enumerate(result.tool_calls)]
    chunks = [
        {**head, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**head, "choices": [{"index": 0, "delta": {}, "finish_reason": _finish(result)}]},
        {**head, "choices": [], "usage": _usage(result)},
    ]
    return [f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks] + [b"data: [DONE]\n\n"]


def _finish(result: ChatResult) -> str:
    return result.finish_reason or ("tool_calls" if result.tool_calls else "stop")


def _usage(result: ChatResult) -> dict:
    prompt, completion_tokens = result.prompt_tokens or 0, result.completion_tokens or 0
    return {"prompt_tokens": prompt, "completion_tokens": completion_tokens,
            "total_tokens": prompt + completion_tokens}


def make_server(proxy: Proxy, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                self._json(200, proxy.models())
            else:
                self._json(404, _error("not found", "not_found"))

        def do_POST(self) -> None:
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                self._json(404, _error("not found", "not_found"))
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                tier, result = proxy.complete(body)
            except json.JSONDecodeError:
                self._json(400, _error("body is not JSON", "invalid_request_error"))
                return
            except ProxyError as exc:
                self._json(exc.status, _error(exc.message, exc.kind))
                return
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in stream_events(tier, result):
                    self.wfile.write(event)
            else:
                self._json(200, completion(tier, result))

        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args) -> None:   # the run log is the record; keep the console quiet
            pass

    return ThreadingHTTPServer((host, port), Handler)


def _error(message: str, kind: str) -> dict:
    return {"error": {"message": message, "type": kind}}
