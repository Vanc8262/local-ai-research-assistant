"""Small-sample statistics for eval pass rates: Wilson intervals and the exact McNemar test.

The 2026-11-14 eval review showed that 6/10 against 10/10 was not significant (McNemar exact
p = 0.125), so the eval summary now prints an interval for every pass rate and a paired test
for every pair of modes run on the same questions."""
from __future__ import annotations

from math import comb, sqrt

Z95 = 1.959964


def wilson(passed: int, total: int, z: float = Z95) -> tuple[float, float] | None:
    """95% Wilson score interval for passed/total."""
    if not total:
        return None
    p = passed / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, round(centre - half, 6)), min(1.0, round(centre + half, 6))


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pairs: items only A passed, and
    items only B passed. 1.0 when there are none."""
    n = only_a + only_b
    if not n:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired(rows: list[dict], mode_a: str, mode_b: str) -> dict | None:
    """Pass/fail pairs of two modes on the same (question, run), counting only items both
    modes finished (outcome ok)."""
    gates = {}
    for r in rows:
        if r.get("outcome") == "ok":
            gates.setdefault((r["question"], r.get("run", 1)), {})[r["mode"]] = r.get("gate") == "pass"
    pairs = [(g[mode_a], g[mode_b]) for g in gates.values() if mode_a in g and mode_b in g]
    if not pairs:
        return None
    only_a = sum(a and not b for a, b in pairs)
    only_b = sum(b and not a for a, b in pairs)
    return {"pairs": len(pairs), "both": sum(a and b for a, b in pairs), "neither": sum(not a and not b for a, b in pairs),
            "only_a": only_a, "only_b": only_b, "p": round(mcnemar_exact(only_a, only_b), 4)}
