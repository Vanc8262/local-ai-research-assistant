# Stage 2 closing report: Research

**Status:** ready for Van's approval (updated 2026-11-14, after the hybrid rerun). The fully local search work Van asked for on 2026-11-13 is done. It fell just short of its own gate, so, as Van chose on 2026-11-14, Stage 2 closes with Tavily kept as a fallback in local mode.
**Date:** 2026-11-14
**Code:** PRs #23 and #24 are merged. PR #25 (the local search router and the GGUF codex fixes) merged on 2026-11-14. On main, `pytest` gives 289 passed.

## Recommendation

Close Stage 2. Stage 2 asked for cited reports in which every claim traces back to a source. Local, hybrid and codex modes all meet that on their latest runs: local passes 9 of 10 with Tavily, and codex passes 10 of 10. The GGUF codex failure from the earlier draft is fixed.

Local mode works without Tavily, but less well. A run with Tavily switched off passed 8 of 10. Most of its sources came from offline copies and free public APIs, and none came from a commercial search service. That is one short of the 9 of 10 that the local search design set as its gate. Tavily therefore stays as a documented fallback in local mode (caveat 1).

The hybrid rerun on the current main passed 10 of 10 on 2026-11-14, so no condition remains before sign-off.

A pass means the checker accepts what is left of a report. It does not mean every sentence is true. The checker's model is sometimes generous, so hand review is still worthwhile.

## Gate

Each report must meet all four of these:

- **Coverage:** 100% of claims are cited.
- **Accuracy:** at least 95% of cited claims are supported by the source.
- **Claims removed:** repair removed at most 15% of the report's claims.
- **Expected facts:** the report contains at least 75% of the question's expected facts.

The eval covers 5 questions with 2 runs each: python-315, lora-vs-qlora, aws-pillars, gguf-quants and self-rag.

## Results

| Mode | What runs | Latest result | Run | Expected facts | Claims removed | Lowest free RAM |
|---|---|---|---|---|---|---|
| **Local** | Gemma 4 E4B writes, on the PC | **9 of 10** | 2026-11-13, `eval-20261113-113015-8f7ed7` | 100% | 3% | 5.0 GB |
| **Hybrid** | Gemini writes from Tavily search (public only) | **10 of 10**, 5 of 5 questions passed on both runs | 2026-11-14, `eval-20261114-062621-6e2c0d` | 100% | 6% | 4.2 GB |
| **Local, Tavily off** | Gemma writes from offline copies, free APIs and SearXNG | **8 of 10** | 2026-11-14, `eval-20261114-050849-f454a6` | 90% | 4% | 4.4 GB |
| **Codex** | Gemma writes from the local document index | **10 of 10** | 2026-11-13, `eval-20261113-183211-75c5f1` | – | – | – |

Results files: `stage2_hybrid_2026-11-14.md`, `stage2_eval_2026-11-14.md` (Tavily-off runs), [`stage2_eval_2026-11-13.md`](https://github.com/Vanc8262/local-ai-research-assistant/blob/main/results/stage2_eval_2026-11-13.md) and [`stage2_eval_2026-11-12.md`](https://github.com/Vanc8262/local-ai-research-assistant/blob/main/results/stage2_eval_2026-11-12.md).

**The single local failure** was lora-vs-qlora run 2. Repair removed 2 of its 11 claims (18%), which is just over the 15% limit. The report still had all the expected facts.

**The two failures with Tavily off** were about the writer, not search. lora-vs-qlora run 1 left out NF4, double quantization and paged optimizers, so it found 25% of the expected facts. gguf-quants run 2 lost 6 of its 20 claims. Four of those were real errors: the writer read sizes and quality labels from the wrong rows of llama.cpp's quantize table, which reaches the model flattened into one line of text.

**Codex:** all 10 reports pass, including both gguf-quants runs, which had failed in every earlier run.

### How Stage 2 got here

| Date | Local | Hybrid | Codex | What changed |
|---|---|---|---|---|
| 2026-11-05 | 0 of 5 | 1 of 5 | – | First full eval; the gate failed |
| 2026-11-08 | 7 of 10 | 8 of 10 | 3 of 6 | Added the repair step and the local decider (PRs #18, #19); made the gate stricter |
| 2026-11-12 | 6 of 10 | 10 of 10 | 5 of 10 | Moved to llama-swap; free RAM went from 86 MB to more than 4.5 GB |
| 2026-11-12 to 13 | **9 of 10** | – | **7 of 10** | Local search now returns page passages; writer rules; decider threshold set to p 0.8; ligature and multi-line quote fixes; Tavily fallback |
| 2026-11-13 | – | – | **10 of 10** | GGUF codex fixes (PR #25): table-aware quotes in the checker, text sources stored as text, whole quant names in keyword search, more distinct chunks |
| 2026-11-13 to 14 | 7, 6, then **8 of 10** with Tavily off | – | – | Local search router (PR #25): Kiwix offline copies, free APIs called directly, keyword queries, off-topic filter; the checker reads the abstract a source gave the writer |

## Caveats

1. **Local mode still leans on Tavily for its best result.** In the 9 of 10 local run, Tavily supplied 99 of the 102 sources, because SearXNG's free engines had suspended our instance. The new router in PR #25 replaces most of that. It queries offline copies through kiwix-serve (Wikipedia, DevDocs, Stack Exchange) and free APIs called directly at their published rates (arXiv, OpenAlex, Hugging Face papers, GitHub, Stack Exchange, Hacker News), and uses SearXNG only as a bonus. With Tavily off, it passed 8 of 10, found 90% of the expected facts and lost 4% of claims to repair. 85% of its sources came from the offline copies and free APIs, and none from Tavily or any keyed service. The design's gate asked for 9 of 10 and 95% of the facts, so Tavily stays as a fallback. It is used only when every layer comes back empty, and only for public questions. `eval --no-tavily` turns it off. Offline-only mode (`--search offline`, nothing leaves the PC) passed 2 of 10, which shows what the offline copies alone can do.
2. **Hybrid removes more claims than before.** The 2026-11-14 rerun on the current main passed 10 of 10 again, but repair removed 6% of claims on average (21 of 353), up from 1%. No report came near the 15% limit; the highest was 13%. The cause could be the checker change in PR #25 (it now also reads the text each source gave) or Gemini's run-to-run variation. The removed claims have not been hand-checked. Hybrid verification also got slower, at up to 39 minutes per report, because Gemini wrote reports of up to 65 claims.
3. **The gate is a checker result, not a guarantee of truth.** The decider overrules passes it doubts, and that has caught real weak passes. A hand review of the AWS codex reports (`aws_codex_review_2026-11-12.md`) still found checker mistakes in both directions.

## Known limits accepted at close

- **Tavily as a fallback in local mode** (caveat 1). Without it, local mode passes 8 of 10, one short of its gate.
- **Flattened tables.** Tables that arrive as one line of text, such as llama.cpp's quantize table on a web page, still lead the writer to read the wrong row. The codex no longer has this problem, since it stores those files as text with whole table rows.
- **Offline-only mode is weak.** It passed 2 of 10, because the offline copies have no papers or pages newer than their dump date. It is reported, not gated.
- **Reports are short.** Local reports have 9 to 25 claims, and codex reports have 6 to 25. The writer rules traded length for accuracy.
- **One run in ten loses a little too much to repair.** This happens in both local and codex modes. It is noise around the 15% limit, not a pattern tied to one question.
- **Long eval runs.** The full eval (local, hybrid and codex, 2 runs each) takes about 5 hours. Hybrid verification is the slow part, at up to 39 minutes per report (the hybrid part alone took 4 hours on 2026-11-14).

## Carried into Stage 3

**Built in Stage 2 and reused in Stage 3:**
- llama-swap with standalone llama.cpp. Free RAM stays above 4.5 GB under load.
- The run log and privacy gateway. Private prompts never reach a cloud tier.
- The local decider. Stage 3 can use it to classify steps and spot destructive shell commands.
- The search router, with offline copies on D:\kiwix (refreshed with `scripts/get_kiwix.ps1`) and free APIs, behind the same gateway `/search`.

**Open items, in the order agreed on 2026-11-12:**
1. Bench code suites on the llama.cpp setup. They have not run since LM Studio was removed. They run model-written code without a sandbox, so they need Van to ask for them.
2. Heretic Gemma 4 E4B bench, then Van's picks for the model vault.
3. Stage 3 plan: the code-editing loop on a sandbox repo, with a gate of at least 6 of 10 tasks passing unattended and failures stopping cleanly, plus the Headroom A/B.
4. Separately: research [agent-rules-books](https://github.com/ciembor/agent-rules-books) for the assistant's rules (its own thread).

**Search, for later:** to drop Tavily entirely, the next steps are to keep tables whole when web pages are fetched, and to add a check for missing expected facts that sends the writer back for another search. Stage 4's router can treat Tavily as a free cloud tier with a quota. The design is in `research/ideas/local-search-path-design.md`.

## Approval

- [ ] Van approves closing Stage 2 on these results and limits.
- [ ] Van accepts Tavily as a documented fallback in local mode (agreed on 2026-11-14, if it does not reach 9 of 10).
- [x] Van merges PR #25 (merged 2026-11-14).
- [x] Hybrid rerun on the current main: 10 of 10 (2026-11-14).

This report is added to `results/` in the sign-off PR. Merging that PR closes Stage 2.
