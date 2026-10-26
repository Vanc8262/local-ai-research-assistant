"""The citation verifier: does every claim in a report trace to a source that supports it?

This is the Stage 2 gate. For a research run folder (report.md + sources.json) it:

1. splits the report into claims (sentences and bullets) and finds their [n] citations;
2. flags claims with no citation, and citations that point at no source;
3. fetches each cited page once and checks the claim's hard facts (version numbers,
   figures, percentages) appear in it, the check that catches a 3.15 claim cited
   to an article about 3.14;
4. asks a local model whether the source's most relevant passages support the claim,
   and requires the quote it gives back to appear word for word in the source.

The model step always runs on a private, local tier unless told otherwise, so pages
and claims never leave the PC for verification.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import httpx

from .gateway import Gateway, PrivacyError, TierNotReady
from .runlog import RunLog

CITATION_RE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(*])")
# Hard facts that must appear in a source verbatim: versions like 3.15 or 1.10.7,
# percentages, and numbers with three or more digits (years, sizes, counts).
FACT_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)*\b|\b\d+(?:\.\d+)?\s?%|\b\d{3,}\b")
STOPWORDS = set("""the and for with that this from into over than then they them their there these those
which while where when what about also have has had been being were will would could should can
such more most many much some other only just each both very like using used uses new""".split())
PASSAGE_CHARS, PASSAGE_STEP, PASSAGES = 700, 500, 3
# Verdicts are judgements, not writing: sample greedily so the same report gets the same
# verdicts on every run, and a change in the score measures a change in the code.
TEMPERATURE = 0.0
MIN_CLAIM_CHARS = 40
BOLD_HEADING_RE = re.compile(r"^\*\*[^*]+\*\*:?\s*$|^[^*]*\*\*\s*$")   # a bold line, or one ending in a stray **
SNIPPET_JOIN = "\n[search snippet] "

VERDICTS = ("supported", "partial", "unsupported", "unchecked")
NO_SOURCE = "no source has this number"

PROMPT = """You check citations in a research report.

Claim: {claim}

Passages from the cited source [{index}] ({title}):
{passages}

Does the source support the claim? Answer with JSON only:
{{"verdict": "supported" | "partial" | "unsupported", "quote": "<the exact sentence from the passages that supports it, or empty>", "reason": "<one short sentence>"}}
"supported" means the passages state the claim, including any version numbers and figures in it.
"partial" means they support part of it. Copy the quote exactly; do not paraphrase."""

COMBINED_PROMPT = """You check citations in a research report. The claim cites several sources; judge
whether they support it together, each source covering its own part.

Claim: {claim}

Passages, each marked with its source number:
{passages}

Answer with JSON only:
{{"verdict": "supported" | "partial" | "unsupported", "quotes": [{{"source": "<number>", "quote": "<exact sentence>"}}], "reason": "<one short sentence>"}}
"supported" means every part of the claim, including version numbers and figures, is stated by at
least one of the sources. Give one quote per part. Copy quotes exactly; do not paraphrase."""


@dataclass
class Check:
    index: str
    verdict: str = "unchecked"
    missing_facts: list[str] = field(default_factory=list)
    quote: str = ""
    reason: str = ""
    combined: bool = False    # one check of all the claim's sources together


@dataclass
class Claim:
    text: str
    citations: list[str]
    checks: list[Check] = field(default_factory=list)
    inherited: bool = False   # citations borrowed from the next cited sentence of the same bullet

    @property
    def support(self) -> str:
        """How many of the claim's sources support it, e.g. "1 of 2"."""
        single = [c for c in self.checks if not c.combined]
        together = " (together)" if any(c.combined and c.verdict == "supported" for c in self.checks) else ""
        return f"{sum(c.verdict == 'supported' for c in single)} of {len(single)}{together}"

    @property
    def verdict(self) -> str:
        """The best support any of the claim's sources gives it."""
        if not self.citations:
            return "uncited"
        found = {check.verdict for check in self.checks}
        for verdict in VERDICTS:
            if verdict in found:
                return verdict
        return "unchecked"


@dataclass
class Verification:
    claims: list[Claim]

    def counts(self) -> dict[str, int]:
        counts = {name: 0 for name in ("uncited", *VERDICTS)}
        for claim in self.claims:
            counts[claim.verdict] += 1
        return counts

    def summary(self) -> dict:
        counts, total = self.counts(), len(self.claims)
        cited = total - counts["uncited"]
        checked = cited - counts["unchecked"]
        return {
            "claims": total, **counts,
            "citation_coverage": round(cited / total, 3) if total else None,
            "citation_accuracy": round(counts["supported"] / checked, 3) if checked else None,
            "dangling_citations": sum(check.reason == NO_SOURCE for claim in self.claims
                                      for check in claim.checks),
        }


def split_claims(report: str) -> list[Claim]:
    """Sentences and bullets of the report body, each with the source numbers it cites."""
    body = report.split("\n## Sources", 1)[0]
    claims = []
    for line in body.splitlines():
        line = line.strip().lstrip("-•").strip()
        if (not line or line.startswith(("#", "|", "```")) or re.match(r"^\[\d+\].*https?://", line)
                or BOLD_HEADING_RE.match(line)):
            continue   # headings, tables, and source-list lines such as "[1] Title https://..."
        line_claims = []
        for sentence in SENTENCE_RE.split(line.lstrip("*").strip() if not line.startswith("**") else line):
            citations = []
            for group in CITATION_RE.findall(sentence):
                citations += _expand(group)
            text = " ".join(CITATION_RE.sub("", sentence).replace("**", "").split())
            if not citations and _is_lead_in(text):
                continue   # "The main new features include:" introduces a list; it claims nothing
            if citations or len(text) >= MIN_CLAIM_CHARS:
                line_claims.append(Claim(text=text, citations=list(dict.fromkeys(citations))))
        # A citation at the end of a bullet or paragraph usually covers the sentences before it.
        following: list[str] = []
        for claim in reversed(line_claims):
            if claim.citations:
                following = claim.citations
            elif following:
                claim.citations, claim.inherited = list(following), True
        claims += line_claims
    return claims


def _is_lead_in(text: str) -> bool:
    """An uncited line that ends in a colon introduces the list or section after it."""
    return text.endswith(":") and len(text) < 160


def _expand(group: str) -> list[str]:
    numbers = []
    for part in re.split(r"\s*,\s*", group):
        bounds = re.split(r"\s*[–-]\s*", part)
        if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit() and int(bounds[0]) < int(bounds[1]) <= int(bounds[0]) + 20:
            numbers += [str(n) for n in range(int(bounds[0]), int(bounds[1]) + 1)]
        else:
            numbers.append(part.strip())
    return numbers


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def fetch_source(client: httpx.Client, url: str, cache: Path) -> str:
    """Page text for a cited URL, cached in the run folder so a re-check is offline."""
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    text = ""
    try:
        resp = client.get(url)
        kind = resp.headers.get("content-type", "")
        if resp.status_code == 200 and "html" in kind:
            text = html_to_text(resp.text)
        elif resp.status_code == 200 and kind.startswith("text/"):
            text = " ".join(resp.text.split())
    except httpx.HTTPError:
        pass
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def missing_facts(claim: str, source: str) -> list[str]:
    """Hard facts in the claim that the source never mentions. The version a report is about
    counts too: "3.15 adds X" cited to a page that only covers 3.14 is the mistake to catch.
    A missing fact only lowers "supported" to "partial"; it never makes a claim unsupported."""
    facts = list(dict.fromkeys(f.replace(" ", "") for f in FACT_RE.findall(claim)))
    compact = source.replace(" %", "%")
    return [fact for fact in facts if fact not in compact]


def best_passages(claim: str, source: str) -> list[str]:
    words = {w for w in re.findall(r"[a-z0-9][a-z0-9.+_-]{2,}", claim.lower()) if w not in STOPWORDS}
    windows = [source[start:start + PASSAGE_CHARS]
               for start in range(0, max(len(source) - PASSAGE_STEP, 1), PASSAGE_STEP)]
    scored = sorted(windows, key=lambda w: sum(word in w.lower() for word in words), reverse=True)
    return scored[:PASSAGES]


def parse_verdict(answer: str) -> dict:
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    verdict = data.get("verdict") if data.get("verdict") in ("supported", "partial", "unsupported") else "unchecked"
    return {"verdict": verdict, "quote": str(data.get("quote") or ""), "reason": str(data.get("reason") or "")}


def _squash(text: str) -> str:
    """Letters and digits only: ignores spacing, punctuation, table pipes and quote styles."""
    text = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", " ", text)   # video transcript timestamps
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def quote_in_source(quote: str, text: str, min_ratio: float = 0.9) -> bool:
    """True when every fragment of the quote (split at [...] or ...) is in the source, exactly
    once spacing and punctuation are ignored, or as one run covering min_ratio of it. A quote
    stitched together from separate places, or invented, fails."""
    source = _squash(text)
    parts = [p for p in (_squash(part) for part in re.split(r"\[\s*\.\.\.\s*\]|\.\.\.|…", quote))
             if len(p) >= 12]
    if not parts:
        return False
    for part in parts:
        if part in source:
            continue
        match = difflib.SequenceMatcher(None, part, source, autojunk=False).find_longest_match(
            0, len(part), 0, len(source))
        if match.size < min_ratio * len(part):
            return False
    return True


class _ReloadingGateway:
    """Passes calls to the gateway, reloading the local model every `every` calls. The local
    server's host memory grows over a long verification (14 GB on a 58-claim report)."""

    def __init__(self, gateway: Gateway, every: int, reloader) -> None:
        self.gateway, self.every, self.reloader, self.calls = gateway, every, reloader, 0
        self.settings = gateway.settings

    def ask(self, *args, **kwargs):
        if self.every and self.reloader and self.calls and self.calls % self.every == 0:
            self.reloader()
        self.calls += 1
        return self.gateway.ask(*args, **kwargs)


def verify_run(run_dir: Path, gateway: Gateway, log: RunLog, *, tier: str | None = None,
               private: bool = True, transport=None, reload_every: int = 0,
               reloader=None) -> Verification:
    run_dir = Path(run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    sources = {str(s.get("index") or n): s for n, s in
               enumerate(json.loads((run_dir / "sources.json").read_text(encoding="utf-8")), start=1)}
    tier = tier or gateway.settings.default_tier
    gateway = _ReloadingGateway(gateway, reload_every, reloader)
    claims = split_claims(report)
    client = httpx.Client(follow_redirects=True, timeout=30.0, transport=transport,
                          headers={"User-Agent": "local-ai-research-assistant/0.1 (citation check)"})
    try:
        for claim in claims:
            for index in claim.citations:
                claim.checks.append(_check(claim, index, sources, client, run_dir, gateway, tier, private))
            verdicts = {check.verdict for check in claim.checks}
            if len(claim.checks) > 1 and "supported" not in verdicts and "partial" in verdicts:
                claim.checks.append(_combined_check(claim, sources, client, run_dir, gateway, tier, private))
    finally:
        client.close()

    result = Verification(claims)
    (run_dir / "verification.json").write_text(json.dumps(
        {"summary": result.summary(), "claims": [{**asdict(c), "verdict": c.verdict, "support": c.support}
                                                for c in claims]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "verification.md").write_text(verification_markdown(result), encoding="utf-8")
    log.step("verify", run=run_dir.name, tier=tier, outcome="ok", **result.summary())
    return result


def _check(claim: Claim, index: str, sources: dict, client, run_dir: Path, gateway: Gateway,
           tier: str, private: bool) -> Check:
    source = sources.get(index)
    if source is None:
        return Check(index=index, verdict="unsupported", reason=NO_SOURCE)
    page, snippet, text = _source_text(source, index, client, run_dir)
    if not text:
        return Check(index=index, reason="source could not be read")
    check = Check(index=index, missing_facts=missing_facts(claim.text, text))
    passages = best_passages(claim.text, page) if page else []
    if snippet:
        passages.append(snippet)
    prompt = PROMPT.format(claim=claim.text, index=index, title=source.get("title", ""),
                           passages="\n---\n".join(passages))
    parsed, raw = {"verdict": "unchecked", "quote": "", "reason": ""}, ""
    for attempt in (prompt, prompt + "\n\nReply with the JSON object only, nothing before or after it."):
        try:
            answer = gateway.ask(tier, [{"role": "user", "content": attempt}], purpose="verify",
                                 private=private, max_tokens=1500, temperature=TEMPERATURE)
        except (PrivacyError, TierNotReady) as exc:
            check.reason = str(exc)
            return check
        raw = answer.error or answer.content
        parsed = parse_verdict("" if answer.error else answer.content)
        if parsed["verdict"] != "unchecked":
            break
    check.verdict, check.quote, check.reason = parsed["verdict"], parsed["quote"], parsed["reason"]
    if check.verdict == "unchecked":
        check.reason = "model reply was not a verdict: " + " ".join(raw.split())[:300]
    if check.verdict == "supported" and check.quote and not quote_in_source(check.quote, text):
        check.verdict, check.reason = "partial", "the model's quote is not in the source: " + check.reason
    if check.verdict == "supported" and check.missing_facts:
        check.verdict = "partial"
        check.reason = f"source does not mention {', '.join(check.missing_facts)}; " + check.reason
    return check


def _source_text(source: dict, index: str, client, run_dir: Path) -> tuple[str, str, str]:
    """(page, snippet, both). The snippet is kept because some pages fetch as boilerplate
    (video sites, script-only pages) while the snippet carries the text the report used.
    A codex source (file://) is not fetched: its full_content is the chunk LDR was given."""
    url = source.get("link") or source.get("url") or ""
    if url.startswith("http"):
        page = fetch_source(client, url, run_dir / "pages" / f"{index}.txt")
    else:
        page = source.get("full_content") or ""
    snippet = source.get("snippet") or ""
    return page, snippet, (page + SNIPPET_JOIN + snippet if page and snippet else page or snippet)


def _combined_check(claim: Claim, sources: dict, client, run_dir: Path, gateway, tier: str,
                    private: bool) -> Check:
    """One check of all the claim's sources together, for claims each source supports in part,
    such as a sentence listing several features drawn from different pages."""
    texts, blocks = {}, []
    for index in claim.citations:
        if index not in sources:
            continue
        page, snippet, text = _source_text(sources[index], index, client, run_dir)
        if text:
            texts[index] = text
            parts = (best_passages(claim.text, page)[:2] if page else []) + ([snippet] if snippet else [])
            blocks += [f"[{index}] {part}" for part in parts]
    check = Check(index="+".join(texts), combined=True,
                  missing_facts=missing_facts(claim.text, " ".join(texts.values())))
    if len(texts) < 2:
        check.reason = "fewer than two readable sources"
        return check
    prompt = COMBINED_PROMPT.format(claim=claim.text, passages="\n---\n".join(blocks))
    try:
        answer = gateway.ask(tier, [{"role": "user", "content": prompt}], purpose="verify",
                             private=private, max_tokens=1500, temperature=TEMPERATURE)
    except (PrivacyError, TierNotReady) as exc:
        check.reason = str(exc)
        return check
    data = _json_object("" if answer.error else answer.content)
    verdict = data.get("verdict")
    check.verdict = verdict if verdict in ("supported", "partial", "unsupported") else "unchecked"
    check.reason = str(data.get("reason") or "")
    # Quotes come as {"source", "quote"} objects; some replies give bare strings instead,
    # which count when they are in any of the claim's sources.
    quotes = []
    for item in data.get("quotes") or []:
        if isinstance(item, dict):
            quotes.append((str(item.get("source") or "").strip("[] "), str(item.get("quote") or "")))
        elif isinstance(item, str):
            quotes.append(("", item))
    quotes = [(source, quote) for source, quote in quotes if quote.strip()]
    check.quote = " | ".join(f"[{source or '?'}] {quote}" for source, quote in quotes)
    if check.verdict == "supported":
        bad = [quote for source, quote in quotes
               if not (quote_in_source(quote, texts[source]) if source in texts
                       else any(quote_in_source(quote, text) for text in texts.values()))]
        if not quotes or bad:
            check.verdict = "partial"
            check.reason = ("no quotes given; " if not quotes else "a quote is not in its source; ") + check.reason
        elif check.missing_facts:
            check.verdict = "partial"
            check.reason = f"no source mentions {', '.join(check.missing_facts)}; " + check.reason
    return check


def _json_object(answer: str) -> dict:
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def verification_markdown(result: Verification) -> str:
    s = result.summary()
    pct = lambda v: "-" if v is None else f"{v:.0%}"
    lines = ["# Citation check", "",
             f"- Claims: {s['claims']}",
             f"- Citation coverage: {pct(s['citation_coverage'])} ({s['uncited']} uncited)",
             f"- Citation accuracy: {pct(s['citation_accuracy'])} "
             f"({s['supported']} supported, {s['partial']} partial, {s['unsupported']} unsupported, "
             f"{s['unchecked']} unchecked)",
             f"- Citations to a missing source: {s['dangling_citations']}", "", "## Claims that need attention", ""]
    flagged = [c for c in result.claims
               if c.verdict != "supported" or any(k.reason == NO_SOURCE for k in c.checks)
               or any(k.verdict == "unsupported" for k in c.checks)]   # a source contradicts it
    for claim in flagged:
        cites = "".join(f"[{i}]" for i in claim.citations) + (" (inherited)" if claim.inherited else "")
        support = f" ({claim.support} sources)" if claim.checks else ""
        lines.append(f"- **{claim.verdict}**{support} {claim.text} {cites}")
        for check in claim.checks:
            facts = f" missing: {', '.join(check.missing_facts)}." if check.missing_facts else ""
            lines.append(f"  - [{check.index}] {check.verdict}.{facts} {check.reason}".rstrip())
    if not flagged:
        lines.append("None.")
    return "\n".join(lines) + "\n"
