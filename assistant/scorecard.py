"""The research bench scorecard (spec sections 6.4 to 6.9): fit, quality, ranking, and the pass line.

One card per bench eval: the candidate's fit failures (assistant/fit.py), and its quality from the
referee (referee.csv) and the pipeline (summary.csv), per question first:

- facts right: the share of the question's expected facts found in referee-supported statements.
  A run that ended without a report scores 0, so failing can't raise the score.
- errors through: the share of the report's statements the referee marks not_supported. A run
  without a report has none (None), and counts under the fit check instead.
- minutes: research + verify + repair. With several runs of a question, its runs are averaged.

Ranking: fit failures are listed last and not ranked. Among the rest, A ranks above B on facts when
the paired bootstrap interval (by question, 95%) of A - B lies wholly above 0; when it holds 0 they
are tied on facts, and errors through decides the same way (lower wins); when both are tied, the
lower median minutes per report wins. Only local-mode rows are candidates; a hybrid row (Gemini
writes, the candidate checks) is shown as a reference, not ranked.

The scorecard refuses to mix bench versions or splits, and refuses a run the referee hasn't fully
judged. On the test split it also refuses an eval whose tree wasn't clean, that has no referee.csv,
or whose attempt isn't in the committed ledger at HEAD; the caller then marks each attempt passed or
missed, so the result is written only for an attempt that can no longer be quietly undone.
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import statistics
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path

from .bench_spec import REFEREE_CAVEAT, BenchSpec, PassLine
from .fit import _number, fit_failures, report_seconds

BOOTSTRAP_N = 10000
SEED = 20261115
LOCAL = "local"


class ScorecardRefused(Exception):
    """These evals can't go on one scorecard; the message says why."""


@dataclass
class Card:
    candidate: str
    candidate_key: str
    eval_id: str
    bench_version: int
    fit: list[str]
    facts_right: float | None
    facts_ci: tuple[float, float] | None
    errors_through: float | None
    errors_ci: tuple[float, float] | None
    median_minutes: float | None
    per_question: dict[str, dict]
    flags: list[str]
    passes: bool
    split: str = ""
    mode: str = LOCAL
    gguf_sha256: str = ""
    gguf_file: str = ""


# --- reading an eval ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _manifest(eval_dir: Path) -> dict:
    path = Path(eval_dir) / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _referee_rows(eval_dir: Path) -> dict[str, dict] | None:
    path = Path(eval_dir) / "referee.csv"
    return {r["run_id"]: r for r in _read_csv(path)} if path.is_file() else None


def eval_rows(eval_dir: Path, mode: str = LOCAL) -> list[dict]:
    """summary.csv rows of one mode. In local mode, a (question, run) the manifest planned but
    summary.csv lacks (an eval that stopped early) is added as a failed run, outcome "missing", so
    an eval cut short can't score on the questions it happened to finish."""
    rows = [r for r in _read_csv(Path(eval_dir) / "summary.csv") if r.get("mode") == mode]
    if mode != LOCAL:
        return rows
    manifest = _manifest(eval_dir)
    have = {(r["question"], str(r.get("run") or 1)) for r in rows}
    for question in manifest.get("questions", []):
        for run in range(1, int(manifest.get("runs") or 1) + 1):
            if (question, str(run)) not in have:
                rows.append({"question": question, "mode": mode, "run": run, "outcome": "missing", "run_id": ""})
    return rows


def _mean(values) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def per_question(eval_dir: Path, mode: str = LOCAL) -> dict[str, dict]:
    """question -> {facts_right, errors_through, minutes}, each the mean over the question's runs.
    A failed run gives facts_right 0.0, errors_through None and the minutes of what ran. An ok run
    with no referee row raises ScorecardRefused."""
    referee = _referee_rows(eval_dir) or {}
    runs: dict[str, list[tuple]] = {}
    for row in eval_rows(eval_dir, mode):
        if row.get("outcome") == "ok":
            judged = referee.get(row["run_id"])
            if judged is None:
                raise ScorecardRefused(f"{Path(eval_dir).name}: run {row['run_id']} has no referee score")
            scores = (_number(judged.get("facts_right")), _number(judged.get("errors_through")))
        else:
            scores = (0.0, None)
        runs.setdefault(row["question"], []).append((*scores, report_seconds(row) / 60))
    return {question: {"facts_right": _mean(f for f, _, _ in found), "errors_through": _mean(e for _, e, _ in found),
                       "minutes": _mean(m for _, _, m in found)}
            for question, found in runs.items()}


# --- the bootstrap -----------------------------------------------------------------------------

def _quantile(ordered: list[float], q: float) -> float:
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_mean(values: list[float], n: int = BOOTSTRAP_N, seed: int = SEED) -> tuple[float, float]:
    """95% percentile interval of the mean of `values`, resampled n times with a fixed seed."""
    rng = random.Random(seed)
    k = len(values)
    means = sorted(sum(rng.choices(values, k=k)) / k for _ in range(n))
    return _quantile(means, 0.025), _quantile(means, 0.975)


def paired_bootstrap(a: dict[str, float], b: dict[str, float], n: int = BOOTSTRAP_N,
                     seed: int = SEED) -> tuple[float, float, float]:
    """Mean of a - b over the questions both have (None skipped), and its 95% percentile interval.
    ValueError when there is nothing to pair: two cards on different questions aren't compared."""
    shared = [q for q in a if q in b]
    if not shared:
        raise ValueError("no shared questions to compare")
    diffs = [a[q] - b[q] for q in shared if a[q] is not None and b[q] is not None]
    if not diffs:
        raise ValueError("no shared question has a score on both sides")
    low, high = bootstrap_mean(diffs, n, seed)
    return sum(diffs) / len(diffs), low, high


def _ci(per_q: dict[str, dict], key: str) -> tuple[float, float] | None:
    values = [v[key] for v in per_q.values() if v[key] is not None]
    return bootstrap_mean(values) if values else None


# --- ranking -----------------------------------------------------------------------------------

def _passes(card: Card, line: PassLine) -> bool:
    return (not card.fit and card.facts_right is not None and card.errors_through is not None
            and card.facts_right >= line.min_facts_right and card.errors_through <= line.max_errors_through)


def _interval(a: Card, b: Card, key: str) -> tuple[float, float] | None:
    """The paired interval of a - b on one measure, or None when no question has both scores."""
    try:
        _, low, high = paired_bootstrap({q: v[key] for q, v in a.per_question.items()},
                                        {q: v[key] for q, v in b.per_question.items()})
    except ValueError:
        return None
    return low, high


def _compare(a: Card, b: Card) -> int:
    facts = _interval(a, b, "facts_right")
    if facts and facts[0] > 0:
        return -1
    if facts and facts[1] < 0:
        return 1
    errors = _interval(a, b, "errors_through")
    if errors and errors[1] < 0:   # a lets wholly fewer errors through
        return -1
    if errors and errors[0] > 0:
        return 1
    slow = float("inf")   # no minutes at all sorts after any known time
    a_min = slow if a.median_minutes is None else a.median_minutes
    b_min = slow if b.median_minutes is None else b.median_minutes
    return (a_min > b_min) - (a_min < b_min)


def rank(cards: list[Card], pass_line: PassLine) -> list[Card]:
    """Fit passes in rank order, then fit failures as given; sets each card's `passes`."""
    for card in cards:
        card.passes = _passes(card, pass_line)
    fits = [c for c in cards if not c.fit]
    for i, a in enumerate(fits):
        for b in fits[i + 1:]:
            if not set(a.per_question) & set(b.per_question):
                raise ScorecardRefused(f"{a.eval_id} and {b.eval_id} share no questions, so they can't be compared")
    return sorted(fits, key=cmp_to_key(_compare)) + [c for c in cards if c.fit]


# --- building cards ----------------------------------------------------------------------------

def _first_token(path: str) -> str:
    name = re.split(r"[\\/]", path or "")[-1].lower().removesuffix(".gguf")
    return re.split(r"[-_]", name)[0]


def referee_family(candidate_file: str, referee_file: str) -> bool:
    """Same model family by file name: the first token (split on - or _), e.g. gemma or qwen3.5."""
    token = _first_token(candidate_file)
    return bool(token) and token == _first_token(referee_file)


def _gguf_file(manifest: dict, env_name: str, env) -> str:
    entry = manifest.get("gguf", {}).get(env_name) or {}
    return entry.get("path") or (env.get(env_name, "") if env_name else "")


def _referee_problems(eval_dir: Path, spec: BenchSpec, test: bool) -> list[str]:
    name = Path(eval_dir).name
    referee = _referee_rows(eval_dir)
    if referee is None and test:
        return [f"{name}: no referee.csv; run `assistant referee {name}` first"]
    referee = referee or {}
    problems = []
    rows = [r for r in _read_csv(Path(eval_dir) / "summary.csv") if r.get("outcome") == "ok"]
    unjudged = [r["run_id"] for r in rows if r["run_id"] not in referee or referee[r["run_id"]].get("complete") != "True"]
    if unjudged:
        problems.append(f"{name}: runs not fully judged by the referee: {', '.join(unjudged)}; "
                        f"re-run `assistant referee {name}` until it succeeds")
    versions = sorted({r.get("prompt_version", "") for r in referee.values()} - {str(spec.referee.prompt_version)})
    if versions:
        problems.append(f"{name}: referee prompt version {', '.join(versions)}, not the spec's "
                        f"{spec.referee.prompt_version}")
    if spec.referee.sha256 and any(r.get("referee_sha256") != spec.referee.sha256 for r in referee.values()):
        problems.append(f"{name}: judged by another referee file than the spec pins")
    return problems


def _committed_eval_ids(committed_ledger: str | None) -> set[str]:
    if not committed_ledger:
        return set()
    return {r.get("eval_id", "") for r in csv.DictReader(io.StringIO(committed_ledger))}


def _card(eval_dir: Path, manifest: dict, spec: BenchSpec, mode: str, env) -> Card:
    bench = manifest["bench"]
    per_q = per_question(eval_dir, mode)
    candidate = bench.get("candidate", "")
    flags, fit = [], []
    ref = spec.candidates.get(candidate)
    gguf = _gguf_file(manifest, ref.gguf_env if ref else "", env)
    if mode == LOCAL:
        offload = bench.get("offload")
        fit = fit_failures(eval_rows(eval_dir, mode), spec.fit, tuple(offload) if offload else None)
        if not offload:
            flags.append("gpu?")
        if referee_family(gguf, _gguf_file(manifest, spec.referee.gguf_env, env)):
            flags.append("referee family")
    else:
        flags.append(f"{mode} reference: another writer, checked by {candidate}")
    minutes = [v["minutes"] for v in per_q.values() if v["minutes"] is not None]
    card = Card(candidate=candidate, candidate_key=bench.get("candidate_key", ""), eval_id=Path(eval_dir).name,
                bench_version=bench.get("version"), fit=fit,
                facts_right=_mean(v["facts_right"] for v in per_q.values()), facts_ci=_ci(per_q, "facts_right"),
                errors_through=_mean(v["errors_through"] for v in per_q.values()),
                errors_ci=_ci(per_q, "errors_through"),
                median_minutes=statistics.median(minutes) if minutes else None, per_question=per_q, flags=flags,
                passes=False, split=bench.get("split", ""), mode=mode,
                gguf_sha256=bench.get("candidate_gguf_sha256", ""), gguf_file=gguf)
    card.passes = mode == LOCAL and _passes(card, spec.pass_line)
    return card


def build_cards(eval_dirs: list[Path], spec: BenchSpec, *, env=os.environ, committed_ledger: str | None = None,
                spec_sha256: str | None = None) -> tuple[list[Card], list[Card]]:
    """(candidate cards, hybrid reference cards) for evals of one bench version and split, or
    ScorecardRefused naming every problem. committed_ledger: the ledger's text at HEAD, needed on
    the test split; spec_sha256: the spec file's hash, which a test-split eval must have run with."""
    eval_dirs = [Path(d) for d in eval_dirs]
    names = [d.name for d in eval_dirs]
    if len(set(names)) != len(names):
        raise ScorecardRefused("the same eval is named twice")
    manifests = {}
    for eval_dir in eval_dirs:
        manifest = _manifest(eval_dir)
        if not isinstance(manifest.get("bench"), dict):
            raise ScorecardRefused(f"{eval_dir.name} is not a bench run (no bench block in its manifest.json)")
        manifests[eval_dir] = manifest
    versions = {m["bench"].get("version") for m in manifests.values()}
    if len(versions) > 1:
        raise ScorecardRefused(f"the evals are on bench versions {', '.join(map(str, sorted(versions, key=str)))}; "
                               "one scorecard holds one bench version")
    if versions != {spec.version}:
        raise ScorecardRefused(f"the evals are bench version {versions.pop()}, the spec is version {spec.version}")
    splits = {m["bench"].get("split") for m in manifests.values()}
    if len(splits) > 1:
        raise ScorecardRefused(f"the evals mix the {' and '.join(sorted(map(str, splits)))} splits; "
                               "one scorecard holds one split")
    test = splits == {"test"}
    committed = _committed_eval_ids(committed_ledger)
    problems = []
    for eval_dir, manifest in manifests.items():
        bench, name = manifest["bench"], eval_dir.name
        if test:
            if bench.get("tree_clean") is not True:
                problems.append(f"{name}: the git tree was not clean when it ran (tree_clean is not true)")
            if spec_sha256 and (bench.get("spec") or {}).get("sha256") != spec_sha256:
                problems.append(f"{name}: it ran with another bench spec file than {spec_sha256[:12]}")
            if name not in committed:
                problems.append(f"{name}: its attempt is not in the committed ledger at HEAD; commit "
                                "results/bench_ledger.csv first")
        problems += _referee_problems(eval_dir, spec, test)
    if problems:
        raise ScorecardRefused("\n".join(problems))
    cards, reference = [], []
    for eval_dir, manifest in manifests.items():
        cards.append(_card(eval_dir, manifest, spec, LOCAL, env))
        other = {r.get("mode") for r in _read_csv(eval_dir / "summary.csv")} - {LOCAL}
        reference += [_card(eval_dir, manifest, spec, mode, env) for mode in sorted(other)]
    return cards, reference


# --- output ------------------------------------------------------------------------------------

def _pct(value) -> str:
    """One decimal, so 4.6% errors through doesn't print as the 5% line."""
    return "-" if value is None else f"{value:.1%}"


def _with_ci(value, ci) -> str:
    return _pct(value) + (f" ({_pct(ci[0])} to {_pct(ci[1])})" if ci else "")


def _fit_cell(card: Card) -> str:
    if card.mode != LOCAL:
        return "-"
    cell = "fail: " + ", ".join(card.fit) if card.fit else "pass"
    return cell + (", gpu?" if "gpu?" in card.flags else "")


def _minutes(value) -> str:
    return "-" if value is None else f"{value:.1f}"


def _ranks(cards: list[Card]) -> list[str]:
    numbers, n = [], 0
    for card in cards:
        if card.fit:
            numbers.append("")
        else:
            n += 1
            numbers.append(str(n))
    return numbers


def _calibration_lines(spec: BenchSpec, calibration_md: str | None) -> list[str]:
    lines = ["## Referee calibration", "",
             f"Minimums: supported recall {spec.referee.min_supported_recall}, not_supported recall "
             f"{spec.referee.min_not_supported_recall}.", ""]
    if not calibration_md:
        return lines + ["Not calibrated yet: no calibration report for the referee "
                        f"(results/calibration_v{spec.version}_{spec.referee.tier}.md)."]
    verdict = [line for line in calibration_md.splitlines() if line.startswith("**")]
    return lines + verdict[:1] + [""] + [line for line in calibration_md.splitlines() if line.startswith("- ")]


def scorecard_markdown(cards: list[Card], spec: BenchSpec, calibration_md: str | None,
                       reference: list[Card] = ()) -> str:
    """cards in rank order (see rank)."""
    split = cards[0].split if cards else ""
    fit, line = spec.fit, spec.pass_line
    lines = [f"# Research bench v{spec.version} scorecard ({split or 'no'} split)", "", REFEREE_CAVEAT, "",
             f"Pass line: facts right at least {line.min_facts_right:.0%} and errors through at most "
             f"{line.max_errors_through:.0%} on the test split, plus fit: fully on the GPU at {fit.context} context, "
             f"free RAM never below {fit.min_free_ram_mb} MB, at most {fit.max_report_minutes:g} minutes per report, "
             f"at most {fit.max_error_share:.0%} of runs without a report. `gpu?` means the offload was not "
             "recorded, so the GPU check is unknown and not counted as a failure.", "",
             "Ranking: fit failures are listed last and not ranked. Then facts right, then errors through, each "
             "compared by a paired bootstrap over questions (95%); when both are tied, the lower median minutes "
             "per report ranks first. A failed run counts as 0 facts right.", ""]
    if split != "test":
        lines += [f"This is the {split or '-'} split: the pass line counts only on the test split.", ""]
    lines += ["| Rank | Candidate | Eval | Candidate key | GGUF sha256 | Fit | Facts right (95% CI) | "
              "Errors through (95% CI) | Median minutes | Flags | Pass line |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for number, card in zip(_ranks(cards), cards):
        flags = ", ".join(f for f in card.flags if f != "gpu?") or "-"
        lines.append(f"| {number or '-'} | {card.candidate} | {card.eval_id} | {card.candidate_key} | "
                     f"{card.gguf_sha256[:12] or '-'} | {_fit_cell(card)} | {_with_ci(card.facts_right, card.facts_ci)} | "
                     f"{_with_ci(card.errors_through, card.errors_ci)} | {_minutes(card.median_minutes)} | {flags} | "
                     f"{'pass' if card.passes else 'miss'} |")
    if reference:
        lines += ["", "## Reference (not ranked)", "",
                  "Rows from another writer in the same eval (hybrid: Gemini writes with Tavily search, the "
                  "candidate checks and repairs), scored by the same referee. Not a candidate.", "",
                  "| Mode | Checked by | Eval | Facts right (95% CI) | Errors through (95% CI) | Median minutes |",
                  "|---|---|---|---|---|---|"]
        for card in reference:
            lines.append(f"| {card.mode} | {card.candidate} | {card.eval_id} | {_with_ci(card.facts_right, card.facts_ci)} | "
                         f"{_with_ci(card.errors_through, card.errors_ci)} | {_minutes(card.median_minutes)} |")
    lines += [""] + _calibration_lines(spec, calibration_md)
    every = list(cards) + list(reference)
    questions = sorted({q for card in every for q in card.per_question})
    if questions:
        lines += ["", "## Per question", "", "Facts right / errors through / minutes, averaged over runs.", "",
                  "| Question | " + " | ".join(f"{c.candidate} ({c.mode}, {c.eval_id})" for c in every) + " |",
                  "|---|" + "---|" * len(every)]
        for q in questions:
            cells = []
            for card in every:
                v = card.per_question.get(q)
                cells.append("-" if v is None else
                             f"{_pct(v['facts_right'])} / {_pct(v['errors_through'])} / {_minutes(v['minutes'])}")
            lines.append(f"| {q} | " + " | ".join(cells) + " |")
    lines += ["", REFEREE_CAVEAT]
    return "\n".join(lines) + "\n"


CSV_FIELDS = ("rank", "candidate", "mode", "candidate_key", "eval_id", "bench_version", "split", "gguf_sha256",
              "gguf_file", "fit", "facts_right", "facts_ci_low", "facts_ci_high", "errors_through", "errors_ci_low",
              "errors_ci_high", "median_minutes", "flags", "passes")


def _csv_row(number: str, card: Card) -> dict:
    ci = lambda interval, i: "" if interval is None else round(interval[i], 4)
    num = lambda v: "" if v is None else round(v, 4)
    return {"rank": number, "candidate": card.candidate, "mode": card.mode, "candidate_key": card.candidate_key,
            "eval_id": card.eval_id, "bench_version": card.bench_version, "split": card.split,
            "gguf_sha256": card.gguf_sha256, "gguf_file": card.gguf_file, "fit": _fit_cell(card),
            "facts_right": num(card.facts_right), "facts_ci_low": ci(card.facts_ci, 0),
            "facts_ci_high": ci(card.facts_ci, 1), "errors_through": num(card.errors_through),
            "errors_ci_low": ci(card.errors_ci, 0), "errors_ci_high": ci(card.errors_ci, 1),
            "median_minutes": num(card.median_minutes), "flags": " | ".join(card.flags), "passes": card.passes}


def write_scorecard(out: Path, cards: list[Card], spec: BenchSpec, *, calibration_md: str | None = None,
                    reference: list[Card] = ()) -> None:
    """out (.md) and the same name with .csv: one row per card, reference rows unranked."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(scorecard_markdown(cards, spec, calibration_md, reference), encoding="utf-8")
    with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows([_csv_row(n, c) for n, c in zip(_ranks(cards), cards)] +
                         [_csv_row("", c) for c in reference])
