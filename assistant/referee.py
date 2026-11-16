"""The research bench's referee: a pinned local model judges every sentence of a saved report
against the stored text of the sources it cites (FACT-style scoring).

It reads only what the run folder already holds (sources.json, pages/<index>.txt) and never
fetches or searches. Every call is private, so a private prompt can never reach a cloud tier.

A judgement either stands or is left unjudged. Only a transient failure (a connection error, a
timeout, HTTP 5xx, a server still loading the model) leaves it unjudged: the run is incomplete and
is judged again on the next `assistant referee`. A deterministic failure (no stored text for the
cited source, a reply that is not a verdict after the retry, a quote not in the source, another
gateway error) would fail the same way again, so it is a final "unclear" flagged referee_failed.
The scorecard shows the referee-failed share and refuses a card above [referee] max_referee_failed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .bench_spec import (REFEREE_CAVEAT, BenchSpec, BenchSpecError, check_referee_pin, check_served_model,
                         normal_hash, question_file_problems, served_model_path)
from .config import Settings
from .evaluate import Question, fact_pattern, load_questions, reload_model
from .ledger import committed_eval_ids
from .manifest import REPO_ROOT, sha256_file
from .text import best_passages
from .verify import quote_in_source, split_claims, squash

LABELS = ("supported", "not_supported", "unclear")
PROMPT_VERSION = 1
PASSAGE_COUNT = 6
SEED = 20261115
MAX_TOKENS = 4000
TEMPERATURE = 0

PROMPT = """You are the referee of a research report. Judge one sentence of the report against
the passages of the one source it cites.

Sentence: {statement}

Passages from source [{index}] ({title}):
{passages}

Think it through, then end your reply with one JSON object and nothing after it:
{{"verdict": "supported" | "not_supported" | "unclear", "quote": "<the exact sentence from the passages that supports it, or empty>", "reason": "<one short sentence>"}}
"supported" means the passages state the sentence, including any version numbers and figures in it.
"not_supported" means the passages contradict it or say nothing about it. "unclear" means you cannot tell.
Copy the quote exactly; do not paraphrase.
"""
RETRY = "\n\nEnd your reply with the JSON object only; nothing may come after it."


def prompt_hash(prompt: str = PROMPT, retry: str = RETRY, passages: int = PASSAGE_COUNT, seed: int = SEED,
                max_tokens: int = MAX_TOKENS, temperature: float = TEMPERATURE) -> str:
    """sha256 of everything that decides what the referee is asked: a change to any of it is a new
    referee prompt, which the bench spec pins as [referee] prompt_hash."""
    text = json.dumps({"prompt": prompt, "retry": retry, "passages": passages, "seed": seed,
                       "max_tokens": max_tokens, "temperature": temperature}, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PROMPT_HASH = prompt_hash()


def check_prompt(spec: BenchSpec) -> None:
    """BenchSpecError unless this code's referee prompt is the spec's: the same prompt_version, and
    the same prompt_hash when the spec names one (a frozen spec must)."""
    if spec.referee.prompt_version != PROMPT_VERSION:
        raise BenchSpecError(f"referee prompt version: the spec has {spec.referee.prompt_version}, this code has "
                             f"{PROMPT_VERSION}")
    pinned = normal_hash(spec.referee.prompt_hash)
    if spec.frozen and not pinned:
        raise BenchSpecError("referee prompt: a frozen spec needs [referee] prompt_hash; this code's is "
                             f"{PROMPT_HASH}")
    if pinned and pinned != PROMPT_HASH:
        raise BenchSpecError(f"referee prompt hash: the spec has {pinned}, this code has {PROMPT_HASH}")


@dataclass
class Statement:
    text: str
    raw: str
    citations: list[str]


@dataclass
class Judgement:
    statement: str
    index: str
    verdict: str
    quote: str = ""
    reason: str = ""
    referee_error: str = ""      # a transient failure: not judged, judged again on a rerun
    referee_failed: bool = False  # a deterministic failure: a final "unclear"


_TRANSIENT_HTTP = re.compile(r"^HTTP (5\d\d|408|429)\b")


def transient(error: str) -> bool:
    """Whether a gateway error may pass on its own: the server unreachable or timing out (an httpx
    transport error), HTTP 5xx (or 408/429), or a model still loading after the reload."""
    error = (error or "").strip()
    if _TRANSIENT_HTTP.match(error):
        return True
    cls = getattr(httpx, error.split(":", 1)[0].strip(), None)
    if isinstance(cls, type) and issubclass(cls, httpx.TransportError):
        return True
    low = error.lower()
    if "not ready" in low or "loading model" in low:
        return True
    if error.startswith("{"):   # an error object in the stream
        try:
            code = json.loads(error).get("code")
        except (ValueError, AttributeError):
            return False
        return isinstance(code, int) and code >= 500
    return False


def statements(report: str) -> list[Statement]:
    """The report's claims, as written, with exact duplicate texts merged (citations unioned)."""
    merged: dict[str, Statement] = {}
    for claim in split_claims(report):
        found = merged.get(claim.text)
        if found is None:
            merged[claim.text] = Statement(claim.text, claim.raw, list(claim.citations))
        else:
            found.citations += [c for c in claim.citations if c not in found.citations]
    return list(merged.values())


def stored_source_text(run_dir: Path, index: str, source: dict) -> str:
    """pages/<index>.txt if present, plus full_content when not already inside, plus the snippet."""
    page_file = Path(run_dir) / "pages" / f"{index}.txt"
    page = page_file.read_text(encoding="utf-8") if page_file.is_file() else ""
    given = source.get("full_content") or ""
    if given and squash(given) not in squash(page):
        page = page + "\n\n" + given if page else given
    snippet = source.get("snippet") or ""
    return page + "\n\n" + snippet if page and snippet else page or snippet


def last_json_object(reply: str) -> dict:
    """The last JSON object in a reply. Reasoning text before it may contain braces, so the
    greedy verify.json_object would fail; try each "{" and keep the last that decodes."""
    decoder, found, pos = json.JSONDecoder(), {}, 0
    while (start := reply.find("{", pos)) != -1:
        try:
            data, end = decoder.raw_decode(reply, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        pos = end   # only outermost objects count; skip what lies inside this one
        if isinstance(data, dict) and "verdict" in data:
            found = data
    return found


def judge(statement: str, index: str, source: dict | None, text: str, gateway, tier: str) -> Judgement:
    if source is None:
        return Judgement(statement, index, "not_supported", reason="no such source")
    if not text:
        return Judgement(statement, index, "unclear", reason="source has no stored text", referee_failed=True)
    passages = best_passages(statement, text, count=PASSAGE_COUNT)
    snippet = source.get("snippet") or ""
    if snippet and squash(snippet) not in squash(" ".join(passages)):
        passages.append(snippet)
    prompt = PROMPT.format(statement=statement, index=index, title=source.get("title", ""),
                           passages="\n---\n".join(passages))
    passing, failure = "", ""
    for attempt in (prompt, prompt + RETRY):
        answer = gateway.ask(tier, [{"role": "user", "content": attempt}], purpose="referee",
                             private=True, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                             extra_body={"seed": SEED, "chat_template_kwargs": {"enable_thinking": True}})
        if answer.error:
            if transient(answer.error):
                passing = answer.error
            else:
                failure = "gateway error: " + answer.error
            continue
        data = last_json_object(answer.content or "")
        verdict = str(data.get("verdict") or "").strip().lower()
        if verdict not in LABELS:
            shown = " ".join((answer.content or "").split())[:300]
            failure = "reply was not a verdict: " + shown if shown else "empty reply"
            continue
        quote, reason = str(data.get("quote") or ""), str(data.get("reason") or "")
        if verdict == "supported" and not (quote and quote_in_source(quote, text)):
            return Judgement(statement, index, "unclear", quote, "quote not in source: " + reason,
                             referee_failed=True)
        return Judgement(statement, index, verdict, quote, reason)
    if passing:   # one attempt never reached the referee: judge it again later
        return Judgement(statement, index, "unclear", referee_error=passing[:300])
    return Judgement(statement, index, "unclear", reason=failure[:300], referee_failed=True)


def statement_verdict(judgements: list[Judgement]) -> str:
    verdicts = {j.verdict for j in judgements}
    return "supported" if "supported" in verdicts else "not_supported" if "not_supported" in verdicts else "unclear"


def score_run(run_dir: Path, question: Question, gateway, tier: str) -> dict:
    run_dir = Path(run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    sources = {str(s.get("index") or n): s for n, s in
               enumerate(json.loads((run_dir / "sources.json").read_text(encoding="utf-8")), start=1)}
    found, judgements, verdicts, failed = statements(report), [], [], []
    uncited = 0
    for st in found:
        if not st.citations:   # the pipeline promises every claim is cited
            uncited += 1
            judgements.append(Judgement(st.text, "", "not_supported", reason="uncited"))
            verdicts.append("not_supported")
            failed.append(False)
            continue
        mine = []
        for index in st.citations:
            source = sources.get(index)
            text = stored_source_text(run_dir, index, source) if source is not None else ""
            mine.append(judge(st.text, index, source, text, gateway, tier))
        judgements += mine
        verdicts.append(statement_verdict(mine))
        # a failed judgement could have been the one "supported": it decides unless another supports
        failed.append(verdicts[-1] != "supported" and any(j.referee_failed for j in mine))
    count = lambda label: sum(v == label for v in verdicts)
    supported_raw = [st.raw for st, v in zip(found, verdicts) if v == "supported"]
    facts = None
    if question.expect:
        hits = sum(any(fact_pattern(term).search(raw) for raw in supported_raw for term in group)
                   for group in question.expect)
        facts = round(hits / len(question.expect), 3)
    summary = {
        "statements": len(found), "cited": len(found) - uncited, "uncited": uncited,
        "supported": count("supported"), "not_supported": count("not_supported"),
        "unclear": count("unclear"),
        "referee_errors": sum(bool(j.referee_error) for j in judgements),
        "referee_failed": sum(failed),   # statements left "unclear" by a failed judgement
        "facts_right": facts,
        "errors_through": round(count("not_supported") / len(found), 3) if found else None,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
    }
    summary["complete"] = summary["referee_errors"] == 0   # False: some judgements were not made
    (run_dir / "referee.json").write_text(json.dumps({
        "summary": summary, "caveat": REFEREE_CAVEAT,
        "statements": [{"text": s.text, "raw": s.raw, "citations": s.citations, "verdict": v, "referee_failed": f}
                       for s, v, f in zip(found, verdicts, failed)],
        "judgements": [asdict(j) for j in judgements]}, indent=2), encoding="utf-8")
    return summary


SCORE_KEYS = ("statements", "cited", "uncited", "supported", "not_supported", "unclear",
              "referee_errors", "referee_failed", "facts_right", "errors_through", "prompt_version", "prompt_hash",
              "referee_sha256", "complete")


def _bench_questions(spec: BenchSpec) -> dict[str, Question]:
    """Every question of every split by id. A file that doesn't exist yet is skipped."""
    found: dict[str, Question] = {}
    for paths in spec.questions.values():
        for path in paths:
            if Path(path).is_file():
                found.update({q.id: q for q in load_questions(path)})
    return found


def _referee_sha256(spec: BenchSpec, env, hasher) -> str:
    """The hash of the referee file in use: the pinned one (the pin check just proved it)."""
    return normal_hash(spec.referee.sha256 or hasher(Path(env[spec.referee.gguf_env])))


def _earlier_summary(run_dir: Path, spec: BenchSpec, sha: str) -> dict | None:
    """The run's referee.json summary when the same prompt (version and hash) and referee file made
    it and every judgement was made (an incomplete one is judged again)."""
    path = run_dir / "referee.json"
    if not path.is_file():
        return None
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))["summary"]
    except (ValueError, KeyError):
        return None
    same = (summary.get("complete") is True and summary.get("prompt_version") == spec.referee.prompt_version
            and summary.get("prompt_hash") == PROMPT_HASH and "referee_failed" in summary
            and normal_hash(summary.get("referee_sha256")) == sha)
    return summary if same else None


def _manifest_bench(eval_dir: Path) -> dict | None:
    path = Path(eval_dir) / "manifest.json"
    if not path.is_file():
        return None
    bench = json.loads(path.read_text(encoding="utf-8")).get("bench")
    return bench if isinstance(bench, dict) else None


def score_eval(eval_dir: Path, spec: BenchSpec, settings: Settings, gateway, *, env=os.environ,
               reloader=reload_model, hasher=sha256_file, served=None, committed_ledger: str | None = None,
               repo_root: Path | None = None) -> list[dict]:
    """Referee every run of a finished eval that ended with a report; write referee.csv and referee.md.

    Checked before anything is judged: the referee prompt is the spec's (check_prompt); for a bench
    eval, its question files are unchanged since the run (manifest bench.question_files) and, on the
    test split, its attempt is in the committed ledger (committed_ledger: the ledger's text at HEAD);
    the referee GGUF matches the pin. The candidate model is then unloaded once, so the referee
    loads fresh, and llama-swap must report serving that same file (served(base_url, model) ->
    model_path, default served_model_path; it fails closed). A run folder whose referee.json came
    from the same prompt and referee file is reused. PrivacyError and TierNotReady propagate."""
    eval_dir = Path(eval_dir)
    check_prompt(spec)
    bench = _manifest_bench(eval_dir)
    if bench is not None:
        problems = question_file_problems(bench, Path(repo_root or REPO_ROOT))
        if problems:
            raise BenchSpecError(f"{eval_dir.name}: " + "; ".join(problems))
        if bench.get("split") == "test" and eval_dir.name not in committed_eval_ids(committed_ledger):
            raise BenchSpecError(f"{eval_dir.name}: its test attempt is not in the committed ledger at HEAD; "
                                 "commit results/bench_ledger.csv first")
    check_referee_pin(spec, env, hasher)
    sha = _referee_sha256(spec, env, hasher)
    questions = _bench_questions(spec)
    with (eval_dir / "summary.csv").open(encoding="utf-8") as handle:
        wanted = [row for row in csv.DictReader(handle) if row.get("outcome") == "ok"]
    for row in wanted:
        if row["question"] not in questions:
            raise BenchSpecError(f"question '{row['question']}' of {eval_dir.name} is in no question "
                                 "file of the bench spec")
    reloader(settings, spec.referee.tier)
    check_served_model(settings.tier(spec.referee.tier), env.get(spec.referee.gguf_env, ""), "referee GGUF",
                       strict=True, fetch=served or served_model_path)
    rows = []
    for row in wanted:
        run_dir = Path(settings.runs_dir) / row["run_id"]
        summary = _earlier_summary(run_dir, spec, sha)
        if summary is None:
            summary = score_run(run_dir, questions[row["question"]], gateway, spec.referee.tier)
            summary["referee_sha256"] = sha
            path = run_dir / "referee.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["summary"] = summary
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        rows.append({"question": row["question"], "run": row.get("run", ""), "mode": row.get("mode", ""),
                     "run_id": row["run_id"], **{key: summary.get(key) for key in SCORE_KEYS}})
    fields = ("question", "run", "mode", "run_id", *SCORE_KEYS)
    with (eval_dir / "referee.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (eval_dir / "referee.md").write_text(referee_markdown(eval_dir.name, rows), encoding="utf-8")
    return rows


def referee_markdown(name: str, rows: list[dict]) -> str:
    lines = [f"# Referee scores: {name}", "", REFEREE_CAVEAT, "",
             "| question | run | facts_right | errors_through | referee_failed | complete |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['question']} | {r['run']} | {r['facts_right']} | {r['errors_through']} | "
                     f"{r['referee_failed']} of {r['statements']} | {r['complete']} |")
    return "\n".join(lines) + "\n"
