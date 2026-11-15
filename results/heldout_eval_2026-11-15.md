# Held-out eval, 2026-11-15

The follow-ups from the eval review (reports/eval-review-2026-11-14.md), run on branch
`claude/project-thread-gmnz0b` (PR #29) at commit c99570c. Run by Claude in a desktop session on
Van's PC, not by Van.

**Read this first.**

- A passing gate means the checker accepted a report. It does not mean every sentence is true.
- The claim labels below are Claude's, not Van's.

## What ran

| Step | Folder | What |
|---|---|---|
| Held-out eval | `runs/eval-20261114-195649-7fd789` | 20 held-out questions × local and hybrid × 1 run, with `--record-search runs/snapshots/heldout`. Started 19:56 on 2026-11-14 (local time), finished 04:14 on 2026-11-15. All 40 runs ended `ok`. |
| Replay check | `runs/eval-20261115-041507-096efc` | bm25, hnsw, kv-cache, cap-theorem and shap, local, 1 run, `--replay-search runs/snapshots/heldout` |
| Claim sample | `results/claim_sample_2026-11-15.csv` | 40 passed and 20 removed claims from the held-out eval and `eval-20261114-050849-f454a6` (the latest eval with a local mode), default seed |
| Rescore | `results/rescore_2026-11-15/` | `python -m assistant rescore` on all 34 eval folders, no models |

Copies of the held-out eval's summary.md, summary.csv and manifest.json are in
`results/heldout_eval_2026-11-15/`.

Setup: llama-swap served Gemma 4 E4B QAT (local tier) and Qwen3.5 9B (backup). Gemini 2.5 Flash
was the cloud tier. SearXNG and Kiwix ran in Docker. Edge and NordVPN were closed before the run;
Steam wasn't running. pytest gave 334 passed.

## Summary table

Gate failure codes are those that `assistant/evaluate.py` writes. Facts is the share of the
question's expected facts found in the report body.

| Question | Mode | Sources | Claims before | Accuracy before | Removed | Claims | Facts | Min free RAM | Gate |
|---|---|---|---|---|---|---|---|---|---|
| bm25 | local | 8 | 16 | 100% | 0 | 16 | 100% | 2882 MB | pass |
| bm25 | hybrid | 17 | 44 | 72% | 11 | 33 | 100% | 3283 MB | fail (accuracy_before changed_share changed_count) |
| hnsw | local | 8 | 16 | 100% | 0 | 16 | 50% | 3589 MB | fail (facts) |
| hnsw | hybrid | 16 | 35 | 94% | 0 | 35 | 100% | 3622 MB | pass |
| flash-attention | local | 8 | 13 | 77% | 2 | 11 | 75% | 3532 MB | fail (accuracy_before changed_share) |
| flash-attention | hybrid | 16 | 29 | 97% | 1 | 28 | 100% | 3670 MB | pass |
| rope | local | 10 | 12 | 100% | 0 | 12 | 50% | 3654 MB | fail (facts) |
| rope | hybrid | 20 | 33 | 97% | 2 | 31 | 100% | 3624 MB | fail (unchecked) |
| kv-cache | local | 9 | 8 | 100% | 0 | 8 | 67% | 3595 MB | fail (facts) |
| kv-cache | hybrid | 23 | 38 | 95% | 2 | 36 | 33% | 3552 MB | fail (facts) |
| speculative-decoding | local | 8 | 18 | 94% | 0 | 18 | 75% | 4041 MB | pass |
| speculative-decoding | hybrid | 20 | 38 | 97% | 2 | 36 | 100% | 4081 MB | pass |
| moe-routing | local | 9 | 13 | 91% | 2 | 11 | 100% | 4012 MB | fail (unchecked changed_share) |
| moe-routing | hybrid | 18 | 33 | 97% | 0 | 33 | 100% | 2941 MB | pass |
| dpo | local | 8 | 20 | 95% | 1 | 19 | 75% | 3929 MB | pass |
| dpo | hybrid | 24 | 49 | 98% | 1 | 48 | 100% | 3956 MB | pass |
| react-pattern | local | 8 | 12 | 100% | 0 | 12 | 100% | 3964 MB | pass |
| react-pattern | hybrid | 25 | 42 | 98% | 2 | 40 | 100% | 3911 MB | pass |
| free-threaded-python | local | 8 | 7 | 86% | 1 | 6 | 25% | 3925 MB | fail (facts) |
| free-threaded-python | hybrid | 23 | 41 | 100% | 0 | 41 | 100% | 3929 MB | pass |
| pandas-arrow | local | 8 | 15 | 100% | 0 | 15 | 67% | 3918 MB | fail (facts) |
| pandas-arrow | hybrid | 21 | 34 | 94% | 0 | 34 | 67% | 3910 MB | fail (facts) |
| polars-lazy | local | 10 | 28 | 82% | 1 | 27 | 100% | 3942 MB | pass |
| polars-lazy | hybrid | 23 | 45 | 100% | 1 | 44 | 100% | 3888 MB | pass |
| cap-theorem | local | 13 | 9 | 78% | 0 | 9 | 100% | 3868 MB | fail (accuracy_before) |
| cap-theorem | hybrid | 24 | 44 | 98% | 1 | 43 | 75% | 3809 MB | pass |
| k8s-hpa | local | 8 | 4 | 100% | 0 | 4 | 75% | 3858 MB | fail (few_claims) |
| k8s-hpa | hybrid | 22 | 37 | 92% | 1 | 36 | 75% | 3843 MB | pass |
| terraform-state | local | 8 | 9 | 100% | 0 | 9 | 100% | 3808 MB | pass |
| terraform-state | hybrid | 16 | 41 | 95% | 2 | 39 | 100% | 3758 MB | pass |
| shap | local | 15 | 11 | 82% | 1 | 10 | 100% | 3792 MB | fail (changed_share) |
| shap | hybrid | 23 | 36 | 94% | 1 | 35 | 100% | 3825 MB | pass |
| k-fold-cv | local | 8 | 7 | 100% | 0 | 7 | 33% | 3779 MB | fail (facts) |
| k-fold-cv | hybrid | 26 | 39 | 95% | 0 | 39 | 67% | 3841 MB | fail (facts) |
| hyde | local | 8 | 13 | 85% | 2 | 11 | 67% | 3728 MB | fail (changed_share facts) |
| hyde | hybrid | 23 | 33 | 97% | 0 | 33 | 100% | 2773 MB | pass |
| cross-encoder-rerank | local | 8 | 5 | 100% | 0 | 5 | 33% | 3274 MB | fail (few_claims facts) |
| cross-encoder-rerank | hybrid | 26 | 35 | 97% | 1 | 34 | 100% | 3218 MB | pass |
| gptq-awq | local | 8 | 8 | 100% | 0 | 8 | 75% | 3325 MB | pass |
| gptq-awq | hybrid | 17 | 27 | 96% | 1 | 26 | 100% | 3384 MB | pass |

The full table (coverage, doubted, rewritten, timings) is in `results/heldout_eval_2026-11-15/summary.md`.

## Pass rates

| Mode | Passed | 95% Wilson interval | Mean facts | Mean accuracy before repair |
|---|---|---|---|---|
| local | 7 / 20 | 18% to 57% | 73% | 93% |
| hybrid | 15 / 20 | 53% to 89% | 91% | 95% |

**Local vs hybrid, paired by question:** 20 pairs.

| | Hybrid pass | Hybrid fail |
|---|---|---|
| **Local pass** | 6 | 1 |
| **Local fail** | 9 | 4 |

The exact McNemar test gives p = 0.0215. Hybrid passes more held-out questions than local, and
the difference is unlikely to be chance at one run per question. The confounds below mean this
compares two whole setups, not two writer models.

Local mostly fails on expected facts: 8 of its 13 failures include `facts`. Two fail
`few_claims` (k8s-hpa with 4 claims, cross-encoder-rerank with 5). Local reports are short,
at 4 to 27 claims after repair (median 11), against 26 to 48 for hybrid.

**Lowest free RAM:** 2773 MB, during the hyde hybrid verify step. Per-run lows were 2773 to
4081 MB. Samples every 15 s are in the run's ram.csv.

### Confounds between local and hybrid

- **Writer model:** local writes with Gemma 4 E4B (4B effective, Q4 on the RTX 3060); hybrid
  writes with Gemini 2.5 Flash.
- **Temperature:** both tiers are set to 1.0, but the research writer uses
  `writer_temperature` 0.3. The two APIs may not treat these settings the same way.
- **Search engine:** local searches through the router (Kiwix, free APIs, SearXNG, with Tavily
  as a fallback); hybrid searches with Tavily inside LDR. Hybrid had over twice as many sources
  per report (16 to 26, against 8 to 15).
- **Judge:** the citation check runs on the local tier (Gemma) in both modes. In local mode,
  Gemma is judging its own reports. The decider is a second local model in both modes.
- **Report length:** hybrid reports have about 3 times as many claims, which helps the facts check and
  never trips `few_claims`. The 15% removed-or-rewritten cap is easier to stay under with more
  claims, but the 5-claim cap is harder (bm25 hybrid failed on both).

## Replay check

The same 5 questions, local only, with searches answered from the held-out recording.

| Question | Snapshot exact / nearest / missed | Sources (run → replay) | Claims | Facts | Gate |
|---|---|---|---|---|---|
| bm25 | 1 / 2 / 1 | 8 → 6 | 16 → 11 | 100% → 100% | pass → pass |
| hnsw | 1 / 3 / 0 | 8 → 8 | 16 → 15 | 50% → 50% | fail → fail |
| kv-cache | 1 / 1 / 2 | 9 → 6 | 8 → 7 | 67% → 33% | fail → fail |
| cap-theorem | 1 / 3 / 0 | 13 → 6 | 9 → 8 | 100% → 75% | fail → pass |
| shap | 1 / 3 / 0 | 15 → 8 | 10 → 13 | 100% → 100% | fail → pass |
| **Total** | **5 / 12 / 3** (of 20 searches) | | | | 3 of 5 match |

The replay passed 3 of 5 (Wilson 23% to 88%); the original run passed 1 of the same 5.

**What it shows:** snapshots remove live web state, but they don't make a rerun repeatable yet.

- Only the first search of each run matched a recorded query exactly. The writer's later
  queries vary, so 12 searches got the nearest recorded query and 3 got nothing.
- Source counts dropped in 3 of 5 questions, and facts changed in 2.
- Gates matched on 3 of 5. The two that flipped (cap-theorem, shap) both went fail → pass.
  The cap-theorem change matches its sources dropping from 13 to 6 and its accuracy before
  repair rising from 78% to 88%.
- Writer and checker sampling also vary run to run (temperature above 0), so some of this
  spread would remain even with every search matched exactly.

## Claim labels

Labeller: **Claude (desktop session), not Van.** For each claim I read the cited sources'
stored text (`full_content` in sources.json), not the checker's quote. I labelled whether the
cited sources support the claim as written, and I was strict: overstating the source counts as
false. Notes for every row are in the CSV.

| Rate | Count | 95% Wilson interval |
|---|---|---|
| False passes (passed, sources don't support it) | 2 of 40 (5%) | 1% to 17% |
| False removals (removed, sources do support it) | 8 of 12 labelled (67%) | 39% to 86% |
| Removed claims labelled unclear | 8 of 20 | |

- **The 2 false passes:** #13 says DPO "avoids" reward hacking where the source says "less
  prone". #35 adds the writer's own guess, "likely a misattribution", to a sourced statement.
  Both are overstatements of a supporting source, not invented facts.
- **The 8 false removals:**
  - Six were claims the decider doubted, which dropped them to "partial" (#44, #45, #49, #50,
    #51, #52).
  - One was the correct BM25 formula, removed for having no citation (#54).
  - One was "k1 and b are the two critical parameters", marked unsupported (#55).
- **The decider:** every decider-doubted removal I could label (6) was true by its sources. The
    one other decider removal (#57) was unclear. The claims it rightly removed (#46, #48, #53)
    were caught by the checker itself, not the decider. With 12 labelled removals, this is a
    lead to check, not a finding.
- **The 8 unclear removals:** uncited hedges, vague sentences, or "the provided sources do not
  contain…" meta statements from offline mode. Including or excluding them changes the
  false-removal rate a lot. The score excludes them.
- **Mode mix:** the passed sample is 26 hybrid, 13 local and 1 offline; the removed sample is
  11 hybrid, 6 local and 3 offline. Labels judge the claim against its cited sources, not
  against the world. A low-quality source that says something (#51, Hackernoon on AWS) still
  counts as support.

## Rescore of earlier evals

All 34 `runs/eval-*` folders with a summary.csv were rescored with no models. Counts are runs
(question × repeat). "Body-only facts" is the old gate with expected facts counted on the
report body. "Current gate" is today's gate. Per-question detail is in
`results/rescore_2026-11-15/<eval id>.md`.

| Eval | Mode | Runs | Passed as run | Body-only facts | Current gate |
|---|---|---|---|---|---|
| [eval-20261025-211144-0cbade](rescore_2026-11-15/eval-20261025-211144-0cbade.md) | local | 2 | 0 | 0 | 0 |
| [eval-20261025-211144-0cbade](rescore_2026-11-15/eval-20261025-211144-0cbade.md) | hybrid | 2 | 1 | 0 | 0 |
| [eval-20261025-214615-69cccf](rescore_2026-11-15/eval-20261025-214615-69cccf.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261025-214615-69cccf](rescore_2026-11-15/eval-20261025-214615-69cccf.md) | hybrid | 1 | 0 | 0 | 0 |
| [eval-20261025-215911-b4cf54](rescore_2026-11-15/eval-20261025-215911-b4cf54.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261025-215911-b4cf54](rescore_2026-11-15/eval-20261025-215911-b4cf54.md) | hybrid | 1 | 0 | 0 | 0 |
| [eval-20261025-224352-fc6ac6](rescore_2026-11-15/eval-20261025-224352-fc6ac6.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261025-224352-fc6ac6](rescore_2026-11-15/eval-20261025-224352-fc6ac6.md) | hybrid | 1 | 0 | 0 | 0 |
| [eval-20261104-190331-1c46cc](rescore_2026-11-15/eval-20261104-190331-1c46cc.md) | local | 2 | 2 | 0 | 0 |
| [eval-20261104-190331-1c46cc](rescore_2026-11-15/eval-20261104-190331-1c46cc.md) | hybrid | 1 | 1 | 0 | 0 |
| [eval-20261105-062326-3164b5](rescore_2026-11-15/eval-20261105-062326-3164b5.md) | local | 5 | 0 | 0 | 0 |
| [eval-20261105-062326-3164b5](rescore_2026-11-15/eval-20261105-062326-3164b5.md) | hybrid | 5 | 1 | 0 | 0 |
| [eval-20261105-113425-2ae5af](rescore_2026-11-15/eval-20261105-113425-2ae5af.md) | local | 5 | 5 | 3 | 3 |
| [eval-20261105-113425-2ae5af](rescore_2026-11-15/eval-20261105-113425-2ae5af.md) | hybrid | 5 | 5 | 5 | 4 |
| [eval-20261108-103541-396170](rescore_2026-11-15/eval-20261108-103541-396170.md) | local | 10 | 7 | 6 | 4 |
| [eval-20261108-103541-396170](rescore_2026-11-15/eval-20261108-103541-396170.md) | hybrid | 10 | 8 | 8 | 7 |
| [eval-20261108-103541-396170](rescore_2026-11-15/eval-20261108-103541-396170.md) | codex | 6 | 3 | 3 | 1 |
| [eval-20261111-133711-517cfa](rescore_2026-11-15/eval-20261111-133711-517cfa.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261111-133711-517cfa](rescore_2026-11-15/eval-20261111-133711-517cfa.md) | hybrid | 1 | 1 | 1 | 1 |
| [eval-20261111-134835-f68130](rescore_2026-11-15/eval-20261111-134835-f68130.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261112-092026-ccf6dc](rescore_2026-11-15/eval-20261112-092026-ccf6dc.md) | local | 10 | 6 | 5 | 5 |
| [eval-20261112-092026-ccf6dc](rescore_2026-11-15/eval-20261112-092026-ccf6dc.md) | hybrid | 10 | 10 | 9 | 9 |
| [eval-20261112-130518-48feeb](rescore_2026-11-15/eval-20261112-130518-48feeb.md) | codex | 10 | 5 | 4 | 4 |
| [eval-20261112-141054-50867a](rescore_2026-11-15/eval-20261112-141054-50867a.md) | codex | 2 | 0 | 0 | 0 |
| [eval-20261112-143522-7d19eb](rescore_2026-11-15/eval-20261112-143522-7d19eb.md) | codex | 6 | 1 | 1 | 0 |
| [eval-20261112-151724-e6e2b7](rescore_2026-11-15/eval-20261112-151724-e6e2b7.md) | local | 10 | 8 | 8 | 6 |
| [eval-20261112-172643-359e26](rescore_2026-11-15/eval-20261112-172643-359e26.md) | codex | 10 | 3 | 3 | 3 |
| [eval-20261112-191911-01cb9e](rescore_2026-11-15/eval-20261112-191911-01cb9e.md) | codex | 10 | 7 | 7 | 7 |
| [eval-20261113-053056-77573d](rescore_2026-11-15/eval-20261113-053056-77573d.md) | local | 7 | 1 | 1 | 1 |
| [eval-20261113-054817-aa4ca2](rescore_2026-11-15/eval-20261113-054817-aa4ca2.md) | local | 2 | 0 | 0 | 0 |
| [eval-20261113-061557-edb4fe](rescore_2026-11-15/eval-20261113-061557-edb4fe.md) | local | 10 | 0 | 0 | 0 |
| [eval-20261113-105431-7e40fd](rescore_2026-11-15/eval-20261113-105431-7e40fd.md) | local | 10 | 2 | 2 | 2 |
| [eval-20261113-111544-1bf0c5](rescore_2026-11-15/eval-20261113-111544-1bf0c5.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261113-111655-9783d8](rescore_2026-11-15/eval-20261113-111655-9783d8.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261113-111814-cf6345](rescore_2026-11-15/eval-20261113-111814-cf6345.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261113-111835-9b620c](rescore_2026-11-15/eval-20261113-111835-9b620c.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261113-112024-835461](rescore_2026-11-15/eval-20261113-112024-835461.md) | local | 1 | 0 | 0 | 0 |
| [eval-20261113-112317-4eded9](rescore_2026-11-15/eval-20261113-112317-4eded9.md) | local | 1 | 1 | 1 | 1 |
| [eval-20261113-113015-8f7ed7](rescore_2026-11-15/eval-20261113-113015-8f7ed7.md) | local | 10 | 9 | 7 | 7 |
| [eval-20261113-181842-23dc77](rescore_2026-11-15/eval-20261113-181842-23dc77.md) | codex | 2 | 2 | 2 | 1 |
| [eval-20261113-183211-75c5f1](rescore_2026-11-15/eval-20261113-183211-75c5f1.md) | codex | 10 | 10 | 7 | 4 |
| [eval-20261113-191737-4644aa](rescore_2026-11-15/eval-20261113-191737-4644aa.md) | local | 10 | 7 | 7 | 6 |
| [eval-20261113-191737-4644aa](rescore_2026-11-15/eval-20261113-191737-4644aa.md) | offline | 10 | 0 | 0 | 0 |
| [eval-20261113-201715-fce487](rescore_2026-11-15/eval-20261113-201715-fce487.md) | local | 10 | 6 | 4 | 4 |
| [eval-20261113-201715-fce487](rescore_2026-11-15/eval-20261113-201715-fce487.md) | offline | 10 | 2 | 0 | 0 |
| [eval-20261114-050849-f454a6](rescore_2026-11-15/eval-20261114-050849-f454a6.md) | local | 10 | 8 | 5 | 4 |
| [eval-20261114-050849-f454a6](rescore_2026-11-15/eval-20261114-050849-f454a6.md) | offline | 10 | 2 | 1 | 1 |
| [eval-20261114-062621-6e2c0d](rescore_2026-11-15/eval-20261114-062621-6e2c0d.md) | hybrid | 10 | 10 | 10 | 8 |
