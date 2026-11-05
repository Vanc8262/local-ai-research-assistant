"""The repair step: turn a report that failed the citation check into one that passes it honestly.

For a run that has been through `verify_run`, every claim that failed is handled in turn:

- if another likely source supports the claim as written, it cites that source instead;
- otherwise a cited claim may be narrowed to the part its sources support;
- a claim that cannot be saved either way is removed.

The model's "supported" is too generous to move a claim on alone: it passed "six pillars" on a
quote that never counts them. A move also needs the quote to share a good part of the claim's
words. A repair must not change what a report says to fit a source, because the source can be the
one that is wrong: an old page counting five AWS pillars would turn a correct "six" into a
wrong "five" that passes the check. So a rewrite may only delete words. One that brings in any
word or number the sentence did not already have is refused.

The report is then checked again, and whatever still fails is removed. Only changed sentences
go back to the model, so a repair costs a fraction of the first check. It never adds a claim
the check has not passed, and report.original.md, repair.md and repair.json keep what was
written and every change, so the removals can be read and counted. Unchecked claims are left
alone: the check could not read their source, which says nothing about the claim.

All of this runs on a local tier, whatever wrote the report. If the model cannot answer, the
repair stops and puts the report back, because silence must not read as "remove this claim".
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from .gateway import Gateway, PrivacyError, TierNotReady
from .runlog import RunLog
from .verify import (BOLD_HEADING_RE, CITATION_RE, NUMBERED_RE, Claim, Verification, _check, _is_lead_in,
                     _check_key, _json_object, _ReloadingGateway, _source_text, _squash, best_passages, keywords,
                     known_checks, verify_run)

ORIGINALS = ("report.md", "verification.json", "verification.md")
CANDIDATE_SOURCES = 2     # sources tried for an uncited claim, the ones sharing most words with it
MIN_REWRITE_CHARS = 20
MIN_QUOTE_OVERLAP = 0.25  # share of a moved claim's words that its new quote must contain
TEMPERATURE = 0.0

REWRITE_PROMPT = """A sentence in a research report failed a citation check. Shorten it, by deleting words, so it says only what the passages below state.

Sentence: {claim}
Why it failed: {reasons}

Passages from the sources it cites:
{passages}

Rules:
- Keep the sentence's own words and delete the part the passages do not support.
- Do not add any word, fact or figure, even one the passages state.
- It must still be a complete sentence. Do not add citation numbers.
- If no part of it can stand this way, use the action "remove".

Answer with JSON only:
{{"action": "rewrite" | "remove", "sentence": "<the new sentence, or empty>"}}"""


class RepairError(Exception):
    """The model could not answer, so the repair stopped and the report was put back."""


@dataclass
class Change:
    action: str     # "cited", "rewritten" or "removed"
    before: str
    after: str = ""
    reason: str = ""


@dataclass
class Repair:
    before: dict
    after: dict
    changes: list[Change] = field(default_factory=list)

    def count(self, action: str) -> int:
        return sum(change.action == action for change in self.changes)


class _CheckedGateway:
    """Turns a failed model call into RepairError, which nothing in the check swallows. The
    usual cause is the local server running out of memory, so it reloads and tries once more."""

    def __init__(self, gateway: Gateway, reloader=None) -> None:
        self.gateway, self.settings, self.reloader = gateway, gateway.settings, reloader

    def ask(self, *args, **kwargs):
        try:
            answer = self.gateway.ask(*args, **kwargs)
            if answer.error and self.reloader:
                self.reloader()
                answer = self.gateway.ask(*args, **kwargs)
        except (PrivacyError, TierNotReady) as exc:
            raise RepairError(str(exc)) from None
        if answer.error:
            raise RepairError(answer.error)
        return answer


def repair_run(run_dir: Path, gateway: Gateway, log: RunLog, *, tier: str | None = None, private: bool = True,
               transport=None, reload_every: int = 0, reloader=None) -> Repair:
    run_dir = Path(run_dir)
    tier = tier or gateway.settings.default_tier
    report_path = run_dir / "report.md"
    for name in ORIGINALS:
        if (run_dir / name).exists() and not _original(run_dir, name).exists():
            shutil.copyfile(run_dir / name, _original(run_dir, name))
    model = _ReloadingGateway(_CheckedGateway(gateway, reloader), reload_every, reloader)
    judged: dict = {}   # verdicts given while looking for a better source, so they are not asked twice
    check = lambda: verify_run(run_dir, model, log, tier=tier, private=private, transport=transport,
                               known={**known_checks(run_dir), **judged})
    try:
        checked = check()
        before = checked.summary()
        client = httpx.Client(follow_redirects=True, timeout=30.0, transport=transport,
                              headers={"User-Agent": "local-ai-research-assistant/0.1 (citation check)"})
        try:
            report, changes = _first_pass(report_path.read_text(encoding="utf-8"), checked, run_dir, client,
                                          model, tier, private, judged)
        finally:
            client.close()
        report_path.write_text(_tidy(report), encoding="utf-8")
        checked = check()
        report = _remove_failures(report_path.read_text(encoding="utf-8"), checked, changes)
        report_path.write_text(_tidy(report), encoding="utf-8")
        checked = check()
    except RepairError:
        for name in ORIGINALS:
            if _original(run_dir, name).exists():
                shutil.copyfile(_original(run_dir, name), run_dir / name)
        log.step("repair", run=run_dir.name, tier=tier, outcome="error")
        raise

    result = Repair(before=before, after=checked.summary(), changes=changes)
    (run_dir / "repair.json").write_text(json.dumps(
        {"before": result.before, "after": result.after, "changes": [asdict(c) for c in changes]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "repair.md").write_text(repair_markdown(result), encoding="utf-8")
    log.step("repair", run=run_dir.name, tier=tier, outcome="ok", cited=result.count("cited"),
             rewritten=result.count("rewritten"), removed=result.count("removed"),
             claims_before=before["claims"], claims_after=result.after["claims"])
    return result


def _original(run_dir: Path, name: str) -> Path:
    path = Path(name)
    return run_dir / f"{path.stem}.original{path.suffix}"


def _first_pass(report: str, checked: Verification, run_dir: Path, client, model, tier: str,
                private: bool, judged: dict) -> tuple[str, list[Change]]:
    sources = {str(s.get("index") or n): s for n, s in
               enumerate(json.loads((run_dir / "sources.json").read_text(encoding="utf-8")), start=1)}
    changes = []
    for claim in checked.claims:
        raw = claim.raw
        if raw not in report:
            continue
        if claim.inherited:   # the sentence lending these citations may be about to go
            pinned = _with_citations(raw, claim.citations)
            report, raw = report.replace(raw, pinned, 1), pinned
        if claim.verdict in ("supported", "unchecked"):
            continue
        claim.raw = raw
        change = _fix(claim, sources, run_dir, client, model, tier, private, judged)
        changes.append(change)
        report = _remove(report, raw) if change.action == "removed" else report.replace(raw, change.after, 1)
    return report, changes


def _fix(claim: Claim, sources: dict, run_dir: Path, client, model, tier: str, private: bool,
         judged: dict) -> Change:
    for index in _likely_sources(claim.text, sources, client, run_dir, skip=claim.citations):
        check = _check(claim, index, sources, client, run_dir, model, tier, private)
        if _quote_backs(check.quote, claim.text):
            judged[_check_key(claim.text, index)] = check
        if check.verdict == "supported" and _quote_backs(check.quote, claim.text):
            return Change("cited", claim.text, _with_citations(_without_citations(claim.raw), [index]), check.reason)
    if not claim.citations:
        return Change("removed", claim.text, reason="no likely source supports it")
    reasons = _reasons(claim)
    sentence = _rewrite(claim, reasons, sources, run_dir, client, model, tier, private)
    if sentence:
        return Change("rewritten", claim.text, _with_citations(sentence, claim.citations), reasons)
    return Change("removed", claim.text, reason=reasons)


def _likely_sources(text: str, sources: dict, client, run_dir: Path, skip: list[str]) -> list[str]:
    words, scores = keywords(text), {}
    for index, source in sources.items():
        if index in skip:
            continue
        body = _source_text(source, index, client, run_dir)[2].lower()
        scores[index] = sum(word in body for word in words)
    ranked = sorted((index for index, score in scores.items() if score), key=scores.get, reverse=True)
    return ranked[:CANDIDATE_SOURCES]


def _quote_backs(quote: str, text: str) -> bool:
    words, lowered = _content_words(text), quote.lower()
    return bool(words) and sum(word in lowered for word in words) / len(words) >= MIN_QUOTE_OVERLAP


def _rewrite(claim: Claim, reasons: str, sources: dict, run_dir: Path, client, model, tier: str,
             private: bool) -> str:
    blocks = []
    for index in claim.citations:
        if index in sources:
            page, snippet, _ = _source_text(sources[index], index, client, run_dir)
            parts = (best_passages(claim.text, page)[:2] if page else []) + ([snippet] if snippet else [])
            blocks += [f"[{index}] {part}" for part in parts]
    if not blocks:
        return ""
    prompt = REWRITE_PROMPT.format(claim=claim.text, reasons=reasons, passages="\n---\n".join(blocks))
    answer = model.ask(tier, [{"role": "user", "content": prompt}], purpose="repair", private=private,
                       max_tokens=1000, temperature=TEMPERATURE)
    data = _json_object(answer.content)
    sentence = " ".join(CITATION_RE.sub("", str(data.get("sentence") or "")).split())
    if data.get("action") != "rewrite" or len(sentence) < MIN_REWRITE_CHARS or _squash(sentence) == _squash(claim.text):
        return ""
    return sentence if _only_deletes(sentence, claim.text) else ""


def _content_words(text: str) -> set[str]:
    return {word.rstrip(".+_-") for word in keywords(text)} | set(re.findall(r"\d+(?:\.\d+)?", text))


def _only_deletes(sentence: str, original: str) -> bool:
    return _content_words(sentence) <= _content_words(original)


def _remove_failures(report: str, checked: Verification, changes: list[Change]) -> str:
    """The second pass: what still fails goes. A rewrite that failed counts as a removal of
    the sentence the report first had, not as a rewrite."""
    rewrites = {_squash(CITATION_RE.sub("", c.after)): c for c in changes if c.action == "rewritten"}
    for claim in checked.claims:
        if claim.verdict in ("supported", "unchecked") or claim.raw not in report:
            continue
        report = _remove(report, claim.raw)
        rewrite = rewrites.get(_squash(claim.text))
        if rewrite:
            rewrite.action, rewrite.after = "removed", ""
            rewrite.reason = f"its rewrite failed too: {_reasons(claim)}"
        else:
            changes.append(Change("removed", claim.text, reason=_reasons(claim)))
    return report


def _reasons(claim: Claim) -> str:
    return "; ".join(check.reason for check in claim.checks if check.reason) or claim.verdict


def _with_citations(sentence: str, citations: list[str]) -> str:
    """Citations go before the closing punctuation, where the sentence splitter expects them."""
    marks = "".join(f"[{index}]" for index in citations)
    body, tail = re.fullmatch(r"(.*?)([.!?]*\**)", sentence, re.DOTALL).groups()
    return f"{body.rstrip()} {marks}{tail}"


def _without_citations(sentence: str) -> str:
    return re.sub(r"(?:\s*,?\s*\[\d+(?:\s*[,–-]\s*\d+)*\])+", "", sentence)


def _remove(report: str, raw: str) -> str:
    return re.sub(re.escape(raw) + r"[ \t]*", "", report, count=1)


def _tidy(report: str) -> str:
    """Clear what removals leave behind: empty bullets, and headings, bold labels and lead-ins
    with nothing under them. The source list is not touched."""
    body, marker, tail = report.partition("\n## Sources")
    kept: list[str] = []
    for line in reversed(body.splitlines()):
        line = line.rstrip()
        bare = NUMBERED_RE.sub("", line.strip().lstrip("-*•").strip() if not line.strip().startswith("**") else line.strip())
        if line and not bare:
            continue   # a bullet or number with nothing after it
        if line and _is_title(line, bare) and _nothing_under(line, bare, kept):
            continue
        kept.insert(0, line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).rstrip() + "\n" + marker + tail


def _is_title(line: str, bare: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("# ") or stripped.startswith(("- ", "* ", "• ")) or NUMBERED_RE.match(stripped):
        return False   # the report's own title stays, and so do list items, even a bare "6. **Sustainability**"
    return stripped.startswith("#") or bool(BOLD_HEADING_RE.match(bare)) or _is_lead_in(CITATION_RE.sub("", bare).strip())


def _level(line: str, bare: str) -> int:
    """Heading depth; labels and lead-ins rank below every heading."""
    stripped = line.strip()
    return len(stripped) - len(stripped.lstrip("#")) if stripped.startswith("#") else 7


def _nothing_under(line: str, bare: str, following: list[str]) -> bool:
    for next_line in following:
        if not next_line.strip():
            continue
        next_bare = NUMBERED_RE.sub("", next_line.strip().lstrip("-*•").strip()
                                    if not next_line.strip().startswith("**") else next_line.strip())
        return _is_title(next_line, next_bare) and _level(next_line, next_bare) <= _level(line, bare)
    return True


def repair_markdown(result: Repair) -> str:
    pct = lambda v: "-" if v is None else f"{v:.0%}"
    before, after = result.before, result.after
    lines = ["# Report repair", "",
             f"- Claims: {before['claims']} before, {after['claims']} after",
             f"- Citation coverage: {pct(before['citation_coverage'])} before, {pct(after['citation_coverage'])} after",
             f"- Citation accuracy: {pct(before['citation_accuracy'])} before, {pct(after['citation_accuracy'])} after",
             f"- Changes: {result.count('cited')} cited, {result.count('rewritten')} rewritten, "
             f"{result.count('removed')} removed", "", "## Changes", ""]
    for change in result.changes:
        lines.append(f"- **{change.action}** {change.before}")
        if change.after:
            lines.append(f"  - now: {change.after}")
        if change.reason:
            lines.append(f"  - why: {change.reason}")
    if not result.changes:
        lines.append("None.")
    return "\n".join(lines) + "\n"
