# Second opinion and repair, re-run on the held-out eval (2026-11-15)

**Read this first.**

- Fewer removals shows that the verdicts changed. It does not show that every claim that
  stayed is true.
- A passing gate shows that the checker accepts a report. It does not show that every
  sentence is true.
- I (Claude) judged the removals listed below. Van hasn't reviewed them.

## What ran

`scripts/redo_second_opinion.py` ran on Van-Desktop over `runs/eval-20261114-195649-7fd789`,
the 21 runs the decider had judged. It kept the checker's own verdicts as they were before
the decider ran, asked the decider again, and ran repair. It ran twice.

| Run | Second opinion reads | Time | Folder |
|---|---|---|---|
| quote | the checker's quote (this branch) | 33 min | `runs/redo-20261115-070902-2d7364-quote` |
| passages | the three passages (the code on main) | 39 min | `runs/redo-20261115-074221-a1b813-passages` |

The passages run is the old behaviour through the same harness. It shows how much repair
varies from run to run on its own. Free RAM stayed above 7.3 GB, and pytest gave 335 passed.

## Totals over the 21 runs

| | Eval as run | passages (old code, re-run) | quote (new code) |
|---|---|---|---|
| Passes the decider overruled | 83 | 84 | 106 |
| Claims removed | 31 | 29 | **24** |
| Claims rewritten | 5 | 6 | 6 |
| Gates passed | 16 | 16 | 16 |

- **Run-to-run spread:** the old code, re-run, matched the eval closely. 16 of 21 runs had
  the same overruled and removed counts. The drop from 29 to 24 removals comes from the new
  check.
- **More overrules, fewer removals:** the new check overrules more passes than the old one
  (106 against 84), but they are different passes. Repair re-cited 14 of the claims from
  another source, and most of the rest kept another supporting check.
- **Gate:** no gate result changed in either run. hyde (local) no longer fails
  `changed_share` (it removes 1 claim instead of 2), but it still fails on `facts`. bm25
  (hybrid) still fails `accuracy_before` and the change caps. That failure comes from the
  checker, not the decider.

## Removals the decider took part in

**On main:** 12 of the eval's removed claims had a decider overrule. Of those, 10 or 11 were
good claims by the labels in `results/decider_probe_2026-11-15/`.

**On this branch:** 5 removals involve the decider.

| Claim | Label |
|---|---|
| SHAP sum caveat (#68 to #70 in the probe) | true, per PR #29 |
| Metadata filters before reranking (#77) | yes |
| "This contrasts with methods like SAGE…" (p 0.50) | not labelled, likely true |
| FlashAttention split-K to split-Q (p 0.47; the checker's reply also failed to parse) | not labelled |
| HPA "applies the formula for each metric" (the checker also gave "partial") | not labelled |

So decider-driven false removals fell from about 10 to about 3 or 4 on this eval.

## Found on the way (not changed here)

- **Ranges and percentages:** two removals came from the hard-fact check, not the decider.
  "25–40% increase in response time" failed because the source lacks "40%", and "8 pods
  averaging 70%" failed because the source lacks "70%". In both cases the checker's own
  reason says the source states the figure, so the source may write ranges or percentages
  another way. Worth checking in `missing_facts`.
- **Uncited claims:** the uncited BM25 formula is still removed, because nothing cites it
  (PR #29's #54).
