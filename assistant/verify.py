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
MIN_CLAIM_CHARS = 40

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


@dataclass
class Check:
    index: str
    verdict: str = "unchecked"
    missing_facts: list[str] = field(default_factory=list)
    quote: str = ""
    reason: str = ""


@dataclass
class Claim:
    text: str
    citations: list[str]
    checks: list[Check] = field(default_factory=list)

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
        line = line.strip().lstrip("-*•").strip()
        if not line or line.startswith(("#", "|", "```")) or re.match(r"^\[\d+\].*https?://", line):
            continue   # headings, tables, and source-list lines such as "[1] Title https://..."
        for sentence in SENTENCE_RE.split(line):
            citations = []
            for group in CITATION_RE.findall(sentence):
                citations += _expand(group)
            text = CITATION_RE.sub("", sentence).strip()
            if citations or len(text) >= MIN_CLAIM_CHARS:
                claims.append(Claim(text=" ".join(text.split()), citations=list(dict.fromkeys(citations))))
    return claims


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


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[“”\"'‘’]", "", text).lower().split())


def verify_run(run_dir: Path, gateway: Gateway, log: RunLog, *, tier: str | None = None,
               private: bool = True, transport=None) -> Verification:
    run_dir = Path(run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    sources = {str(s.get("index") or n): s for n, s in
               enumerate(json.loads((run_dir / "sources.json").read_text(encoding="utf-8")), start=1)}
    tier = tier or gateway.settings.default_tier
    claims = split_claims(report)
    client = httpx.Client(follow_redirects=True, timeout=30.0, transport=transport,
                          headers={"User-Agent": "local-ai-research-assistant/0.1 (citation check)"})
    try:
        for claim in claims:
            for index in claim.citations:
                claim.checks.append(_check(claim, index, sources, client, run_dir, gateway, tier, private))
    finally:
        client.close()

    result = Verification(claims)
    (run_dir / "verification.json").write_text(json.dumps(
        {"summary": result.summary(), "claims": [{**asdict(c), "verdict": c.verdict} for c in claims]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "verification.md").write_text(verification_markdown(result), encoding="utf-8")
    log.step("verify", run=run_dir.name, tier=tier, outcome="ok", **result.summary())
    return result


def _check(claim: Claim, index: str, sources: dict, client, run_dir: Path, gateway: Gateway,
           tier: str, private: bool) -> Check:
    source = sources.get(index)
    if source is None:
        return Check(index=index, verdict="unsupported", reason=NO_SOURCE)
    url = source.get("link") or source.get("url") or ""
    text = fetch_source(client, url, run_dir / "pages" / f"{index}.txt") if url.startswith("http") else ""
    text = text or source.get("snippet") or ""
    if not text:
        return Check(index=index, reason="source could not be read")
    check = Check(index=index, missing_facts=missing_facts(claim.text, text))
    passages = "\n---\n".join(best_passages(claim.text, text))
    prompt = PROMPT.format(claim=claim.text, index=index, title=source.get("title", ""), passages=passages)
    try:
        answer = gateway.ask(tier, [{"role": "user", "content": prompt}], purpose="verify",
                             private=private, max_tokens=800)
    except (PrivacyError, TierNotReady) as exc:
        check.reason = str(exc)
        return check
    parsed = parse_verdict(answer.content if not answer.error else "")
    check.verdict, check.quote, check.reason = parsed["verdict"], parsed["quote"], parsed["reason"]
    if check.verdict == "supported" and check.quote and _normal(check.quote) not in _normal(text):
        check.verdict, check.reason = "partial", "the model's quote is not in the source: " + check.reason
    if check.verdict == "supported" and check.missing_facts:
        check.verdict = "partial"
        check.reason = f"source does not mention {', '.join(check.missing_facts)}; " + check.reason
    return check


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
               if c.verdict != "supported" or any(k.reason == NO_SOURCE for k in c.checks)]
    for claim in flagged:
        lines.append(f"- **{claim.verdict}** {claim.text} {''.join(f'[{i}]' for i in claim.citations)}")
        for check in claim.checks:
            facts = f" missing: {', '.join(check.missing_facts)}." if check.missing_facts else ""
            lines.append(f"  - [{check.index}] {check.verdict}.{facts} {check.reason}".rstrip())
    if not flagged:
        lines.append("None.")
    return "\n".join(lines) + "\n"
