# Stage 2 research eval, with repair (2026-11-05)

Same five public questions and setup as the [morning run](stage2_eval_2026-11-05.md), with three
changes: `repair` runs after `verify`, numbered list labels no longer count as claims, and local
tiers write reports at temperature 0.3 instead of 1.0. Hybrid writing on Gemini is unchanged.
The eval took 2 hours 9 minutes.

Gate: every claim cited (coverage 100%), at least 95% of cited claims supported, and at most 25%
of claims removed. "Before" is the report as written, and "after" is the repaired report.

| Question | Mode | Claims before | Coverage before | Accuracy before | Moved to a source | Shortened | Removed | Claims after | Coverage after | Accuracy after | Facts | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| python-315 | local | 10 | 100% | 90% | 0 | 0 | 1 | 9 | 100% | 100% | 100% | **pass** |
| python-315 | hybrid | 40 | 100% | 98% | 0 | 1 | 0 | 40 | 100% | 100% | 100% | **pass** |
| lora-vs-qlora | local | 12 | 75% | 78% | 3 | 1 | 1 | 11 | 100% | 100% | 50% | **pass** |
| lora-vs-qlora | hybrid | 33 | 100% | 97% | 0 | 0 | 1 | 32 | 100% | 100% | 100% | **pass** |
| aws-pillars | local | 9 | 89% | 100% | 0 | 0 | 1 | 8 | 100% | 100% | 100% | **pass** |
| aws-pillars | hybrid | 25 | 100% | 96% | 1 | 0 | 0 | 25 | 100% | 100% | 100% | **pass** |
| gguf-quants | local | 19 | 95% | 94% | 0 | 0 | 2 | 17 | 100% | 100% | 100% | **pass** |
| gguf-quants | hybrid | 51 | 98% | 88% | 0 | 1 | 6 | 45 | 100% | 100% | 100% | **pass** |
| self-rag | local | 13 | 92% | 100% | 0 | 0 | 1 | 12 | 100% | 100% | 67% | **pass** |
| self-rag | hybrid | 31 | 100% | 97% | 1 | 0 | 0 | 31 | 100% | 100% | 100% | **pass** |

Before repair, the means were:

| Mode | Run | Coverage | Accuracy | Facts |
|---|---|---|---|---|
| local | Nov 5 morning | 89% | 85% | 95% |
| local | this run | 90% | 92% | 83% |
| hybrid | Nov 5 morning | 96% | 92% | 100% |
| hybrid | this run | 100% | 95% | 100% |

## What the numbers mean

- **All 10 reports pass the gate.** Repair removed 3% of claims on average in hybrid reports and
  10% in local reports. The most removed from one report was 12%.
- **A 100% score is close to guaranteed by design.** Repair removes whatever still fails, so the
  citation scores cannot show how good the original report was. The "before" columns, the claims
  removed and the facts found are the numbers to watch.
- **The citation checker is still generous.** Reading every change by hand turned up weak moves.
  "It's essentially an end-to-end one-pass pipeline" now cites a quote about a single model trained
  end-to-end, and a claim about design principles cites a quote that does not mention them. A pass
  means the checker accepts what is left, not that every sentence is proven true.
- **Repair can remove useful, possibly correct detail.** The hybrid GGUF report lost its file
  sizes and most of its speed comparison. The removal list is in each run's `repair.md`.
- **Local reports are clean but can be incomplete.** Local QLoRA found 2 of the 4 expected facts,
  and local Self-RAG found 2 of 3. Repair did not cause this, because the facts were already missing
  from the reports as written. The gate does not measure completeness.
- **The lower temperature cannot be judged from one run.** Local accuracy before repair rose
  from 85% to 92%, but local facts fell from 95% to 83%. Results also swing widely from run to run.
- **Repair is quick:** 9 to 345 seconds per report, against 126 to 1,634 seconds for verification.
- **Memory is still tight.** Free RAM fell to 0.28 GB at its lowest. Nothing failed.
