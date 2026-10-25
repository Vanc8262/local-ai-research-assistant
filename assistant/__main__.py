"""Command line for the assistant.

    python -m assistant tiers                               list tiers and whether each is usable
    python -m assistant ask "prompt"                        ask the default (local) tier
    python -m assistant ask --public -t local -t gemini "prompt"
                                                            the same prompt on several tiers, compared
    python -m assistant search "query"                      search through the local SearXNG
    python -m assistant show <run_id>                       print the steps of a logged run
    python -m assistant proxy [--public]                    serve the tiers as an OpenAI-compatible
                                                            endpoint for tools such as LDR
    python -m assistant codex list|fetch [--only id ...]    the local library of papers and books
    python -m assistant research "question"                 a cited report from Local Deep Research
    python -m assistant research --public -t gemini --search tavily "question"

Without --public a prompt counts as private and is refused by cloud tiers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from .codex import CatalogError, fetch, load_catalog, read_manifest
from .config import load_settings
from .gateway import Gateway, PrivacyError, TierNotReady
from .proxy import Proxy, make_server
from .research import SEARCH_TOOLS, run_research
from .runlog import RunLog, read_run
from .search import SearchError, search


def cmd_tiers(settings, args) -> int:
    print(f"{'tier':<14} {'kind':<6} {'model':<20} {'key':<8} server")
    for tier in settings.tiers.values():
        key = "n/a" if not tier.api_key_env else ("set" if tier.ready else "MISSING")
        try:
            headers = {"Authorization": f"Bearer {tier.api_key}"} if tier.ready else {}
            status = httpx.get(f"{tier.base_url}/models", headers=headers, timeout=10).status_code
            server = "reachable" if status == 200 else f"HTTP {status}"
        except httpx.HTTPError as exc:
            server = f"unreachable ({type(exc).__name__})"
        marks = " (default)" if tier.name == settings.default_tier else (
            " (backup)" if tier.name == settings.backup_tier else "")
        print(f"{tier.name:<14} {tier.kind:<6} {tier.model:<20} {key:<8} {server}{marks}")
    return 0


def cmd_ask(settings, args) -> int:
    tiers = args.tier or [settings.default_tier]
    log = RunLog(settings.runs_dir, task=f"ask: {args.prompt[:200]}")
    gateway = Gateway(settings, log)
    messages = [{"role": "user", "content": args.prompt}]
    rows = []
    try:
        for name in tiers:
            print(f"\n=== {name} ===")
            try:
                result = gateway.ask(name, messages, purpose="ask", private=not args.public,
                                     max_tokens=args.max_tokens)
            except PrivacyError as exc:
                print(f"blocked: {exc}")
                rows.append((name, "blocked", None, None, None, None))
                continue
            except (TierNotReady, KeyError) as exc:
                print(f"not ready: {exc}")
                rows.append((name, "not_ready", None, None, None, None))
                continue
            print(result.error or result.content)
            tokens = result.completion_tokens
            overall = tokens / result.total_s if tokens and result.total_s > 0 else None
            rows.append((name, "error" if result.error else "ok", tokens, result.ttft_s, overall, result.total_s))
    finally:
        gateway.close()
    # tok/s here is tokens over total time, which is comparable across local and cloud tiers.
    print(f"\n{'tier':<14} {'outcome':<9} {'tokens':>7} {'first tok':>10} {'tok/s':>7} {'total':>7}")
    for name, outcome, tokens, ttft, tps, total in rows:
        print(f"{name:<14} {outcome:<9} {_fmt(tokens, '{}'):>7} {_fmt(ttft, '{:.2f} s'):>10} "
              f"{_fmt(tps, '{:.1f}'):>7} {_fmt(total, '{:.1f} s'):>7}")
    print(f"\nRun log: {log.path}")
    return 0 if all(row[1] == "ok" for row in rows) else 1


def cmd_search(settings, args) -> int:
    log = RunLog(settings.runs_dir, task=f"search: {args.query}")
    try:
        hits = search(settings.searxng_url, args.query, log, limit=args.limit)
    except SearchError as exc:
        print(f"search failed: {exc}\nIs SearXNG running? Try: docker compose up -d")
        return 1
    for number, hit in enumerate(hits, start=1):
        print(f"[{number}] {hit.title}\n    {hit.url}\n    {hit.snippet[:200]}")
    print(f"\nRun log: {log.path}")
    return 0


def cmd_show(settings, args) -> int:
    path = Path(args.run) if args.run.endswith(".jsonl") else settings.runs_dir / f"{args.run}.jsonl"
    for record in read_run(path):
        if record["event"] != "step":
            print(f"{record['event']}: {record.get('task', '')}")
            continue
        detail = (f"{record.get('tier')} ({record.get('tier_kind')}) {record.get('model', '')}"
                  if record["kind"] == "model" else f"{record.get('query', '')!r} -> {record.get('hits')} hits")
        print(f"{record['step']:>3}. {record['kind']:<7} {record['outcome']:<9} {detail}  "
              f"tokens={record.get('completion_tokens')} tok/s={record.get('overall_tps')} "
              f"total={record.get('total_s')} s")
    return 0


def cmd_proxy(settings, args) -> int:
    mode = "public: cloud tiers allowed" if args.public else "private: cloud tiers refused"
    log = RunLog(settings.runs_dir, task=f"proxy ({mode})")
    gateway = Gateway(settings, log)
    server = make_server(Proxy(settings, gateway, private=not args.public), port=args.port or settings.proxy_port)
    host, port = server.server_address[:2]
    print(f"Serving tiers {', '.join(settings.tiers)} at http://{host}:{port}/v1 ({mode})")
    print(f"Run log: {log.path}\nCtrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        gateway.close()
    return 0


def cmd_codex(settings, args) -> int:
    try:
        items = load_catalog(args.catalog)
    except CatalogError as exc:
        print(f"catalog error: {exc}")
        return 1
    if args.action == "list":
        manifest = read_manifest(settings.codex_dir)
        for item in items:
            state = "have" if item.id in manifest else ("html" if item.kind == "html" else "-")
            print(f"{state:<5} {item.topic:<21} {item.id:<24} {item.title}")
        print(f"\n{len(manifest)} of {len(items)} in {settings.codex_dir}")
        return 0
    log = RunLog(settings.runs_dir, task=f"codex fetch into {settings.codex_dir}")
    try:
        outcomes = fetch(items, settings.codex_dir, log, only=args.only)
    except CatalogError as exc:
        print(f"catalog error: {exc}")
        return 1
    for item, outcome in outcomes:
        print(f"{outcome:<14} {item.topic}/{item.id}")
    failed = [item.id for item, outcome in outcomes if outcome not in ("downloaded", "present", "skipped_html")]
    print(f"\nManifest: {settings.codex_dir / 'manifest.csv'}\nRun log: {log.path}")
    if failed:
        print(f"Failed: {', '.join(failed)} (fix the URL in {args.catalog} and run again)")
    return 1 if failed else 0


def cmd_research(settings, args) -> int:
    log = RunLog(settings.runs_dir, task=f"research: {args.question[:200]}")
    try:
        result = run_research(settings, args.question, log, tier=args.tier, search=args.search,
                              public=args.public, mode="report" if args.report else "quick")
    except PrivacyError as exc:
        print(f"blocked: {exc}")
        return 1
    except (TierNotReady, KeyError, ValueError) as exc:
        print(f"not ready: {exc}")
        return 1
    if result.error:
        print(f"research failed: {result.error}")
    if result.summary:
        print(result.report_path.read_text(encoding="utf-8"))
    print(f"Run folder: {result.run_dir}\nRun log: {log.path}")
    return 0 if result.summary and not result.error else 1


def _fmt(value, pattern: str) -> str:
    return "-" if value is None else pattern.format(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="assistant.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tiers", help="list tiers and check that each is usable")
    ask = sub.add_parser("ask", help="send one prompt to one or more tiers")
    ask.add_argument("prompt")
    ask.add_argument("-t", "--tier", action="append", help="tier name; repeat to compare tiers")
    ask.add_argument("--public", action="store_true",
                     help="the prompt holds no private code, so cloud tiers may receive it")
    ask.add_argument("--max-tokens", type=int, default=None)
    find = sub.add_parser("search", help="search the web through SearXNG")
    find.add_argument("query")
    find.add_argument("-n", "--limit", type=int, default=8)
    show = sub.add_parser("show", help="print the steps of a logged run")
    show.add_argument("run", help="run id or path to its .jsonl file")
    proxy = sub.add_parser("proxy", help="serve the tiers as an OpenAI-compatible endpoint on 127.0.0.1")
    proxy.add_argument("--port", type=int, default=None, help="default: proxy_port in assistant.toml")
    proxy.add_argument("--public", action="store_true",
                       help="every request in this session is public, so cloud tiers may receive it")
    codex = sub.add_parser("codex", help="list or download the papers and books in codex/catalog.toml")
    codex.add_argument("action", choices=("list", "fetch"))
    codex.add_argument("--catalog", default="codex/catalog.toml")
    codex.add_argument("--only", nargs="+", metavar="ID", help="fetch just these catalog ids")
    research = sub.add_parser("research", help="a cited report from Local Deep Research, via the gateway")
    research.add_argument("question")
    research.add_argument("-t", "--tier", default=None, help="model tier for LDR (default: the default tier)")
    research.add_argument("--search", choices=SEARCH_TOOLS, default="searxng")
    research.add_argument("--public", action="store_true",
                          help="the question holds nothing private, so cloud tiers and Tavily may see it")
    research.add_argument("--report", action="store_true", help="a long sectioned report instead of a summary")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    handler = {"tiers": cmd_tiers, "ask": cmd_ask, "search": cmd_search, "show": cmd_show,
               "proxy": cmd_proxy, "codex": cmd_codex,
               "research": cmd_research}[args.command]
    return handler(settings, args)


if __name__ == "__main__":
    sys.exit(main())
