# Eval review — 2026-11-14

This is a read-only review of Stage 2's model evaluations and their tasks, done by the `ecc:mle-reviewer` agent.

- **Nothing was changed or run on models:** no files were edited, no models were run, the bench code suite was not run, and no file with "key" in its name was opened.
- **Main-session check:** after the agent finished, the main session read the code again for finding 1 and the closing-report claims, and recomputed the Fisher p-value in finding 3.
- **Confirmed** means checked in the code or data; **suspected** means not shown.
- **What "10 per gate" means:** 5 questions × 2 runs, not 10 independent items. The same 5 questions have been used since 2026-10-26.
- **Passing verify:** a passing `assistant verify` means the checker accepted a report, not that every sentence is true. No number here measures truth.

## Ranked findings

### 1. The expected-facts check scores the whole report, including the question title and the Sources list (confirmed)
- **Where:** `assistant/evaluate.py:68-73` (`facts_found`) is run on the full `report.md` at `:168` and `:183`.
  - The report's H1 is the question text, and `## Sources` holds source titles and URLs.
- **gguf-quants gets most facts for free:** the question text (`evals/research_questions.toml:36`) contains `Q4_K_M`, `Q8_0` and `quality`. So 3 of its 4 facts (`:37`) match for free, and the 75% facts gate can never fail on that question.
- **Other free matches:** `retriev` comes from the self-rag question text, and `3.15` from source titles.
- **Re-scored on the body only:** the agent stripped the title and Sources from the saved reports and scored again.

| Run | Reported | Body only |
|---|---|---|
| local + Tavily, 11-13 | 9/10 | 7/10 (both gguf-quants runs fall to 50%) |
| local, Tavily off, 11-14 | 8/10 | 6/10 (both self-rag runs fall to 67%) |
| codex, 11-13b | 10/10 | 9/10 |
| local, 11-12 morning | 6/10 | 5/10 |
| hybrid (both dates) | 10/10 | 10/10 |

- **Why it matters:** it inflates every "latest run" number that the closing report's recommendation rests on.
- **Smallest fix:** score facts on the body only (drop line 1 and everything from `## Sources` on), then re-score the saved `runs/*/report.md` files. This needs no model time.

### 2. The gate and checker were tuned on the same 5 questions after each failure (confirmed)
- **What changed, in commit order:**
  - the gate was made stricter (6020abf, 11-08);
  - the decider threshold of 0.8 was set from claims taken from the 11-12 eval reports (ff75879; 88 claims, one labeller);
  - Python and GGUF primary sources were added to the codex after those questions failed (eef1c40);
  - GGUF-specific fixes landed, plus 2 decide cases built from eval failures (4affeb0);
  - the checker started reading source abstracts (ea064e6), citing "8 of 9 removed claims in the last gate run";
  - the GitHub back-off was added (667e4b1), citing the aws-pillars query.
- **Effect of the new checker:** `stage2_eval_2026-11-14.md:79-89` re-verifies the failed reports with it, and removals fall from 3 to 1, 3 to 0 and 3 to 0.
- **Why it matters:** the Tavily-off sequence 7 → 6 → 8 (closing report line 54) comes from code revised on the evaluation set. The final 8/10 partly measures the fixes. No held-out questions exist.
- **Smallest fix:** add 10–15 questions that were never looked at, freeze the code and checker, run once, and report old-checker and new-checker numbers side by side.

### 3. "Local 6/10 vs hybrid 10/10" is weak evidence, and the latest runs show no gap (confirmed)
- **Confidence intervals:**
  - 6/10: Clopper-Pearson 95% CI 0.26–0.88 (Wilson 0.31–0.83).
  - 10/10: 0.69–1.00 (Wilson 0.72–1.00).
- **Significance tests:**
  - Fisher exact, 6/10 vs 10/10: p = 0.087.
  - Paired by (question, run), McNemar exact on 4 discordant pairs (4:0): p = 0.125.
  - By question (3 of 5 differ): sign test p = 0.25.
- **Runs are not independent:** the two runs reuse the same 5 questions, and the failures cluster in self-rag and lora-vs-qlora.
- **Later comparisons show no measurable gap:**
  - local + Tavily 9/10 vs hybrid 10/10: p = 1.0.
  - The Tavily-off runs pooled give 21/30 = 70% (CI 0.51–0.85).
  - 8/10 vs 9/10: p = 1.0.
- **The comparison is confounded:**
  - writer: Gemma E4B (temperature 0.3) vs Gemini 2.5 Flash (temperature 1.0);
  - search: SearXNG and the router vs Tavily;
  - judge: Gemma E4B judges its own local reports.
- **Sample sizes needed:**
  - About 20 independent questions per arm detect 60% vs 95% with ~80% power.
  - About 60 per arm are needed to separate 7/10 from 9/10.
  - About 30 consecutive passes show a pass rate of at least 90% with 95% confidence.
  - Repeat runs of the same 5 questions add little.
- **Smallest fix:** use 20–30 distinct questions, run each once, and score with a paired or clustered test.

### 4. The accuracy and coverage gates are close to vacuous (confirmed)
- **Repair masks accuracy:** repair deletes failing claims, so accuracy and coverage after repair are ~100% by construction.
- **What actually gates:** only `removed_share <= 15%` (`evaluate.py:182-186`) plus the facts check. `accuracy_before` is never gated. Hybrid python-315 r2 passed with 69% accuracy before repair (2 of 15 claims removed).
- **The cap is a share, not a count:**
  - a short report fails on one removal (1 of 6 = 17%), while a long one passes with six (6 of 65 = 9%);
  - rewritten and re-cited claims don't count toward the cap.
- **No minimum claim count:** passing reports exist with 1 claim (offline gguf-quants r2), 4 (codex aws-pillars r2) and 5 (local aws-pillars r2).
- **Smallest fix:**
  - gate on `accuracy_before`, and cap removals by count as well as share;
  - require a minimum number of claims;
  - count rewrites toward the cap.

### 5. Fact matching is loose and checks presence, not correctness (confirmed)
- **Common substrings count as facts:** `security`, `reliability`, `quality` and `retriev` (`research_questions.toml:28, :37, :45`). A report can contain the keyword in a wrong or removed claim and still score.
- **python-315 depends on the date:** the question asks for the "latest stable" version. The offline Kiwix dump (2026-06) can't contain 3.15, so offline python-315 scores 0% by construction, which says nothing about the model.
- **Expected facts not verified:** the facts themselves (3.15, lazy import, UTF-8) were not checked against primary sources.
- **Smallest fix:**
  - use word-boundary or multi-word phrases;
  - check facts in the relevant section of the report;
  - pin the question to "Python 3.15".

### 6. Results depend on web state nobody controlled (confirmed, partly)
- **Search engines were unstable:**
  - SearXNG engines were suspended on 11-12 and 11-13;
  - the 11-12 afternoon rerun got 0 sources for self-rag;
  - on 11-13, 99 of 102 sources were from Tavily;
  - the 11-14 second run got about 5 SearXNG sources against 38 in the first run.
- **The cache wasn't versioned:** the cache key had no version until ad27c8a, and the second run was clean only because the cache was moved by hand (`stage2_eval_2026-11-14.md:5`).
- **The 7/6/8 spread can't be attributed:** the three Tavily-off runs differ in code as well as in web state.
- **One change was never explained:** after the checker was made more lenient, hybrid accuracy-before fell from 97% to 91% and removals rose from 1% to 6% (closing report line 59).
- **Smallest fix:** save a snapshot of search results per question and replay it, so checker or writer changes are tested without web noise.

### 7. Reproducibility is thin (confirmed)
- **What `summary.csv` doesn't record** (`evaluate.py:52-56`): git SHA, model name or quant, GGUF hash, temperature, seed, context size, and search snapshot. These exist only in hand-written `.md` notes.
- **Runs can't be replayed:**
  - research runs take no seed;
  - `runs/` is git-ignored, so the committed tables are the only record;
  - `gemini-2.5-flash` is an unpinned alias.
- **Smallest fix:** write a `manifest.json` per eval (SHA, tier config, GGUF sha256, env, cache version) into the run folder and copy it to `results/`.

### 8. The checker's reliability is under-measured (confirmed)
- **No false-pass estimate:** the 11-14 re-verify sections show false removals (8 of 9) and the decider doubting a correct Q8_0 claim at p = 0.04. Nothing estimates how often false claims pass.
- **The gate ignores unchecked claims:**
  - repair keeps "unchecked" claims (`repair.py:170, :245`);
  - accuracy leaves them out of its denominator (`verify.py:136`);
  - the eval gate (`evaluate.py:184`) never reads them;
  - review #1's `failures()` is used only by the CLI exit code.
- **Past results are unaffected:** all 7 main evals have unchecked = 0 in their final verification.json, so this gap is in the gate only.
- **Smallest fix:**
  - have the eval gate call `failures()`, or require `unchecked == 0`;
  - hand-label a random sample of passed claims to estimate the false-pass rate.

### 9. The review-commit changes don't alter past numbers (confirmed)
- **review #1 (f48b8d2):** changes only the CLI exit code, not the eval gate.
- **review #10 (8571e1c):** all 5 questions are `public = true`, so nothing changes for past runs. Any future question without that flag will silently skip hybrid.

### 10. `tests/test_evaluate.py` is not hermetic (confirmed)
- **A test reads this PC's real codex:** `test_each_question_runs_twice_codex_only_where_marked_and_ram_is_logged` (`:192`, assertion `:206`) failed here. It picks up the real codex index at `~/Desktop/local-ai-codex` and gets "ok" where it expects "not_run".
  - Result: 1 failed, 40 passed across `test_evaluate.py` and `test_code.py`.
- **Missing tests:** one that scores facts on the body only, and one that rejects a pass with an unchecked claim.
- **Smallest fix:** set `codex_dir` to `tmp_path` in `settings_for`, and add the two missing tests.
- **Full suite not run:** the 289-test run didn't finish, because of a Windows temp-dir permission error (`pytest-of-vanc8`).

### 11. `evals/decide_cases.toml` has a skewed label balance (confirmed)
- **Most noul cases expect "true":** 11 of 15 do, so an always-true answerer scores 73% on that kind.
- **Two cases came from eval failures:** the ones added in 4affeb0 were tuned on the eval.
- **One label is ambiguous:** `label-06`, as the 11-08 results note itself says.
- **Smallest fix:**
  - balance true and false;
  - relabel or drop `label-06`;
  - keep tuned cases in a separate dev split.

### 12. Bench suites (confirmed)
- **code_editing:**
  - The 10 tasks are fair; `tests/test_code.py:14` makes sure each starter fails and each solution passes.
  - The tests are visible to the model and the failures are fed back, so this measures "make the visible tests pass". It may saturate before Stage 3's "6 of 10" gate.
  - Task 03's tuple input is shown only in the tests, not in task.md.
  - Task 06 leaves the KeyError/ValueError order unspecified.
- **research (`research_sets.json`):**
  - 5 numeric items, with regexes like `\b48\b` that match any occurrence.
  - Citing every source in every sentence passes the source check.
  - Length is reported but not gated.
- **tool_calling and tool cases:**
  - The 30 cases agree with the world data.
  - `create_event` has no exact-count check in single-07, args-01 or recover-04, so duplicate events pass.
  - notool-03's `\btest` matches almost anything.
  - Final-answer regexes match a bare number anywhere.
- **vision:**
  - bar-highest rejects "South, not North".
  - line-peak doesn't reject "May".
  - The terminal regex accepts "17" anywhere.
  - All items are flagged for manual review, but the score is automatic only.
- **speed and headroom:**
  - These are measurements, not pass/fail tests.
  - The 4.2–5.0 GB free-RAM numbers were taken with Edge and NordVPN open.

## Claims in `results/stage2_closing_report.md` the data doesn't support

| Line | Claim | Problem |
|---|---|---|
| 9, 33 | "local passes 9 of 10 with Tavily" | Tavily supplied 99 of 102 sources (line 58), so this is mostly Gemma writing from Tavily. Body-only facts give 7/10. |
| 3, 11 | "fell just short of its own gate" / "8 of 10" | 8 is the best of 7/6/8, each with different code. Pooled 21/30 = 70%. Body-only gives 6/10 for the last run. |
| 11 | "none came from a commercial search service" | 12 of 79 sources came through SearXNG, which queries Google, Brave and DuckDuckGo. Line 58's "any keyed service" is accurate. |
| 43 | gguf-quants "had failed in every earlier run" | The earlier gguf-only codex run (11-13) passed 2 of 2. Body-only facts drop r2 to 50%. |
| 49–50 | History table | It mixes questions (5) with reports (10), and leaves out that the same-day 11-05b repair run passed 10/10 under a 25% cap. The gate changed (25% → 15%, facts added) between rows, so the rows aren't comparable. |
| 67 | "Local reports have 9 to 25 claims, codex 6 to 25" | Local Tavily-off reports have 5–20 claims, and codex aws-pillars r2 has 4. |
| 68 | "One run in ten loses a little too much ... noise, not a pattern tied to one question" | Removal failures cluster on gguf-quants and lora-vs-qlora. In the three Tavily-off runs, 4 of 10 failures were removal-driven. |
| 13, 59 | "no condition remains before sign-off" | The rise in hybrid removals was attributed to "could be" causes and never tested. |

The caveat at lines 15 and 60, that a pass is a checker result, is correct. But the report still says "meets", as if that were measured.

## Verdict

**The engineering conclusions can be trusted:**
- the pipeline runs end to end;
- free RAM stays above 4 GB;
- the privacy gate is default-deny;
- the closing report is candid about the checker's limits.

**The numbers can't be trusted:**
- 6/10, 8/10, 9/10 and 10/10 are within noise of each other;
- the facts metric is inflated by the title and Sources list;
- the gate and checker were revised on the same 5 questions.

Read Stage 2 as "all three modes produce plausible, mostly checker-clean reports on 5 easy public questions". It is not evidence that hybrid beats local, or that local meets a 9/10 gate.

## Top 3 fixes before Stage 3
1. **Fix scoring:** score facts on the body only; add `unchecked == 0` and a minimum claim count to the gate; re-score all saved reports. This is cheap and needs no models.
2. **Build a held-out set:** add 15–25 unseen questions, freeze the code and run once, and compare local and hybrid with a paired test. Add a per-run manifest and a search-result snapshot so web state stops changing runs.
3. **Measure the checker:** hand-label 50–100 random passed and removed claims to get real false-pass and false-removal rates. Keep the tuned threshold and decide cases in a separate dev set.

## Not checked
- **Truth of the reports:** whether any claim in any report is actually true. One report read (lora-vs-qlora Tavily-off r1) had all 8 claims supported but a weak answer.
- **Expected facts:** not checked against primary sources.
- **Tests:** the full test suite (stopped by the temp-dir error); only `test_evaluate.py` and `test_code.py` ran.
- **Other code and data:**
  - `bench/results`, the codex catalog and index;
  - the `verify.py` prompts, `research.py` and `sources.py`;
  - `bench.toml` and the 11-08 RAM log.
- **Re-scoring caveat:** the body-only figures come from re-scoring the saved `report.md` files, which could differ slightly from the live eval if those files were edited after the run.
- **Key file:** `Google Gemini API Key.txt` sits in the repo root. It was not opened; it is git-ignored (`.gitignore:15`) and untracked, so nothing leaked. But it goes against CLAUDE.md's "keys come from environment variables" rule.
