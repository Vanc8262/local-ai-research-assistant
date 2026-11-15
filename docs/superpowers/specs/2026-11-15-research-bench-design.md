# Research bench: a frozen, refereed Stage 2 eval — design

Status: APPROVED by Van, 2026-11-15
Thread: "Settle a stable Stage 2 eval gate"

## 1. Intent

Two problems, one fix.

1. **The Stage 2 gate keeps looping.** Since 2026-10-26, almost every failed eval led to a change in
   the checker or the gate, tested on the same five questions. Rescoring the saved runs under today's
   gate drops earlier passes (for example the 2026-11-14 local run falls from 8/10 to 4/10). On the
   20 held-out questions, local passes 7/20 and hybrid 15/20. Claude's hand labels show the checker
   rarely lets a bad claim through (2 of 40) but often removes good ones (8 of 12). Today the gate
   mixes two questions: "is the checker right?" and "is the pipeline's report good?"
2. **Van wants to reuse the eval to choose models.** He wants to point the eval at another local
   model and learn which one works best **in his local pipeline on his PC** (RTX 3060 12 GB, 16 GB
   RAM). He doesn't want to know which model is best in general.

The fix is a **research bench**. It is a versioned, frozen eval in which a candidate model runs the
whole local pipeline, and a fixed, pinned local **referee** scores the final reports. Passing Stage 2
becomes one result on that bench: "the chosen default meets the pass line on bench v1."

## 2. Decisions Van made (2026-11-15)

- The candidate runs the **full pipeline as in daily use**: search, write, check, repair.
- The goal is **fit for his pipeline and PC**, not a general model ranking.
- **Report quality ranks first; speed only breaks ties.**
- Approach **A**: extend the existing `assistant eval`, don't build a separate harness.
- **Borrow the fact-checking method** from DeepResearch Bench (its FACT part).
- The referee is a **pinned local model**.
- **A candidate is the model file plus the pipeline commit and its settings.** After a test-split
  miss, a changed pipeline is a new candidate, but each bench version allows **at most 2 retests**
  of a candidate after its first miss. If it still misses, the result stands: local mode doesn't
  meet the bar on that version, and Van decides whether to accept it or move to a new version.
- **Who does the hand work:** Claude labels the calibration pairs and Van spot-checks a random 25.
  A fresh session that has seen no eval results drafts the test questions, and Van approves them
  before they are committed.

## 3. Goals, non-goals, anti-goals

**Goals**
- One command scores a candidate on a frozen bench and writes a scorecard row that can be compared
  with earlier rows from the same bench version.
- Stop the loop: rules for when the bench may change, written down and enforced in code where possible.
- Close Stage 2 on a number that won't move when the code is later tidied up.

**Non-goals**
- A public leaderboard, or a comparison with frontier or cloud models. Hybrid mode stays as a
  reported reference row, not a candidate.
- Scoring other pipelines (plain LDR, other engines).
- Running the DeepResearch Bench tasks themselves.

**Anti-goals** (failure even if it technically works)
- Tuning the pipeline, the referee or the scoring on the test questions.
- Ranking models on differences smaller than the noise.
- A referee that silently changes: a new GGUF, a new prompt or new settings without a new bench version.
- A score that the pipeline's own checker can raise by deleting claims.

## 4. Approaches considered

- **A. Add a referee step and a frozen bench spec to `assistant eval` (chosen).** This reuses the
  manifest, `rescore`, search record and replay, Wilson intervals and paired tests. It is tied to
  this pipeline, which is what Van wants.
- **B. A separate black-box bench module.** It could score any pipeline, but that flexibility answers
  a question Van said he doesn't care about, and it means more new code.
- **C. Adopt a public benchmark (DeepResearch Bench, LiveResearchBench).** Its tasks are PhD-level
  and long, it needs frontier judges, and it says nothing about fit on this PC. Only its FACT method
  is borrowed (section 6.2).

## 5. How it fits the current code

- `assistant eval` runs `run_research`, `verify_run` and `repair_run` per question and mode, then
  scores with `GATE_*` in `assistant/evaluate.py`. It already writes `manifest.json`, `ram.csv` and
  per-run folders, and supports `--record-search`/`--replay-search`.
- The local tier (`[tiers.local]` in `assistant.toml`) is the writer, the checker and the decider.
  Swapping the candidate into that tier therefore swaps the whole local pipeline, as Van asked.
- `assistant rescore` already re-scores saved evals without models. The referee pass works the same
  way: it runs after the pipeline, on saved run folders.
- `assistant claims sample/score` already produces labelled-claim CSVs and Wilson rates. The
  referee's calibration reuses it.

## 6. Design

### 6.1 The bench spec (`evals/bench_v1.toml`)

One file freezes everything a score depends on:

- `version = 1` and a frozen date;
- question files and which split each question is in (`dev` or `test`);
- the referee: GGUF file name, sha256, llama-server flags, sampling settings, prompt version;
- fit limits (section 6.4);
- the Stage 2 pass line (section 6.6);
- runs per question (1 by default) and the search mode (`searxng` router; Tavily fallback off).

**Search on the test split (Claude's call).** Search is live, as in daily use, because each model
asks its own queries and the recorded-search replay only matches the nearest query. To keep the web
from drifting between candidates, all candidates in one round run back to back within the same
three days, and every run records its searches for later diagnosis. The hybrid reference row keeps
Gemini plus Tavily. It is a reference only and isn't ranked or gated.

`assistant eval --bench evals/bench_v1.toml --candidate <tier>` refuses to run unless the referee GGUF
hash and the prompt version match the spec. It stamps the bench version on every output. The scorecard
only puts rows with the same bench version side by side.

### 6.2 The referee (FACT-style, borrowed from DeepResearch Bench)

The referee ignores the pipeline's own claim split and verdicts. For each final `report.md`:

1. **Extract.** Pull every (statement, cited source) pair from the report body. The referee model
   does this, with a deterministic markdown pass for the citation markers. Merge duplicate pairs.
   Each statement is a **verbatim span** of the report (Claude's call, from the builder check), so
   fact matching in step 4 runs on the report's own words, not on a rewording.
2. **Fetch.** Use the source text the run already stored (`sources.json` `full_content`, or the run
   cache). Never search the web again. A citation to a missing source counts as unsupported.
3. **Judge.** For each pair, ask "does this source support this statement as written?" and get
   `supported`, `not_supported` or `unclear`. The referee may reason before answering (thinking on),
   unlike the one-letter decider whose missing reasoning caused the false removals. It must quote the
   supporting text, and the quote is checked against the source by the existing `quote_in_source`
   matcher. A `supported` verdict whose quote doesn't match becomes `unclear`.
4. **Facts.** For each of the question's `expect` groups, the fact counts as **found** only if it
   appears in a statement the referee marked `supported`.

The referee makes no edits and writes `referee.json` into the run folder. It is deterministic
(temperature 0, fixed seed) and can be re-run with no candidate model loaded.

**Picking and pinning the referee model.** The referee runs alone after the pipeline, so it can use
the whole 12 GB of VRAM. The candidates are the models already on the PC, starting with Qwen3.5 9B
Q4_K_M and Gemma 4 12B QAT, plus Granite 4.2 8B (if its GGUF is still in D:\models), which is from neither candidate family. Each runs the referee task on the **calibration set** (section 6.3). The
one with the best balanced error (it gets both kinds of error low, not just one) is pinned in
`bench_v1.toml`. When a candidate under test is the same model family as the referee, the scorecard
flags that row.

If no model reaches the minimums in 6.3: keep working on the prompt against the calibration set,
then try a larger quant (Q6/Q8) of the best one. If that still fails, stop and bring the numbers to
Van. The bar is never lowered quietly (Claude's call).

### 6.3 Calibration set (the referee's own test)

- About 150 statement–source pairs taken from **dev-split runs only**, so the referee is never tuned
  on test questions. Sampled to include supported, unsupported and borderline pairs (overstatements,
  table values, uncited hedges).
- Each pair is hand-labelled `supported`, `not_supported` or `unclear` against the stored source,
  with the strict rule already used (overstating the source counts as not supported). Claude labels
  all of them. Van spot-checks a random 25 before the freeze. If he disagrees with more than 3 of the
  25, the labelling rule is revisited before anything is frozen.
- The referee must reach agreed minimums before bench v1 is frozen (proposed: at least 90% recall on
  `supported` and at least 85% on `not_supported`, with Wilson intervals). Its measured error rates
  are printed on every scorecard, so a score is never read as truth.
- Prompt work on the referee happens **only** against the calibration set, before the freeze.

### 6.4 Fit limits (pass/fail, checked first)

A candidate fails fit if any of these hold on any test-split run:

- it doesn't load fully on the GPU at the pipeline's context (32K);
- free system RAM falls below **2.0 GB** (from `ram.csv`; the lowest seen on the held-out run was 2.8 GB);
- a single report takes more than **60 minutes** end to end;
- more than **10%** of runs end in a pipeline error rather than a report.

These numbers are proposals for Van to adjust before the freeze.

### 6.5 Quality score and ranking

For each candidate, over the test split:

- **Facts right**: the mean, per question, of the share of `expect` groups found in referee-supported
  statements.
- **Errors through**: the share of all final statements the referee marks `not_supported`.
  `unclear` is reported but not counted.

Ranking:
1. Candidates that fail fit are listed, but not ranked.
2. Rank by **facts right**, then by **errors through**.
3. **Tie rule:** two candidates are tied on a measure if a paired comparison by question (bootstrap
   over questions, 95%) can't separate them. When tied on both measures, the faster one (median
   minutes per report) ranks higher.

The pipeline's internal numbers (claims removed, accuracy before repair, decider overrules) are
logged and shown in a details table, **not** scored. Their effect already shows in the final report.

### 6.6 The Stage 2 pass line

Stage 2 passes when the default local setup, on bench v1's test split, passes fit and the referee
scores at least:

- **facts right ≥ 75%** (the expected-facts bar used since 2026-11-08), and
- **errors through ≤ 5%** (the old 95% accuracy bar, now measured by the referee on the final report).

These are proposals. Van sets the final values in `bench_v1.toml` before any test-split run, and
they don't change afterwards for v1.

### 6.7 Questions and splits

- **Dev split**: the 5 original questions and the 20 held-out questions. Both have been looked at, so
  they are now for tuning the pipeline and building the calibration set.
- **Test split**: about **25 new questions**, written before any test run. They are drafted by a
  fresh session that has seen no eval results or checker behaviour, then reviewed and approved by
  Van. They cover Van's codex
  topics (Python, software engineering, cloud, ML, AI, LLM agents, RAG, local models, data science),
  don't use the codex, are all `public = true`, and use the same `expect` conventions. Their `expect` groups are reviewed
  for keyword leaks (no fact word in the question text). The file is committed before the first run.
- About 25 questions gives roughly 80% power to see a 25-point pass-rate gap. Smaller gaps will show
  up as ties, by design.

### 6.8 Rules that stop the loop

These go in the bench spec's header and the README:

1. **Pipeline changes are free on dev, not on test.** Anyone can change the pipeline (checker, decider,
   writer prompts, PR #30) and measure it on the dev split. A test-split score is taken once per
   candidate per bench version, where a candidate is the GGUF hash plus the pipeline commit plus the
   tier settings. After a miss, at most 2 retests per bench version. The scorecard lists every test
   run, including misses, and the code refuses a third retest.
2. **Bench changes need proof and a new version.** The referee, its prompt, the scoring or the test
   questions change only when hand-labelled examples show a real grading error (proposed: at least 5
   labelled cases of the same error). The change creates bench v2, and v1 scores are not compared with v2.
3. **One round at a time.** A comparison round (several candidates) runs on one frozen bench version
   from start to finish. Nothing changes mid-round.
4. **A broken task is fixed, not argued about.** A test question that every candidate scores 0 on,
   or whose `expect` group is wrong, is logged and goes into the next version. It isn't edited in place.

### 6.9 Outputs

- `runs/eval-<id>/referee.json` per run; `scorecard.md` and `scorecard.csv` per eval.
- `results/bench_v1_scorecard.md`: one row per candidate (bench version, candidate GGUF and hash,
  fit, facts right with CI, errors through with CI, median minutes, flags), plus the referee's
  calibration rates.
- Every scorecard repeats the CLAUDE.md caveat: a referee pass means the referee accepted the report,
  not that every sentence is true.

## 7. Constraints

- $0 a month. Everything runs on the PC; no cloud judge.
- Test questions are public, so hybrid can run as a reference row. Private prompts never reach a
  cloud tier (unchanged).
- RAM: the referee runs after the pipeline with the candidate unloaded (llama-swap group swap).
- Time: a 25-question local test run is about 5 hours plus the referee pass. Plan runs overnight.
- Only Van picks models for `vault.toml`. A bench result never adds a model there.
- The bench `code` suite is unrelated and stays off unless asked.

## 8. Left to the builder

- Exact referee prompt and extraction format, within section 6.2.
- How the calibration pairs are sampled, within section 6.3.
- The bootstrap details and number of resamples.
- How `--candidate` maps onto tiers and llama-swap model entries (for example a `[tiers.candidate]`
  block plus a llama-swap entry per GGUF).
- Whether the existing `GATE_*` pass/fail stays as a secondary "pipeline health" column.

## 9. Order of work (for the plan)

1. Bench spec file, `--bench` and `--candidate` flags, bench version stamping.
2. Referee extraction and judging on saved run folders; `assistant referee runs/eval-<id>`.
3. Calibration set from dev runs, Claude's labelling, Van's 25-pair spot-check, referee model pick.
4. Test questions drafted by a fresh session, approved by Van, committed; freeze bench v1.
5. Scorecard and tie rule.
6. First round: the default (Gemma 4 E4B) and the backup (Qwen3.5 9B), then Stage 2 sign-off against the pass line.
