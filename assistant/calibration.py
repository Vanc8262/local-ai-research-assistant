"""The referee's calibration set: pairs of (report sentence, cited source) that a person labels,
and the check that the referee agrees with those labels often enough to be trusted.

`sample_pairs` draws pairs from finished dev-split evals. The pipeline's own verifier already
sorted each claim (supported, partial or unsupported, or removed by repair), and the sample takes
about a third from each so hard cases are in. Claude labels the set; `spot_check` hands Van a blind
subset to label himself, and `agreement` counts the disagreements. `judge_pairs` then runs the
referee over every pair and `recall` / `meets` compare it with the labels. Labels use the
referee's own words: supported, not_supported, unclear.

Nothing here writes to a run folder, and the test split is refused when sampling, so a
calibration set cannot leak the questions a candidate is scored on.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from .bench_spec import BenchSpec
from .referee import LABELS, judge, statements, stored_source_text
from .stats import wilson

PAIR_FIELDS = ("pair_id", "question", "mode", "run_id", "statement", "index", "stratum", "source_title",
               "source_url", "label", "labeller", "note")
STRATA = ("supported", "doubtful", "removed")


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sources(run_dir: Path) -> dict[str, dict]:
    path = run_dir / "sources.json"
    if not path.exists():
        return {}
    return {str(s.get("index") or n): s for n, s in enumerate(_load(path), start=1)}


def _run_pairs(eval_row: dict, run_dir: Path) -> list[dict]:
    """Every cited (statement, source) pair of one run, with the pipeline's verdict as stratum."""
    report = run_dir / "report.md"
    if not report.exists():
        return []
    sources = _sources(run_dir)
    verdicts = {}
    if (run_dir / "verification.json").exists():
        verdicts = {c["text"]: c.get("verdict", "") for c in _load(run_dir / "verification.json")["claims"]}
    found = [(st, {"supported": "supported", "partial": "doubtful", "unsupported": "doubtful"}.get(
        verdicts.get(st.text, ""), "other")) for st in statements(report.read_text(encoding="utf-8"))]
    repair = run_dir / "repair.json"
    if repair.exists():
        removed = [c["before"] for c in _load(repair)["changes"] if c["action"] == "removed"]
        original = run_dir / "report.original.md"   # the final report no longer holds these sentences
        if original.exists():
            have = {st.text for st, _ in found}
            found += [(st, "removed") for st in statements(original.read_text(encoding="utf-8"))
                      if st.text not in have and any(st.text in before or st.raw.strip() in before for before in removed)]
    out = []
    for st, stratum in found:
        for index in st.citations:
            source = sources.get(index, {})
            out.append({"question": eval_row["question"], "mode": eval_row.get("mode", ""),
                        "run_id": eval_row["run_id"], "statement": st.text, "index": index, "stratum": stratum,
                        "source_title": source.get("title", ""),
                        "source_url": source.get("link") or source.get("url", ""),
                        "label": "", "labeller": "", "note": ""})
    return out


def _write(path: Path, rows: list[dict], fields=PAIR_FIELDS) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sample_pairs(eval_dirs: list[Path], out: Path, dev_ids: set[str], n: int = 150, seed: int = 20261115,
                 runs_dir: Path | None = None) -> list[dict]:
    """About n/3 pairs from each stratum, topped up from what remains, drawn with a fixed seed."""
    pool, seen = [], set()
    for eval_dir in map(Path, eval_dirs):
        base = Path(runs_dir) if runs_dir else eval_dir.parent
        rows = _read(eval_dir / "summary.csv")
        outside = sorted({r["question"] for r in rows} - set(dev_ids))
        if outside:
            raise ValueError(f"{eval_dir.name} holds questions that are not dev questions: {', '.join(outside)}")
        for row in rows:
            if row.get("outcome") != "ok":
                continue
            for pair in _run_pairs(row, base / row["run_id"]):
                key = (pair["run_id"], pair["statement"], pair["index"])
                if key not in seen:
                    seen.add(key)
                    pool.append(pair)
    rng = random.Random(seed)
    picked = []
    share = n // len(STRATA)
    for stratum in STRATA:
        members = [p for p in pool if p["stratum"] == stratum]
        picked += rng.sample(members, min(share, len(members)))
    chosen = {id(p) for p in picked}
    rest = [p for p in pool if id(p) not in chosen]
    picked += rng.sample(rest, min(n - len(picked), len(rest)))
    rng.shuffle(picked)
    for i, row in enumerate(picked, start=1):
        row["pair_id"] = f"p{i:03d}"
    _write(out, picked)
    return picked


def spot_check(labelled: Path, out: Path, n: int = 25, seed: int = 20261116) -> list[dict]:
    """A random n of the labelled pairs with label and labeller blanked, for Van to label blind."""
    rows = [r for r in _read(labelled) if r.get("label")]
    picked = random.Random(seed).sample(rows, min(n, len(rows)))
    for row in picked:
        row["label"], row["labeller"] = "", ""
    _write(out, picked)
    return picked


def judge_pairs(labelled: Path, runs_dir: Path, gateway, tier: str) -> list[dict]:
    """Add referee_verdict to every pair, reading only the stored sources of its run folder."""
    rows, cache = _read(labelled), {}
    for row in rows:
        run_dir = Path(runs_dir) / row["run_id"]
        if run_dir not in cache:
            cache[run_dir] = _sources(run_dir)
        source = cache[run_dir].get(row["index"])
        text = stored_source_text(run_dir, row["index"], source) if source is not None else ""
        row["referee_verdict"] = judge(row["statement"], row["index"], source, text, gateway, tier).verdict
    return rows


def recall(rows: list[dict]) -> dict:
    """How often the referee gives the label's verdict, per label. `unclear` labels are left out."""
    result = {}
    for label in ("supported", "not_supported"):
        mine = [r for r in rows if r.get("label") == label]
        hits = sum(r.get("referee_verdict") == label for r in mine)
        result[label] = (hits, len(mine), wilson(hits, len(mine)))
    result["unclear_labels"] = sum(r.get("label") == "unclear" for r in rows)
    return result


def meets(result: dict, spec: BenchSpec) -> bool:
    """Both recalls at or above the spec's minimums. A label with no pairs cannot pass."""
    for label, minimum in (("supported", spec.referee.min_supported_recall),
                           ("not_supported", spec.referee.min_not_supported_recall)):
        hits, total = result[label][:2]
        if not total or hits / total < minimum:
            return False
    return True


def agreement(claude: Path, van: Path) -> tuple[int, int]:
    """(agreeing, compared) over pair_ids both files label; a pair Van left blank is not compared."""
    mine = {r["pair_id"]: r["label"] for r in _read(claude) if r.get("label") in LABELS}
    theirs = {r["pair_id"]: r["label"] for r in _read(van) if r.get("label") in LABELS}
    common = [k for k in theirs if k in mine]
    return sum(mine[k] == theirs[k] for k in common), len(common)
