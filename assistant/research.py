"""Cited research reports: Local Deep Research driven through the gateway.

`run_research` starts the gateway proxy on a free port, then runs LDR in its own
venv (see ldr_runner.py) with LDR's model provider pointed at that proxy. Every
model call LDR makes is therefore a logged, privacy-checked gateway step. The
report, its sources and LDR's raw result are saved under runs/<run_id>/.

Privacy follows the rest of the assistant: a run is private unless public=True.
A private run may use only local tiers and the local SearXNG. Tavily is a cloud
service that sees the query, so it needs a public run, like a cloud model tier.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .gateway import Gateway, PrivacyError, TierNotReady
from .proxy import Proxy, make_server
from .runlog import RunLog

RUNNER = Path(__file__).with_name("ldr_runner.py")
SEARCH_TOOLS = ("searxng", "tavily")
CLOUD_SEARCH = {"tavily": "TAVILY_API_KEY"}


@dataclass
class ResearchResult:
    run_dir: Path
    summary: str = ""
    sources: list[dict] = field(default_factory=list)
    elapsed_s: float | None = None
    error: str | None = None

    @property
    def report_path(self) -> Path:
        return self.run_dir / "report.md"


def build_overrides(settings: Settings, *, tier: str, search: str, proxy_url: str, private: bool,
                    tavily_key: str = "") -> dict:
    """LDR settings for one run. LDR's model provider is the gateway proxy, never a model server."""
    research = settings.research
    overrides = {
        "llm.provider": "openai_endpoint",
        "llm.openai_endpoint.url": proxy_url,
        "llm.openai_endpoint.api_key": "via-gateway",   # the proxy holds the real keys
        "llm.model": tier,
        "llm.local_context_window_size": research.context_window,
        "llm.max_tokens": settings.tier(tier).max_tokens,
        "llm.temperature": settings.tier(tier).temperature,
        "llm.require_local_endpoint": private,
        "search.tool": search,
        "search.engine.web.searxng.default_params.instance_url": settings.searxng_url,
        "search.iterations": research.iterations,
        "web.host": "127.0.0.1",   # LDR's default, 0.0.0.0, would expose its UI to the LAN
    }
    if search == "tavily":
        overrides["search.engine.web.tavily.api_key"] = tavily_key
    return overrides


def run_research(settings: Settings, query: str, log: RunLog, *, tier: str | None = None,
                 search: str = "searxng", public: bool = False, mode: str = "quick",
                 runner: list[str] | None = None, transport=None) -> ResearchResult:
    tier = tier or settings.default_tier
    run_dir = Path(settings.runs_dir) / log.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    base = {"query": query, "tier": tier, "search": search, "mode": mode, "private": not public}

    if search not in SEARCH_TOOLS:
        raise ValueError(f"search must be one of {SEARCH_TOOLS}, not {search!r}")
    if not public and (settings.tier(tier).kind != "local" or search in CLOUD_SEARCH):
        log.step("research", **base, outcome="blocked", error="private research stays on this PC")
        raise PrivacyError(f"tier {tier!r} with search {search!r} leaves this PC; add --public "
                           "if the question holds nothing private")
    key_env = CLOUD_SEARCH.get(search, "")
    secret = os.environ.get(key_env, "") if key_env else ""
    if key_env and not secret:
        log.step("research", **base, outcome="not_ready", error=f"{key_env} is not set")
        raise TierNotReady(f"search {search!r} needs the {key_env} environment variable")

    gateway = Gateway(settings, log, transport=transport)
    server = make_server(Proxy(settings, gateway, private=not public), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    proxy_url = f"http://{host}:{port}/v1"
    out = run_dir / "result.json"
    job = {"query": query, "mode": mode, "out": str(out),
           "overrides": build_overrides(settings, tier=tier, search=search, proxy_url=proxy_url,
                                        private=not public, tavily_key=secret)}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           # LDR refuses loopback URLs unless allowed; allow exactly our SearXNG and proxy.
           "LDR_SEARCH_PRIVATE_ENGINE_URL_ALLOWLIST": f"{settings.searxng_url},{proxy_url}"}
    command = runner or [str(settings.research.ldr_python), str(RUNNER)]
    result = ResearchResult(run_dir=run_dir)
    try:
        proc = subprocess.run(command, input=json.dumps(job), text=True, encoding="utf-8",
                              capture_output=True, env=env, timeout=settings.research.timeout_s)
        if out.exists():
            _scrub(out, secret)
            record = json.loads(out.read_text(encoding="utf-8"))
            result.error, result.elapsed_s = record.get("error"), record.get("elapsed_s")
            raw = record.get("result") or {}
            result.summary = raw.get("summary") or raw.get("content") or ""
            result.sources = raw.get("sources") or raw.get("all_links_of_system") or []
        else:
            result.error = f"LDR exited {proc.returncode}: {_scrubbed(proc.stderr, secret)[-1500:]}"
    except FileNotFoundError:
        result.error = f"LDR's Python was not found at {command[0]} (set [research] ldr_python)"
    except subprocess.TimeoutExpired:
        result.error = f"LDR took longer than {settings.research.timeout_s} s"
    finally:
        server.shutdown()
        server.server_close()
        gateway.close()

    if result.summary:
        result.report_path.write_text(report_markdown(query, result), encoding="utf-8")
    (run_dir / "sources.json").write_text(json.dumps(result.sources, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    log.step("research", **base, outcome="error" if result.error or not result.summary else "ok",
             elapsed_s=result.elapsed_s, n_sources=len(result.sources), report_chars=len(result.summary),
             error=result.error, report=str(result.report_path) if result.summary else None)
    return result


def report_markdown(query: str, result: ResearchResult) -> str:
    lines = [f"# {query}", "", result.summary.strip(), "", "## Sources", ""]
    for number, source in enumerate(result.sources, start=1):
        index = source.get("index") or number
        title = source.get("title") or source.get("link") or "untitled"
        lines.append(f"[{index}] {title}  \n    {source.get('link') or source.get('url') or ''}")
    return "\n".join(lines) + "\n"


def _scrubbed(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _scrub(path: Path, secret: str) -> None:
    """Keys are passed to LDR in memory; make sure none is echoed into a saved file."""
    if secret:
        text = path.read_text(encoding="utf-8")
        if secret in text:
            path.write_text(text.replace(secret, "***"), encoding="utf-8")

