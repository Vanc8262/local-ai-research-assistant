"""Command line for the Stage 0 benchmark.

    python -m bench models                      list the models the server reports
    python -m bench run --model <id>            run every suite against one model
    python -m bench run --model <id> --suites tools,code
    python -m bench headroom --model <id>       memory check only
    python -m bench report                      rebuild results/summary.md and .csv
"""
from __future__ import annotations

import argparse
import datetime as dt
import platform
import sys

from . import __version__, sysmon
from .client import ChatClient
from .config import Config, ModelCfg, load_config
from .report import write_report
from .results import RunWriter, slug
from .suites import SUITES
from .suites.base import Ctx, speed_metrics


def make_chat_fn(client: ChatClient, cfg: Config, model: ModelCfg):
    def chat_fn(messages, tools=None, max_tokens=None):
        return client.chat(
            model.id, messages, tools=tools,
            temperature=cfg.temperature if model.temperature is None else model.temperature,
            max_tokens=max_tokens or model.max_tokens or cfg.max_tokens,
            seed=cfg.seed, extra_body=model.extra_body,
        )
    return chat_fn


def cmd_models(cfg: Config, _args) -> int:
    client = ChatClient(cfg.base_url, cfg.api_key, cfg.timeout_s)
    models = client.list_models()
    if not models:
        print(f"No models reported by {cfg.base_url}. Is the LM Studio server running?")
        return 1
    for entry in models:
        details = ", ".join(f"{key}={entry[key]}" for key in
                            ("state", "type", "quantization", "max_context_length",
                             "loaded_context_length") if key in entry)
        print(f"{entry.get('id')}  {details}")
    return 0


def cmd_run(cfg: Config, args) -> int:
    names = [name.strip() for name in args.suites.split(",") if name.strip()]
    unknown = [name for name in names if name not in SUITES]
    if unknown:
        print(f"Unknown suite(s): {', '.join(unknown)}. Choose from: {', '.join(SUITES)}")
        return 2

    model = cfg.model(args.model)
    client = ChatClient(cfg.base_url, cfg.api_key, cfg.timeout_s)
    info = client.model_info(model.id)
    loaded_context = info.get("loaded_context_length")
    if loaded_context and cfg.context_length and loaded_context != cfg.context_length:
        print(f"Warning: the server reports a loaded context of {loaded_context}, "
              f"but bench.toml says {cfg.context_length}.")

    print(f"Checking that {model.id} answers (this also loads the model if needed)...")
    probe = make_chat_fn(client, cfg, model)(
        [{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=600)
    if probe.error:
        print(f"The model did not answer: {probe.error}\n"
              f"Check that the LM Studio server is running at {cfg.base_url} and that the model id "
              "matches `python -m bench models`.")
        return 1

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.results_dir / "_".join(filter(None, [stamp, slug(model.id), slug(args.tag or "")]))
    writer = RunWriter(run_dir)
    writer.write_meta({
        "harness_version": __version__, "started": dt.datetime.now().isoformat(timespec="seconds"),
        "model": model.id, "model_label": model.label, "tag": args.tag,
        "context_length": loaded_context or cfg.context_length, "server_model_info": info,
        "base_url": cfg.base_url, "temperature": cfg.temperature if model.temperature is None else model.temperature,
        "max_tokens": model.max_tokens or cfg.max_tokens, "seed": cfg.seed,
        "extra_body": model.extra_body, "system_suffix": model.system_suffix,
        "suites": names, "limit": args.limit,
        "machine": {"platform": platform.platform(), "python": platform.python_version(),
                    **sysmon.snapshot()},
    })
    ctx = Ctx(cfg=cfg, model=model, run_dir=run_dir, chat_fn=make_chat_fn(client, cfg, model),
              client=client, limit=args.limit)

    print(f"Model: {model.label} ({model.id})  ->  {run_dir}")
    for name in names:
        print(f"\n[{name}]")
        ctx.calls.clear()
        ctx.suite_calls.clear()
        with sysmon.PeakSampler() as sampler:
            result = SUITES[name](ctx)
        if result.skipped:
            print(f"  skipped: {result.skipped}")
        else:
            result.metrics.update(speed_metrics(ctx.suite_calls))
            result.metrics.update(sampler.peak)
            score = "" if result.score is None else f"  score {result.score:.0%}"
            print(f"  -> {result.passed}/{result.total}{score}")
            if result.metrics["call_errors"]:
                print(f"  {result.metrics['call_errors']} of {result.metrics['calls']} requests failed; "
                      "see the suite's .jsonl file.")
        writer.add_suite(result)

    path, _ = write_report(cfg.results_dir)
    print(f"\nRun saved to {run_dir}\nSummary table: {path}")
    if (run_dir / "manual_scores.csv").exists():
        print(f"Manual scoring: read {run_dir / 'manual_review.md'} and fill in "
              f"{run_dir / 'manual_scores.csv'}, then run `python -m bench report`.")
    return 0


def cmd_report(cfg: Config, _args) -> int:
    if not cfg.results_dir.exists():
        print(f"No results folder at {cfg.results_dir} yet.")
        return 1
    path, count = write_report(cfg.results_dir)
    print(f"Wrote {count} rows to {path} and {path.with_suffix('.csv')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="bench.toml", help="settings file (default: bench.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="list the models the server reports").set_defaults(func=cmd_models)

    run = sub.add_parser("run", help="run suites against one model")
    run.add_argument("--model", required=True, help="model id as shown by `python -m bench models`")
    run.add_argument("--suites", default=",".join(SUITES), help=f"comma-separated: {', '.join(SUITES)}")
    run.add_argument("--tag", default="", help="short label for this run, e.g. ctx16k or q5")
    run.add_argument("--limit", type=int, default=None, help="run only the first N items of each suite")
    run.add_argument("--context", type=int, default=None,
                     help="context length the model was loaded with, if not the bench.toml value")
    run.set_defaults(func=cmd_run)

    head = sub.add_parser("headroom", help="memory check only")
    head.add_argument("--model", required=True)
    head.set_defaults(func=cmd_run, suites="headroom", tag="headroom", limit=None, context=None)

    sub.add_parser("report", help="rebuild the summary table").set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    # Model output can contain characters the Windows console cannot print.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    cfg = load_config(args.config)
    if getattr(args, "context", None):
        cfg.context_length = args.context
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
