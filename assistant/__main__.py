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
    python -m assistant codex index [--force]               embed the codex PDFs for --search codex
    python -m assistant research "question"                 a cited report from Local Deep Research
    python -m assistant research --public -t gemini --search tavily "question"
    python -m assistant verify <run_id>                     check a report's citations against its sources
python -m assistant repair <run_id>                     fix or remove the claims that failed the check
    python -m assistant eval [--mode local --mode hybrid]   the Stage 2 eval: research + verify per question
    python -m assistant decide probe [-t local]             which decision method works on a local tier
    python -m assistant decide check [-t local -t local-backup] [--debias]
                                                            score the decider on evals/decide_cases.toml
    python -m assistant vault plan                          what the picks in vault.toml would add
    python -m assistant vault sync [--only NAME ...]        store the picked models, nothing else
    python -m assistant vault list|verify [NAME ...]        the stored models, and a re-hash of their files
    python -m assistant vault scan <file>                   check one weight file before loading it

Without --public a prompt counts as private and is refused by cloud tiers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from .codex import CatalogError, build_index, fetch, load_catalog, read_manifest
from .config import load_settings
from .decide import METHODS, Decider, noul
from . import decide_check
from .evaluate import MODES, load_questions, reload_model, run_eval
from .gateway import Gateway, PrivacyError, TierNotReady
from .proxy import Proxy, make_server
from .research import SEARCH_TOOLS, run_research
from .runlog import RunLog, read_run
from .search import SearchError, search
from .repair import RepairError, repair_run
from .verify import verify_run
from . import vault


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
        if record["kind"] == "model":
            detail = f"{record.get('tier')} ({record.get('tier_kind')}) {record.get('model', '')}"
        elif record["kind"] == "research":
            detail = f"{record.get('tier')} + {record.get('search')} -> {record.get('n_sources')} sources"
        elif record["kind"] == "download":
            detail = f"{record.get('topic')}/{record.get('id')} {record.get('bytes')} bytes"
        elif record["kind"] == "vault":
            detail = f"{record.get('action')} {record.get('model')}/{record.get('file')} {record.get('bytes')} bytes"
        elif record["kind"] == "codex-index":
            detail = f"{record.get('n_files')} PDFs -> {record.get('chunks')} chunks"
        else:
            detail = f"{record.get('query', '')!r} -> {record.get('hits')} hits"
        print(f"{record['step']:>3}. {record['kind']:<7} {record['outcome']:<9} {detail}  "
              f"tokens={record.get('completion_tokens')} tok/s={record.get('overall_tps')} "
              f"total={record.get('total_s', record.get('elapsed_s'))} s")
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
    if args.action == "index":
        return _codex_index(settings, args)
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


def _codex_index(settings, args) -> int:
    log = RunLog(settings.runs_dir, task=f"codex index of {settings.codex_dir}")
    try:
        record = build_index(settings, log, force=args.force)
    except ValueError as exc:
        print(f"blocked: {exc}")
        return 1
    if record.get("error"):
        print(f"index failed: {record['error']}\nIs the embedding model loaded? "
              f"lms load {settings.research.embed_model}")
        return 1
    state = "already up to date" if record.get("current") else f"built in {record.get('elapsed_s')} s"
    print(f"Codex index {state}: {len(record.get('files', []))} PDFs, {record.get('chunks')} chunks\n"
          f"Run log: {log.path}")
    return 0


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
    if result.summary and args.repair:
        return _repair(settings, result.run_dir, log, tier=None)
    if result.summary and args.verify:
        return _verify(settings, result.run_dir, log, tier=None, private=True)
    return 0 if result.summary and not result.error else 1


def cmd_verify(settings, args) -> int:
    run_dir = Path(args.run) if Path(args.run).is_dir() else settings.runs_dir / args.run
    if not (run_dir / "report.md").exists():
        print(f"no report.md in {run_dir}")
        return 1
    log = RunLog(settings.runs_dir, task=f"verify: {run_dir.name}")
    return _verify(settings, run_dir, log, tier=args.tier, private=not args.public,
                   reload_every=args.reload_every)


def _verify(settings, run_dir: Path, log: RunLog, *, tier, private: bool, reload_every: int = 0) -> int:
    gateway = Gateway(settings, log)
    decider = Decider(settings, gateway, log, tier=tier) if private else None   # decisions are local only
    try:
        result = verify_run(run_dir, gateway, log, tier=tier, private=private, reload_every=reload_every,
                            reloader=lambda: reload_model(settings, tier), decider=decider)
    finally:
        if decider:
            decider.close()
        gateway.close()
    print((run_dir / "verification.md").read_text(encoding="utf-8"))
    summary = result.summary()
    print(f"Saved: {run_dir / 'verification.json'}")
    return 0 if not (summary["uncited"] or summary["unsupported"] or summary["dangling_citations"]) else 1


def cmd_repair(settings, args) -> int:
    run_dir = Path(args.run) if Path(args.run).is_dir() else settings.runs_dir / args.run
    if not (run_dir / "report.md").exists():
        print(f"no report.md in {run_dir}")
        return 1
    log = RunLog(settings.runs_dir, task=f"repair: {run_dir.name}")
    return _repair(settings, run_dir, log, tier=args.tier, reload_every=args.reload_every)


def _repair(settings, run_dir: Path, log: RunLog, *, tier, reload_every: int = 0) -> int:
    gateway = Gateway(settings, log)
    decider = Decider(settings, gateway, log, tier=tier)
    try:
        result = repair_run(run_dir, gateway, log, tier=tier, reload_every=reload_every,
                            reloader=lambda: reload_model(settings, tier), decider=decider)
    except RepairError as exc:
        print(f"repair stopped, report unchanged: {exc}")
        return 1
    finally:
        decider.close()
        gateway.close()
    print((run_dir / "repair.md").read_text(encoding="utf-8"))
    print(f"Saved: {run_dir / 'report.md'} (original: report.original.md)")
    return 0 if not (result.after["uncited"] or result.after["unsupported"] or result.after["partial"]
                     or result.after["dangling_citations"]) else 1


def cmd_eval(settings, args) -> int:
    questions = load_questions(args.questions)
    if args.only:
        questions = [q for q in questions if q.id in args.only]
    out_dir, rows = run_eval(settings, questions, args.mode or list(MODES), verify_tier=args.verify_tier,
                              reload=not args.no_reload, runs=args.runs)
    print((out_dir / "summary.md").read_text(encoding="utf-8"))
    print(f"Saved: {out_dir / 'summary.csv'}")
    return 0 if rows and all(r.get("gate") == "pass" for r in rows) else 1


def cmd_decide(settings, args) -> int:
    tiers = args.tier or [settings.default_tier]
    log = RunLog(settings.runs_dir, task=f"decide {args.action}: {', '.join(tiers)}")
    gateway = Gateway(settings, log)
    try:
        if args.action == "probe":
            return _probe(settings, gateway, log, tiers)
        cases = decide_check.load_cases(args.cases)
        if args.only:
            cases = [c for c in cases if c.id in args.only]
        results = []
        for tier in tiers:
            decider = Decider(settings, gateway, log, tier=tier, method=args.method, debias=args.debias)
            try:
                print(f"{tier}: {len(cases)} cases ...", flush=True)
                results.append(decide_check.run_check(decider, cases, tier))
            finally:
                decider.close()
    except PrivacyError as exc:
        print(f"refused: {exc}")
        return 1
    finally:
        gateway.close()
    out = settings.runs_dir / f"decide-check-{log.run_id}.json"
    decide_check.save(results, out)
    print(decide_check.check_markdown(results))
    print(f"Saved: {out} and {out.with_suffix('.md')}")
    return 0


def _probe(settings, gateway, log, tiers) -> int:
    question = noul("The sky in the state is green.")
    state = "The sky is blue today."
    for tier in tiers:
        for method in METHODS[1:]:
            decider = Decider(settings, gateway, log, tier=tier, method=method)
            try:
                d = decider.ask(state, question, purpose=f"probe:{method}")
            finally:
                decider.close()
            status = "FAILED " + d.error if d.error else (
                f"answer {d.answer}, p(true)={d.p_true:.3f}, mass={d.mass:.2f}, {d.latency_s} s")
            print(f"{tier:<14} {method:<10} {status}\n{'':<25} first tokens: {d.raw[:120]}")
    return 0


def cmd_vault(settings, args) -> int:
    root = settings.vault_dir
    try:
        if args.action == "list":
            models = vault.read_manifest(root)["models"]
            for name, entry in sorted(models.items()):
                size = sum(f["bytes"] for f in entry["files"].values()) / 1e9
                where = entry.get("revision", "")[:12] or "import"
                print(f"{name:<52} {size:>7.2f} GB  {entry['license']:<14} {where}  {entry.get('why', '')}")
            print(f"\n{len(models)} models in {root}")
            return 0
        if args.action == "scan":
            if not args.names:
                print("vault scan needs a file")
                return 1
            path = Path(args.names[0])
            kind = vault.classify(path.name)
            problem = "refused file type (pickle, code or unknown)" if kind == "refused" else vault.scan_file(path)
            print(f"{'REFUSED' if problem else 'ok'}: {path}" + (f" ({problem})" if problem else ""))
            return 1 if problem else 0
        if args.action == "verify":
            report = vault.verify(root, names=args.names or None)
            bad = 0
            for name, checks in report.items():
                problems = [c for c in checks if c.outcome != "ok"]
                bad += len(problems)
                print(f"{'ok' if not problems else 'PROBLEM':<8} {name} ({len(checks)} files)")
                for check in problems:
                    print(f"         {check.outcome:<11} {check.path}  {check.detail}")
            print(f"\n{len(report)} models checked, {bad} problems")
            return 1 if bad else 0
        picks = vault.load_picks(args.picks)
        if args.action == "plan":
            rows = vault.plan(root, picks)
            for name, state in rows:
                print(f"{state:<11} {name}")
            if not rows:
                print(f"Nothing picked yet. Add [[model]] entries to {args.picks} after benchmarking.")
            if any(state == "not picked" for _, state in rows):
                print("\n'not picked' models stay until you delete them; the vault never deletes.")
            return 0
        log = RunLog(settings.runs_dir, task=f"vault sync into {root}")
        results = vault.sync(root, picks, log, only=args.names or None)
    except vault.VaultError as exc:
        print(f"vault: {exc}")
        return 1
    for result in results:
        state = "stored" if result.ok else "NOT stored (fix the problems below and rerun)"
        print(f"{result.name}@{result.revision[:12] or 'import'} license={result.license}: {state}")
        for check in result.files:
            print(f"    {check.outcome:<14} {check.path}  {check.detail}")
    if not results:
        print("Every pick is already stored.")
    else:
        print(f"Run log: {log.path}")
    return 0 if all(r.ok for r in results) else 1


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
    codex = sub.add_parser("codex", help="list, download or index the papers and books in codex/catalog.toml")
    codex.add_argument("action", choices=("list", "fetch", "index"))
    codex.add_argument("--catalog", default="codex/catalog.toml")
    codex.add_argument("--only", nargs="+", metavar="ID", help="fetch just these catalog ids")
    codex.add_argument("--force", action="store_true", help="rebuild the index even if it is up to date")
    research = sub.add_parser("research", help="a cited report from Local Deep Research, via the gateway")
    research.add_argument("question")
    research.add_argument("-t", "--tier", default=None, help="model tier for LDR (default: the default tier)")
    research.add_argument("--search", choices=SEARCH_TOOLS, default="searxng")
    research.add_argument("--public", action="store_true",
                          help="the question holds nothing private, so cloud tiers and Tavily may see it")
    research.add_argument("--report", action="store_true", help="a long sectioned report instead of a summary")
    research.add_argument("--verify", action="store_true", help="check the report's citations when it is done")
    research.add_argument("--repair", action="store_true", help="check the citations, then fix or remove failed claims")
    check = sub.add_parser("verify", help="check a research report's citations against its sources")
    check.add_argument("run", help="research run id (a folder in runs/) or path to the folder")
    check.add_argument("-t", "--tier", default=None, help="model tier for the support check (default: local)")
    check.add_argument("--public", action="store_true", help="the report and its sources may go to a cloud tier")
    check.add_argument("--reload-every", type=int, default=0, metavar="N",
                       help="reload the local model in LM Studio every N model calls (long reports)")
    mend = sub.add_parser("repair", help="fix or remove the claims in a report that fail the citation check")
    mend.add_argument("run", help="research run id (a folder in runs/) or path to the folder")
    mend.add_argument("-t", "--tier", default=None, help="local model tier for the repair (default: local)")
    mend.add_argument("--reload-every", type=int, default=0, metavar="N",
                      help="reload the local model in LM Studio every N model calls (long reports)")
    evaluate = sub.add_parser("eval", help="run the research eval: each question researched, verified and repaired")
    evaluate.add_argument("--questions", default="evals/research_questions.toml")
    evaluate.add_argument("--mode", action="append", choices=list(MODES), help="repeat; default: all modes")
    evaluate.add_argument("--only", nargs="+", metavar="ID", help="just these question ids")
    evaluate.add_argument("--verify-tier", default=None, help="local tier for the citation check")
    evaluate.add_argument("--runs", type=int, default=2, help="runs per question and mode (default 2)")
    evaluate.add_argument("--no-reload", action="store_true",
                          help="keep the loaded model instead of reloading it before each question")
    dec = sub.add_parser("decide", help="fast local decisions: probe the methods, or score the labelled cases")
    dec.add_argument("action", choices=("probe", "check"))
    dec.add_argument("-t", "--tier", action="append", help="local tier; repeat to compare (default: the default tier)")
    dec.add_argument("--cases", default="evals/decide_cases.toml")
    dec.add_argument("--only", nargs="+", metavar="ID", help="just these case ids")
    dec.add_argument("--method", choices=METHODS, default="auto")
    dec.add_argument("--debias", action="store_true",
                     help="also ask with the options reversed and average, against first-option bias")
    store = sub.add_parser("vault", help="the model vault: verified local copies of the models you picked")
    store.add_argument("action", choices=("plan", "sync", "list", "verify", "scan"))
    store.add_argument("names", nargs="*", help="verify: model names; scan: one file")
    store.add_argument("--picks", default="vault.toml", help="the models chosen for the vault")
    store.add_argument("--only", nargs="+", metavar="NAME", help="sync: just these picks")
    args = parser.parse_args(argv)
    if args.command == "vault" and args.action == "sync":
        args.names = args.only or []

    settings = load_settings(args.config)
    handler = {"tiers": cmd_tiers, "ask": cmd_ask, "search": cmd_search, "show": cmd_show,
               "proxy": cmd_proxy, "codex": cmd_codex,
               "research": cmd_research, "verify": cmd_verify, "repair": cmd_repair,
               "eval": cmd_eval, "decide": cmd_decide, "vault": cmd_vault}[args.command]
    return handler(settings, args)


if __name__ == "__main__":
    sys.exit(main())
