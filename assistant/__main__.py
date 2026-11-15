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
                             [--no-tavily] [--mode offline]   fully local: no keyed search; Kiwix copies only
    python -m assistant eval --bench evals/bench_v1.toml --candidate gemma-e4b --split dev|test
                                                            a research bench run of one candidate; the test
                                                            split is capped and logged in results/bench_ledger.csv
    python -m assistant rescore runs/eval-<id>               score a finished eval again from its saved reports
    python -m assistant referee runs/eval-<id> --bench evals/bench_v1.toml
                                                            judge an eval's reports with the pinned referee
    python -m assistant referee calibrate sample|spot|agree|run ...
                                                            the referee's calibration set: sample, spot-check, compare, score
    python -m assistant claims sample runs/eval-<id> ...    sample checker verdicts to label by hand
    python -m assistant claims score results/claim_sample.csv   false-pass and false-removal rates from the labels
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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import httpx

from .codex import CatalogError, build_index, fetch, load_catalog, read_manifest
from .config import load_settings
from .decide import METHODS, Decider, noul
from . import decide_check
from . import calibration, claim_sample
from .evaluate import MODES, RUNS, load_questions, reload_model, rescore, run_eval
from .gateway import Gateway, PrivacyError, TierNotReady
from .proxy import Proxy, make_server
from .research import SEARCH_TOOLS, run_research
from .runlog import RunLog, new_run_id, read_run
from .search import SearchError, search
from .repair import RepairError, repair_run
from .bench_spec import REFEREE_CAVEAT, BenchSpecError, candidate_key, check_referee_pin, load_bench
from .ledger import DEFAULT_LEDGER, RetestLimit, check_attempt, finish_attempt, record_attempt
from .manifest import REPO_ROOT, _git, sha256_file
from .referee import PROMPT_VERSION, score_eval
from .verify import CAVEAT, failures, verify_run
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
        hint = ("Is llama-swap running? scripts/start_llama_swap.ps1" if settings.model_server == "llama-swap"
                else f"Is the embedding model loaded? lms load {settings.research.embed_model}")
        print(f"index failed: {record['error']}\n{hint}")
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


def _report_dir(settings, run: str) -> Path | None:
    """A research run folder, from its id or path; None (with a message) when it has no report."""
    run_dir = Path(run) if Path(run).is_dir() else settings.runs_dir / run
    if not (run_dir / "report.md").exists():
        print(f"no report.md in {run_dir}")
        return None
    return run_dir


def cmd_verify(settings, args) -> int:
    run_dir = _report_dir(settings, args.run)
    if run_dir is None:
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
    print(f"Saved: {run_dir / 'verification.json'}")
    return _exit_code(result.summary())


def cmd_repair(settings, args) -> int:
    run_dir = _report_dir(settings, args.run)
    if run_dir is None:
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
    return _exit_code(result.after)


def _exit_code(summary: dict) -> int:
    """0 only when the citation check passes, which shows the checker accepts the report."""
    reasons = failures(summary)
    print(f"Citation check: {'FAIL (' + ', '.join(reasons) + ')' if reasons else 'pass'}. {CAVEAT}")
    return 1 if reasons else 0


def cmd_eval(settings, args) -> int:
    if args.bench:
        try:
            plan = _bench_plan(settings, args)
        except (BenchRefused, BenchSpecError, RetestLimit) as exc:
            print(f"Eval refused: {exc}")
            return 2
        return _bench_eval(settings, args, plan)
    if args.candidate or args.split:
        print("Eval refused: --candidate and --split need --bench (e.g. --bench evals/bench_v1.toml)")
        return 2
    if args.no_tavily:   # the fully local gate: no keyed search service at all
        settings.research.tavily_fallback = False
    questions_path = args.questions or "evals/research_questions.toml"
    questions = load_questions(questions_path)
    if args.only:
        questions = [q for q in questions if q.id in args.only]
    # offline mode runs by default only once Kiwix copies are configured
    modes = args.mode or [m for m in MODES if m != "offline" or settings.search.kiwix_books]
    out_dir, rows = run_eval(settings, questions, modes, verify_tier=args.verify_tier,
                              reload=not args.no_reload, runs=RUNS if args.runs is None else args.runs,
                              questions_path=questions_path, **_snapshot_options(args))
    return _eval_result(out_dir, rows)


class BenchRefused(Exception):
    """A bench run that must not start; the message says why."""


BENCH_MODES = ("local", "hybrid")   # the candidate, and Gemini as a reference row


def _snapshot_options(args) -> dict:
    return {"snapshot_dir": args.record_search or args.replay_search,
            "snapshot_mode": "replay" if args.replay_search else "record"}


def _eval_result(out_dir: Path, rows: list[dict]) -> int:
    print((out_dir / "summary.md").read_text(encoding="utf-8"))
    print(f"Saved: {out_dir / 'summary.csv'}")
    return 0 if rows and all(r.get("gate") == "pass" for r in rows) else 1


def _dirty_paths(porcelain: str) -> list[str]:
    """The repo-relative paths in `git status --porcelain` output ("XY path", or "XY old -> new"
    for a rename). _git strips its output, so the first line may have lost its leading space."""
    paths = []
    for line in porcelain.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            paths.append(parts[1].split(" -> ")[-1].strip('"'))
    return paths


def _repo_path(path: Path) -> str | None:
    """`path` relative to the repo root, as git names it, or None when it is outside the repo."""
    try:
        return Path(path).resolve().relative_to(Path(REPO_ROOT).resolve()).as_posix()
    except ValueError:
        return None


def _check_ledger_committed(ledger: Path, ledger_rel: str | None) -> None:
    """The test split counts attempts in one ledger per repo, results/bench_ledger.csv, as committed:
    tracked, in HEAD, and with every committed row still there (new rows may follow). Otherwise a
    second ledger, an uncommitted one, or a locally truncated one would reset the retest cap."""
    if ledger_rel is None or PurePosixPath(ledger_rel) != PurePosixPath(DEFAULT_LEDGER):
        raise BenchRefused(f"the test split records attempts only in this repo's {DEFAULT_LEDGER}, not {ledger}")
    if not _git("ls-files", "--error-unmatch", ledger_rel) or not _git("rev-parse", "--verify", "--quiet",
                                                                        f"HEAD:{ledger_rel}"):
        raise BenchRefused(f"{ledger_rel} is not committed (git-tracked and in HEAD); commit it first")
    committed = _git("show", f"HEAD:{ledger_rel}").replace("\r\n", "\n")   # _git strips the final newline
    current = ledger.read_text(encoding="utf-8").replace("\r\n", "\n") if ledger.is_file() else ""
    if committed and not (current.rstrip("\n") == committed or current.startswith(committed + "\n")):
        raise BenchRefused(f"{ledger_rel} no longer starts with its committed rows; restore them "
                           f"(git checkout HEAD -- {ledger_rel}) and add new attempts only after them")


def _bench_plan(settings, args) -> dict:
    """Everything a bench run needs, or BenchRefused. The spec sets the questions, runs and Tavily
    setting, so a frozen bench can't be overridden by a flag. The test split also needs a frozen,
    committed spec, the committed ledger, a clean tree at a commit (the candidate key names it), the
    candidate's GGUF, the pinned referee, and room under the retest cap. Cheap checks run first."""
    for flag, value in (("--questions", args.questions), ("--runs", args.runs)):
        if value is not None:
            raise BenchRefused(f"{flag} comes from the bench spec; leave it out with --bench")
    if not args.candidate:
        raise BenchRefused("--bench needs --candidate <name>, one of the spec's [candidates]")
    spec = load_bench(args.bench)
    ref = spec.candidates.get(args.candidate)
    if ref is None:
        raise BenchRefused(f"unknown candidate {args.candidate!r}; {args.bench} has: {', '.join(spec.candidates)}")
    tier = settings.tiers.get(ref.tier)
    if tier is None or tier.kind != "local":
        raise BenchRefused(f"candidate {args.candidate!r} runs on tier {ref.tier!r}, which is not a local tier in "
                           f"{args.config}")
    other = [m for m in args.mode or [] if m not in BENCH_MODES]
    if other:
        raise BenchRefused(f"with --bench only local mode runs, plus hybrid as a reference row; not {', '.join(other)}")
    if spec.search != MODES["local"][1]:
        raise BenchRefused(f"the spec's search {spec.search!r} is not the local mode's {MODES['local'][1]!r}")
    split = args.split or "dev"
    test = split == "test"
    if test:
        changed = [flag for flag, value in (("--only", args.only), ("--verify-tier", args.verify_tier),
                                            ("--no-tavily", args.no_tavily), ("--replay-search", args.replay_search))
                   if value]
        if changed:
            raise BenchRefused("the test split runs the whole spec as written, with the candidate as checker: "
                               f"leave out {', '.join(changed)}")
    if test and not spec.frozen:
        raise BenchRefused(f"the test split needs a frozen bench spec (frozen = true in {args.bench}); "
                           "use --split dev until then")
    files = spec.questions.get(split) or []
    missing = [f for f in files if not Path(f).is_file()]
    if not files or missing:
        raise BenchRefused(f"{split} question files missing: {', '.join(missing) or 'none listed in the spec'}")
    questions = [q for f in files for q in load_questions(f)]
    if args.only:
        questions = [q for q in questions if q.id in args.only]
    ledger, spec_path = Path(args.ledger), Path(args.bench)
    ledger_rel, spec_rel = _repo_path(ledger), _repo_path(spec_path)
    commit = _git("rev-parse", "HEAD")
    # the ledger is the one file a test run is expected to leave changed; it doesn't touch the pipeline
    dirty = [p for p in _dirty_paths(_git("status", "--porcelain", "--untracked-files=no"))
             if (Path(REPO_ROOT) / p).resolve() != ledger.resolve()]
    if test:
        if not commit:
            raise BenchRefused("the test split needs a git commit to name in the candidate key")
        _check_ledger_committed(ledger, ledger_rel)
        if spec_rel is None or not _git("ls-files", "--error-unmatch", spec_rel):
            raise BenchRefused(f"the test split needs the committed bench spec; {spec_path} is not git-tracked "
                               "in this repo")
        if spec_rel in dirty:
            raise BenchRefused(f"the bench spec {spec_rel} has uncommitted changes; a frozen spec changes only "
                               "as a new bench version, committed")
        if dirty:
            raise BenchRefused("the git tree has uncommitted changes (" + ", ".join(dirty[:5]) +
                               "); commit them first: the candidate key names the commit")
    gguf = os.environ.get(ref.gguf_env, "")
    if test and not gguf:
        raise BenchRefused(f"candidate GGUF: environment variable {ref.gguf_env} is not set")
    if test and not Path(gguf).is_file():
        raise BenchRefused(f"candidate GGUF: file from {ref.gguf_env} does not exist: {gguf}")
    if test:
        check_referee_pin(spec, os.environ)
    gguf_sha = sha256_file(Path(gguf)) if gguf and Path(gguf).is_file() else ""
    key = candidate_key(gguf_sha, commit, tier)
    if test:
        check_attempt(ledger, spec.version, gguf_sha, key)
    modes = ["local"] + (["hybrid"] if "hybrid" in (args.mode or []) else [])
    named = lambda path: _repo_path(Path(path)) or Path(path).as_posix()
    bench = {"version": spec.version, "split": split, "candidate": args.candidate, "candidate_key": key,
             "candidate_gguf_sha256": gguf_sha, "tree_clean": bool(commit) and not dirty,
             "question_files": {named(f): sha256_file(Path(f)) for f in files},
             "spec": {"path": named(spec_path), "sha256": sha256_file(spec_path)},
             "ledger": ledger_rel if test else None}
    return {"spec": spec, "tier": ref.tier, "questions": questions, "modes": modes, "test": test,
            "commit": commit, "bench": bench}


def _bench_eval(settings, args, plan: dict) -> int:
    """Run the candidate. On the test split the attempt goes in the ledger before the first
    question, and is marked "error" if the eval raises or any run has no checked report; the
    referee (`assistant referee`) decides passed or missed later."""
    spec, bench, ledger = plan["spec"], plan["bench"], Path(args.ledger)
    settings.research.tavily_fallback = spec.tavily_fallback and not args.no_tavily
    eval_id = new_run_id()
    attempt = f"eval-{eval_id}"
    if plan["test"]:
        record_attempt(ledger, {"bench_version": spec.version, "candidate": bench["candidate"],
                                "candidate_key": bench["candidate_key"], "gguf_sha256": bench["candidate_gguf_sha256"],
                                "git_commit": plan["commit"], "split": "test", "eval_id": attempt,
                                "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    try:
        out_dir, rows = run_eval(settings, plan["questions"], plan["modes"], verify_tier=args.verify_tier,
                                 reload=not args.no_reload, runs=spec.runs, candidate_tier=plan["tier"],
                                 bench=bench, eval_id=eval_id, **_snapshot_options(args))
    except BaseException:   # a crash or Ctrl-C still used up the attempt
        if plan["test"]:
            finish_attempt(ledger, attempt, "error")
        raise
    if plan["test"] and (not rows or any(r.get("outcome") != "ok" for r in rows)):
        finish_attempt(ledger, attempt, "error")
    code = _eval_result(out_dir, rows)
    if plan["test"]:
        print(f"Test attempt {attempt} is in {ledger}; commit that file.")
    return code


def cmd_rescore(settings, args) -> int:
    rows = rescore(Path(args.eval_dir), load_questions(args.questions), settings.runs_dir)
    print((Path(args.eval_dir) / "rescore.md").read_text(encoding="utf-8"))
    return 0 if rows else 1


def cmd_referee(settings, args) -> int:
    """Judge the finished eval's reports with the pinned referee (README: research bench)."""
    if args.words[0] == "calibrate":
        return cmd_calibrate(settings, args)
    eval_dir = Path(args.words[0])
    log = RunLog(settings.runs_dir, task=f"referee {eval_dir.name}")
    gateway = Gateway(settings, log)
    try:
        rows = score_eval(eval_dir, load_bench(args.bench), settings, gateway)
    except BenchSpecError as exc:
        print(f"Referee refused: {exc}")
        return 1
    finally:
        gateway.close()
    print((eval_dir / "referee.md").read_text(encoding="utf-8"))
    print(f"Saved: {eval_dir / 'referee.csv'}")
    incomplete = [r for r in rows if not r.get("complete")]
    if incomplete:
        print(f"{len(incomplete)} of {len(rows)} runs are incomplete: some referee judgements failed.")
    return 0 if rows and not incomplete else 1


def _calibration_report(path: Path, tier: str, spec, result: dict, passed: bool) -> None:
    """results/calibration_v1_<tier>.md: both recalls with intervals, the verdict, and what pins them."""
    sha = os.environ.get(spec.referee.gguf_env, "")
    lines = [f"# Referee calibration: {tier}", "", f"**{'PASS' if passed else 'FAIL'}**", ""]
    for label, minimum in (("supported", spec.referee.min_supported_recall),
                           ("not_supported", spec.referee.min_not_supported_recall)):
        hits, total, interval = result[label]
        rate = f"{hits / total:.3f}" if total else "n/a"
        ci = f"; 95% interval {interval[0]:.3f} to {interval[1]:.3f}" if interval else ""
        lines.append(f"- {label} recall: {hits} of {total} = {rate}{ci}; minimum {minimum} "
                     f"({'pass' if total and hits / total >= minimum else 'fail'})")
    lines += [f"- labelled unclear (excluded): {result['unclear_labels']}", f"- prompt version: {PROMPT_VERSION}"]
    if sha:
        lines.append(f"- referee GGUF sha256: {sha256_file(Path(sha))}")
    lines += ["", REFEREE_CAVEAT]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_calibrate(settings, args) -> int:
    """referee calibrate sample|spot|agree|run (README: research bench)."""
    action, rest = (args.words[1] if len(args.words) > 1 else ""), args.words[2:]
    if action not in ("sample", "spot", "agree", "run") or not rest:
        print("Usage: referee calibrate sample <eval dirs> | spot <csv> | agree <claude csv> <van csv> | run <csv>")
        return 2
    try:
        if action == "sample":
            spec = load_bench(args.bench)
            dev_ids = set()
            for path in spec.questions.get("dev", []):
                dev_ids |= {q.id for q in load_questions(path)}
            out = Path(args.out or "results/calibration_v1.csv")
            rows = calibration.sample_pairs([Path(p) for p in rest], out, dev_ids, runs_dir=settings.runs_dir)
            print(f"Sampled {len(rows)} pairs into {out}. Fill `label` (supported, not_supported, unclear) and `labeller`.")
            return 0 if rows else 1
        if action == "spot":
            out = Path(args.out or "results/calibration_v1_spot.csv")
            rows = calibration.spot_check(Path(rest[0]), out)
            print(f"Wrote {len(rows)} pairs with labels blanked to {out}, for Van to label.")
            return 0 if rows else 1
        if action == "agree":
            if len(rest) != 2:
                print("Name two CSVs: Claude's labels, then Van's")
                return 2
            agreeing, compared = calibration.agreement(Path(rest[0]), Path(rest[1]))
            print(f"Van disagrees on {compared - agreeing} of {compared}")
            return 1 if compared - agreeing > 3 else 0
        spec = load_bench(args.bench)
        tier = args.tier or spec.referee.tier
        log = RunLog(settings.runs_dir, task=f"referee calibrate {tier}")
        gateway = Gateway(settings, log)
        try:
            rows = calibration.judge_pairs(Path(rest[0]), settings.runs_dir, gateway, tier)
        finally:
            gateway.close()
    except (BenchSpecError, ValueError, OSError) as exc:
        print(f"Calibration refused: {exc}")
        return 1
    result = calibration.recall(rows)
    passed = calibration.meets(result, spec)
    report = Path("results") / f"calibration_v1_{tier}.md"
    _calibration_report(report, tier, spec, result, passed)
    for label in ("supported", "not_supported"):
        hits, total, interval = result[label]
        print(f"{label} recall: {hits} of {total}" + (f" (95% interval {interval[0]:.2f} to {interval[1]:.2f})" if interval else ""))
    print("PASS" if passed else "FAIL", f"against the spec minimums. Saved: {report}")
    print(REFEREE_CAVEAT)
    return 0 if passed else 1


def cmd_claims(settings, args) -> int:
    if args.action == "sample":
        if not args.paths:
            print("Name at least one eval folder, e.g. runs/eval-20261115-...")
            return 2
        out = Path(args.out)
        rows = claim_sample.sample([Path(p) for p in args.paths], out, args.passed, args.removed, args.seed,
                                   settings.runs_dir)
        print(f"Sampled {sum(r['group'] == 'passed' for r in rows)} passed and "
              f"{sum(r['group'] == 'removed' for r in rows)} removed claims into {out}. "
              "Fill `label` (true, false or unclear) and `labeller`, then run: python -m assistant claims score " + str(out))
        return 0 if rows else 1
    if len(args.paths) != 1:
        print("Name the one labelled CSV to score")
        return 2
    print(claim_sample.score_markdown(claim_sample.score(Path(args.paths[0]))))
    return 0


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
    for stream in (sys.stdout, sys.stderr):   # Windows consoles are cp1252; an emoji in a reply must not crash
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
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
    evaluate.add_argument("--questions", default=None, help="default: evals/research_questions.toml")
    evaluate.add_argument("--mode", action="append", choices=list(MODES), help="repeat; default: all modes")
    evaluate.add_argument("--only", nargs="+", metavar="ID", help="just these question ids")
    evaluate.add_argument("--verify-tier", default=None, help="local tier for the citation check")
    evaluate.add_argument("--runs", type=int, default=None, help="runs per question and mode (default 2)")
    evaluate.add_argument("--no-tavily", action="store_true",
                          help="never fall back to Tavily, even for these public questions")
    evaluate.add_argument("--no-reload", action="store_true",
                          help="keep the loaded model instead of reloading it before each question")
    evaluate.add_argument("--bench", default=None, metavar="SPEC",
                          help="run a research bench candidate; questions, runs and Tavily come from the spec")
    evaluate.add_argument("--candidate", default=None, help="with --bench: a name from the spec's [candidates]")
    evaluate.add_argument("--split", choices=("dev", "test"), default=None,
                          help="with --bench: dev (default; free, not logged) or test (frozen spec, capped, logged)")
    evaluate.add_argument("--ledger", default=DEFAULT_LEDGER,
                          help="with --bench: the committed record of test-split attempts")
    again = sub.add_parser("rescore", help="score a finished eval again from its saved reports (no model calls)")
    again.add_argument("eval_dir", help="the eval's folder, e.g. runs/eval-20261114-...")
    again.add_argument("--questions", default="evals/research_questions.toml")
    ref = sub.add_parser("referee", help="judge a finished eval's reports with the pinned bench referee")
    ref.add_argument("words", nargs="+", help="an eval folder, e.g. runs/eval-20261115-..., or: calibrate "
                     "sample|spot|agree|run ...")
    ref.add_argument("--bench", default="evals/bench_v1.toml")
    ref.add_argument("--out", help="calibrate sample|spot: the CSV to write")
    ref.add_argument("--tier", help="calibrate run: the tier to judge with (default: the spec's referee tier)")
    snap = evaluate.add_mutually_exclusive_group()
    snap.add_argument("--record-search", metavar="DIR", help="save the page searches of local and offline modes")
    snap.add_argument("--replay-search", metavar="DIR", help="answer the page searches from a recording, offline")
    claims = sub.add_parser("claims", help="sample the checker's verdicts for labelling, or score the labels")
    claims.add_argument("action", choices=["sample", "score"])
    claims.add_argument("paths", nargs="*", help="sample: eval folders; score: the labelled CSV")
    claims.add_argument("--out", default="results/claim_sample.csv")
    claims.add_argument("--passed", type=int, default=40, help="passed claims to sample (default 40)")
    claims.add_argument("--removed", type=int, default=20, help="removed claims to sample (default 20)")
    claims.add_argument("--seed", type=int, default=20261115)
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
               "eval": cmd_eval, "rescore": cmd_rescore, "referee": cmd_referee, "claims": cmd_claims, "decide": cmd_decide, "vault": cmd_vault}[args.command]
    return handler(settings, args)


if __name__ == "__main__":
    sys.exit(main())
