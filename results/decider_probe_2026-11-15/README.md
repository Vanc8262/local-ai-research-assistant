# Why the second opinion removed good claims (2026-11-15)

**Read this first.**

- The labels here are Claude's (a cloud session), not Van's. I chose the fix after seeing
  these labels, so the before and after numbers below are in-sample. The re-run in
  `results/second_opinion_redo_2026-11-15/` is the check on real reports.
- A passing gate shows that the checker accepts a report. It does not show that every
  sentence is true.

## What the second opinion is

`verify.check_citation` asks the local model (Gemma 4 E4B) for a verdict and a quote. The
quote must appear in the source, and the claim's numbers must appear there too. A
"supported" verdict then goes to the decider (`assistant/decide.py`, the same Gemma, asked for
one letter, true or false). If the decider's p(true) is below 0.8, the verdict drops to
"partial", and repair re-cites, rewrites or removes the claim.

## Data

`scripts/decider_probe.py` ran on Van-Desktop over the held-out eval
(`eval-20261114-195649-7fd789`) and `eval-20261114-050849-f454a6`. It took every check the
decider overruled (87) and a seeded sample of 40 it let through. For each one it rebuilt
the exact passages the decider saw, from the run's cached pages, and asked again in five
ways. The run took 147 s, and free RAM stayed at 7.4 GB or more.

| Variant | What the decider is shown |
|---|---|
| current | the production prompt: three passages, about 3,400 characters |
| reversed | the same, with the options in the other order |
| quote | only the sentence the checker quoted, which has already been found in the source |
| both | the passages, then the quote |
| reworded | the passages, with "the passages say this, in the same or other words" |

`probe.jsonl` holds every answer, with its letter mass and first tokens. `cases.csv` holds
the claims, quotes, passages and labels.

**Labels.** I judged each check against the checker's quote, reading the passages when the
quote fell short.

- **yes:** the source states the whole claim.
- **part:** it states the main point, but the claim adds a detail, an emphasis such as
  "crucial", or a framing the source lacks.
- **no:** the quote doesn't support the claim.
- One case was unclear (a "This makes…" sentence with no antecedent), and I left it out.

| Group | yes | part | no |
|---|---|---|---|
| Overruled by the decider (p < 0.8) | 51 | 32 | 3 |
| Let through (p ≥ 0.8), sample of 40 | 35 | 5 | 0 |

The 6 decider removals that PR #29 labelled as true are all in the overruled group, and I
labelled them the same way.

## What it shows

1. **It's not noise or a parsing fault.** On every case, all of the letter mass fell on A
   or B (mass 1.00), and the re-asked p matched the stored one within a few hundredths. The
   decider gives the same wrong answer every time.
2. **Below 0.8, p carries almost no information.** Of the 86 overruled checks, 51 were fully
   supported. Below p 0.5 there were 33 yes against 20 part. From 0.5 to 0.8 there were 18
   yes, 12 part and 3 no, so all three unsupported checks were in the upper band. The
   decider often answers p 0.00 on claims that its own quote states almost word for word.
   For example, "Q8_0 is a specific quantization format supported by GGUF" got p 0.04, and
   "Examples include Amazon DynamoDB, Apache Cassandra, and CouchDB" got p 0.76 against
   "Example databases: Amazon DynamoDB, Apache Cassandra, CouchDB".
3. **Option order and rewording don't fix it.** With the options reversed it still
   overruled 44 yes, and with the reworded statement 35.
4. **Showing it the quote does.** Asked whether the checker's quote supports the claim, the
   same model gave p ≥ 0.8 on 72 of the 86 fully supported checks.

**Root cause.** The second opinion asked a one-letter, no-reasoning call to search about
3,400 characters of passages for the support, which the checker had already found and
verified. The 4B model can't do that search in one token, so its "false" mostly means it
didn't find the support, not that the support is missing. The question the second opinion
should answer is narrower: given the sentence the checker quoted, does it say what the
claim says?

## Before and after, on these 126 labelled checks

The decider overrules a pass when p(true) < 0.8 (unchanged).

| Second opinion reads | Good claims overruled (yes) | Weak claims caught (part or no) |
|---|---|---|
| passages (before) | **51** of 86 | 35 of 40 |
| quote (after) | **14** of 86 | 19 of 40 |
| quote, threshold 0.5 | 10 | 12 |
| passages + quote ("both") | 22 | 13 |

- **Before:** 59% of overrules (51 of 86) hit fully supported claims.
- **After:** 42% do (14 of 33). Both counts include the 2 let-through "part" claims that the
  new check now catches.
- **The cost:** 15 more part claims and 1 more "no" now stay "supported". The "no" is #17,
  "This reward model takes a prompt…", quoted to a table cell that says "Explicitly trained
  separate model". The quote-based decider gave it p 0.95.

From the earlier sample (PR #29), the checker's false-pass rate was 2 of 40. Most of the
claims that now pass are "part": the main point is sourced and a detail or emphasis isn't.

The threshold stays at 0.8. At 0.5, the quote check catches 7 fewer weak claims and spares
only 4 more good ones.

## Limits

- One labeller, and that labeller chose the fix. The labels are against the checker's
  quote and passages, not the whole page.
- The set has 126 checks, mostly hybrid (79 of 87 overruled checks). Local reports had only 8.
- The 40 let-through checks are a sample from a much larger pool. A rate taken from them
  (such as 2 of 40 now caught) applies to that whole pool, not to 40 checks.
- The decider is Gemma 4 E4B. Qwen would need its own check.
- The decider still can't see context ("It", "This approach"). Several overruled "yes"
  claims depend on the sentence before them.
