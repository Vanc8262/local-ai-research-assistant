# Decider check, 2026-11-08

`python -m assistant decide check` on the 36 cases in `evals/decide_cases.toml`, run on Van-Desktop (RTX 3060, LM Studio 0.4.25) at commit 169c369. Every decision used the `chat` method (first-token logprobs, thinking turned off with `reasoning_effort: "none"`). No errors, and no decision had under half the model's weight on an option letter.

The first run, before thinking was turned off, scored 0 of 36 on both models: they spent the answer tokens thinking, and LM Studio keeps thinking out of the logprobs.

| | Gemma 4 E4B | Gemma, debiased | Qwen3.5 9B Q4_K_M | Qwen, debiased |
| --- | --- | --- | --- | --- |
| Correct | 34 of 36 | 34 of 36 | 34 of 36 | 34 of 36 |
| Median time per decision | 0.26 s | 0.40 s | 0.50 s | 0.71 s |
| Mean confidence, right / wrong | 0.93 / 0.33 | 0.93 / 0.45 | 0.84 / 0.71 | 0.84 / 0.60 |

| Use case | Priority | Gemma | Qwen |
| --- | --- | --- | --- |
| Request routing | P1 | 5 of 5 | 5 of 5 |
| Model routing | P1 | 4 of 5 | 5 of 5 |
| Eval grading | P1 | 6 of 6 | 6 of 6 |
| Bulk labelling | P1 | 5 of 6 | 4 of 6 |
| Agent control | P1 | 4 of 4 | 4 of 4 |
| Map-reduce features | P1 | 4 of 4 | 4 of 4 |
| Guardrails | P2 | 6 of 6 | 6 of 6 |

Wrong cases (expected, got, p(expected), confidence; the same answers with debias):

- Gemma: `model-05` true, got false, 0.36, 0.06. `label-06` level 1, got 2, 0.15, 0.60.
- Qwen: `label-03` transition, got heading, 0.07, 0.68. `label-06` level 1, got 2, 0.02, 0.74.

Findings:

- Debiasing changed no answer and costs about 1.5 times the time, so it stays off by default.
- Gemma is about twice as fast, and its confidence separates right from wrong answers (0.93 vs 0.33). A threshold near 0.6 to 0.7 would send its shaky answers elsewhere. Qwen was confidently wrong on both misses.
- `label-06` ("fine I guess, but it took forty minutes and half the links were dead") fails on both models; "negative" vs "mixed" is arguable, so the label may be the problem.
- Free RAM was about 4.4 GB with Gemma and fell to about 0.57 GB during the Qwen checks.
- 36 cases is a small set: this shows the method works on both models, not how accurate it is in general.
