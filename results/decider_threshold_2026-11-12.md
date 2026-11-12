# Decider threshold: hand-labelled claims and a recommendation

## What the threshold does today

`verify` asks the decider whether the passages support each claim the checker passed. A pass is kept only if the decider answers true **and** its confidence is at least 0.5 (`MIN_DECIDER_CONFIDENCE`). Confidence is 1 minus the entropy of the two options, so 0.5 means **p(true) of about 0.89**. Anything from 0.50 to 0.88 is an answer of "true" that still gets overruled.

## Data

The three evals of 2026-11-12 (local, hybrid, codex; 36 reports) gave 946 decider answers on claims the checker had passed. 878 had p of 0.9 or more, 68 were below, and 66 were overruled.

I labelled all 68 below 0.9 and a random 20 of the 878 above, 88 in all. Each label compares the claim with the quote the checker took from the source: **yes** (the quote supports the whole claim), **part** (it supports the main point, but the claim adds a detail the quote lacks) or **no**.

| Decider p(true) | Claims | Yes | Part | No |
|---|---|---|---|---|
| below 0.50 | 25 | 9 (36%) | 12 | 4 |
| 0.50 to 0.76 | 15 | 5 (33%) | 7 | 3 |
| 0.77 to 0.80 | 8 | 4 (50%) | 4 | 0 |
| 0.81 to 0.89 | 20 | 15 (75%) | 5 | 0 |
| 0.90 and above (sample of 20) | 20 | 18 (90%) | 1 | 1 |

## Recommendation

**Overrule a pass only when p(true) is below 0.8, and write the rule on p(true) directly instead of on entropy confidence.**

- Between 0.81 and 0.89 there are 20 passes, nearly all overruled today. None was wrong, 15 were fully supported, and the 5 "part" cases add small details (a date, "65B" where the source says 70B). That band is about 30% of all overrules, and today it cost correct claims: two GGUF codex claims that restate the source's own definition were removed at p 0.81 and 0.84.
- From 0.77 to 0.80 it is a coin flip between "yes" and "part", and below 0.77 only a third of the passes are fully supported, so the decider is earning its keep there. Keep overruling them.
- Below 0.50 the decider is still wrong about a third of the time (9 of 25 fully supported), so an overrule should keep meaning "partial, try to repair", never "unsupported".

## Limits

- 88 claims, labelled by one reader, against the checker's quote and not the full passages the decider saw.
- The bands are small (8 to 25 claims), so anything from 0.75 to 0.85 is defensible; 0.8 is where "no" stops appearing and "yes" passes three in four.
- All from Gemma 4 E4B. Qwen would need its own check.
