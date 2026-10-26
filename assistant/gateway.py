"""One interface to every model tier, with every call written to the run log.

Privacy is default-deny: a request counts as private unless the caller says
otherwise, and a private request is never sent to a cloud tier. Stage 4 will
replace the caller's flag with a check of where the content came from.
"""
from __future__ import annotations

from bench.client import ChatClient, ChatResult

from .config import Settings, Tier
from .runlog import RunLog


class PrivacyError(RuntimeError):
    """A private request was addressed to a tier that leaves this PC."""


class TierNotReady(RuntimeError):
    """The tier needs an API key that is not set."""


class Gateway:
    def __init__(self, settings: Settings, log: RunLog, transport=None) -> None:
        self.settings = settings
        self.log = log
        self._transport = transport   # tests pass an httpx.MockTransport
        self._clients: dict[str, ChatClient] = {}

    def close(self) -> None:
        for client in self._clients.values():
            client.close()

    def _client(self, tier: Tier) -> ChatClient:
        if tier.name not in self._clients:
            self._clients[tier.name] = ChatClient(tier.base_url, tier.api_key, transport=self._transport)
        return self._clients[tier.name]

    def ask(self, tier_name: str, messages: list[dict], *, purpose: str = "",
            private: bool = True, max_tokens: int | None = None,
            tools: list[dict] | None = None, temperature: float | None = None) -> ChatResult:
        """Send one chat request to a tier and log it as a step."""
        tier = self.settings.tier(tier_name)
        base = {"tier": tier.name, "tier_kind": tier.kind, "model": tier.model,
                "purpose": purpose, "private": private}
        if private and tier.kind != "local":
            self.log.step("model", **base, outcome="blocked",
                          error="private request refused by a cloud tier")
            raise PrivacyError(f"tier {tier.name!r} leaves this PC; private requests stay local")
        if not tier.ready:
            self.log.step("model", **base, outcome="not_ready", error=f"{tier.api_key_env} is not set")
            raise TierNotReady(f"tier {tier.name!r} needs the {tier.api_key_env} environment variable")

        result = self._client(tier).chat(
            tier.model, messages, tools=tools,
            temperature=tier.temperature if temperature is None else temperature,
            max_tokens=max_tokens or tier.max_tokens, extra_body=tier.extra_body)
        # gen_tps counts from the first streamed token. Cloud tiers that think without
        # streaming it send the whole answer at the end, so overall_tps is the fair comparison.
        overall_tps = (round(result.completion_tokens / result.total_s, 3)
                       if result.completion_tokens and result.total_s > 0 else None)
        self.log.step("model", **base, outcome="error" if result.error else "ok",
                      **result.timing(), overall_tps=overall_tps, error=result.error,
                      prompt=messages, answer=result.content)
        return result
