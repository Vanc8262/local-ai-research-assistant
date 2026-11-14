# Stage 2 eval, 2026-11-13 evening: local search router and GGUF codex fixes

Branch `claude/project-thread-8nyf6x` at cf43ea6 (PR #25). All runs finished on 2026-11-13 local time (01:05 UTC on the 14th). This file is `-13b` because `stage2_eval_2026-11-13.md` already exists on PR #23. The model (Gemma 4 E4B QAT on llama-swap) ran on this PC for every step. No Tavily: the gate used `--no-tavily` and the search cache shows Tavily 0. The bench code suite was not run. 284 tests pass. A pass shows the checker accepts a report, not that every sentence is true.

| Eval | Run folder | Result |
|---|---|---|
| Codex, gguf-quants only | `runs/eval-20261113-181842-23dc77` | 2 of 2 pass |
| Codex, all 5 questions | `runs/eval-20261113-183211-75c5f1` | **10 of 10 pass**, 5 of 5 questions on every run, 2% of claims removed |
| Gate: local (gated) | `runs/eval-20261113-191737-4644aa` | **7 of 10 pass**, 3 of 5 questions on every run, facts 87%, 5% removed |
| Offline (reported only) | same | 0 of 6 scored reports pass; 4 runs found no sources |

Before this: codex 7/10 on 2026-11-12 (gguf-quants failed both runs), local 9/10 on 2026-11-13 with 99 of 102 sources from Tavily.

## Setup

- Codex fetch: all 5 downloaded as Markdown. gguf-spec.md 44.4 KB / 81 chunks, k-quants-pr.md 9.3 KB / 17, llama-cpp-readme.md 7.8 KB / 16, llama-quantize-readme.md 12.6 KB / 27, llama-quantize-types-2023.md 7.9 KB / 10. k-quants-pr.md starts with the PR's "What / Why" text, not GitHub page chrome. The index rebuilt to 26 files and 13,944 chunks in 443 s.
- Kiwix: `scripts/get_kiwix.ps1 -To D:\kiwix` fetched 11 DevDocs ZIMs, the ai, datascience and stats Stack Exchange sites, and `wikipedia_en_all_nopic_2026-06` (52.7 GB, about 50 minutes). It skipped Stack Overflow. kiwix-serve listed all 15 books after a restart; `/catalog/v2/entries` shows only 10 unless you pass `count=-1`.
- Memory: kiwix used about 10 MB serving the small ZIMs and peaked at 374 MB (of its 512 MB limit) while serving Wikipedia during the gate. That figure comes from one sample a minute. The lowest free RAM was 4.09 GB in the gguf-only codex run, 4.18 GB in the full codex run and 4.87 GB in the gate (15 s samples in each run's `ram.csv`); before the evals it was 4.90 GB. Edge (10 processes) and the NordVPN service were running; Steam was not.

## Router smoke test (before Wikipedia was served)

`make_router(load_settings()).search(q, 6)` returned hits for every eval question: python-315 6 (kiwix devdocs_en_python 2, Stack Exchange 2, Hacker News 1, SearXNG 1), lora-vs-qlora 6 (arXiv, OpenAlex, HF papers, SearXNG), aws-pillars 2 (SearXNG only), gguf-quants 3 (OpenAlex 1, SearXNG 2), self-rag 6 (arXiv, OpenAlex, HF papers, SearXNG). The Stack Exchange hits for python-315 were off-topic ("Installing MapServer…", "Travis CI defaults to wrong language"). For self-rag, arXiv returned other RAG papers but not the Self-RAG paper.

## Layer shares (gate)

- local: 58 sources. Offline 2, free APIs 18, SearXNG 38, Tavily 0. Offline and free APIs together give 34%; SearXNG still supplies about two thirds.
- offline: 35 sources, all from Kiwix.

## Failures

- gguf-quants local run 2: 2 of 11 claims removed (18%, over the 15% limit). Facts 100%.
- self-rag local runs 1 and 2: 33% of expected facts, 4 sources each. Run 2's sources were two blog posts (Medium, Meilisearch) and two other RAG papers, not the Self-RAG paper.
- offline, lora-vs-qlora and gguf-quants: no sources on either run. Every Kiwix search for these questions came back empty.
- offline, aws-pillars and self-rag: Kiwix returned 4 to 7 sources, but the reports had 1 claim each, and repair removed it.
- offline, python-315: 4 claims kept on both runs, but 0% of expected facts (6 to 8 Kiwix sources). I did not check which ZIM pages they came from.

## Codex, all questions (`runs/eval-20261113-183211-75c5f1`)

| Question | Mode | Run | Outcome | Sources | Claims before | Coverage before | Accuracy before | Facts before | Doubted | Cited | Rewritten | Removed | Claims | Coverage | Accuracy | Facts | Research | Verify | Repair | Min free RAM | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| python-315 | codex | 1 | ok | 10 | 24 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 24 | 100% | 100% | 100% | 113.9 s | 179.6 s | 1.0 s | 4184 MB | pass |
| python-315 | codex | 2 | ok | 10 | 15 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 15 | 100% | 100% | 100% | 132.8 s | 149.1 s | 1.0 s | 4281 MB | pass |
| lora-vs-qlora | codex | 1 | ok | 10 | 10 | 100% | 100% | 100% | 1 | 0 | 0 | 0 | 10 | 100% | 100% | 100% | 107.0 s | 121.4 s | 1.0 s | 4334 MB | pass |
| lora-vs-qlora | codex | 2 | ok | 10 | 9 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 9 | 100% | 100% | 100% | 104.4 s | 121.9 s | 1.0 s | 4321 MB | pass |
| aws-pillars | codex | 1 | ok | 8 | 9 | 100% | 78% | 100% | 0 | 2 | 0 | 0 | 9 | 100% | 100% | 100% | 78.2 s | 60.1 s | 16.6 s | 4281 MB | pass |
| aws-pillars | codex | 2 | ok | 6 | 4 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 4 | 100% | 100% | 100% | 77.7 s | 28.0 s | 1.0 s | 4524 MB | pass |
| gguf-quants | codex | 1 | ok | 10 | 16 | 100% | 75% | 100% | 2 | 0 | 2 | 2 | 14 | 100% | 100% | 100% | 115.7 s | 329.0 s | 199.4 s | 4475 MB | pass |
| gguf-quants | codex | 2 | ok | 10 | 7 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 7 | 100% | 100% | 100% | 111.2 s | 107.2 s | 0.9 s | 4225 MB | pass |
| self-rag | codex | 1 | ok | 10 | 17 | 100% | 76% | 100% | 1 | 3 | 0 | 1 | 16 | 100% | 100% | 100% | 113.9 s | 117.5 s | 51.9 s | 4637 MB | pass |
| self-rag | codex | 2 | ok | 10 | 17 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 17 | 100% | 100% | 100% | 121.2 s | 112.8 s | 1.0 s | 4607 MB | pass |

## Codex, gguf-quants only (`runs/eval-20261113-181842-23dc77`)

| Question | Mode | Run | Outcome | Sources | Claims before | Coverage before | Accuracy before | Facts before | Doubted | Cited | Rewritten | Removed | Claims | Coverage | Accuracy | Facts | Research | Verify | Repair | Min free RAM | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gguf-quants | codex | 1 | ok | 10 | 8 | 100% | 75% | 100% | 2 | 0 | 1 | 1 | 7 | 100% | 100% | 100% | 123.5 s | 198.5 s | 56.7 s | 4354 MB | pass |
| gguf-quants | codex | 2 | ok | 10 | 13 | 100% | 92% | 100% | 0 | 0 | 0 | 1 | 12 | 100% | 100% | 100% | 101.0 s | 169.5 s | 149.5 s | 4086 MB | pass |

## Gate: local and offline (`runs/eval-20261113-191737-4644aa`)

| Question | Mode | Run | Outcome | Sources | Claims before | Coverage before | Accuracy before | Facts before | Doubted | Cited | Rewritten | Removed | Claims | Coverage | Accuracy | Facts | Research | Verify | Repair | Min free RAM | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| python-315 | local | 1 | ok | 5 | 10 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 10 | 100% | 100% | 100% | 92.1 s | 130.1 s | 1.0 s | 4966 MB | pass |
| python-315 | offline | 1 | ok | 8 | 4 | 100% | 25% | 0% | 0 | 3 | 0 | 0 | 4 | 100% | 100% | 0% | 60.0 s | 22.5 s | 22.7 s | 5130 MB | fail |
| python-315 | local | 2 | ok | 4 | 8 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 8 | 100% | 100% | 100% | 97.6 s | 128.3 s | 1.0 s | 5066 MB | pass |
| python-315 | offline | 2 | ok | 6 | 4 | 100% | 75% | 0% | 0 | 1 | 0 | 0 | 4 | 100% | 100% | 0% | 59.3 s | 24.6 s | 9.5 s | 5054 MB | fail |
| lora-vs-qlora | local | 1 | ok | 8 | 9 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 9 | 100% | 100% | 100% | 100.2 s | 92.2 s | 1.0 s | 4968 MB | pass |
| lora-vs-qlora | offline | 1 | no_sources | 0 | - | - | - | - | - | - | - | - | - | - | - | - | 39.1 s | - s | - s | 4970 MB | - |
| lora-vs-qlora | local | 2 | ok | 8 | 14 | 93% | 85% | 100% | 0 | 1 | 0 | 2 | 12 | 100% | 100% | 100% | 106.2 s | 97.3 s | 70.3 s | 4978 MB | pass |
| lora-vs-qlora | offline | 2 | no_sources | 0 | - | - | - | - | - | - | - | - | - | - | - | - | 43.6 s | - s | - s | 5091 MB | - |
| aws-pillars | local | 1 | ok | 6 | 10 | 100% | 80% | 100% | 0 | 0 | 1 | 1 | 9 | 100% | 100% | 100% | 90.9 s | 86.5 s | 57.9 s | 4982 MB | pass |
| aws-pillars | offline | 1 | ok | 5 | 1 | 0% | - | 0% | 0 | 0 | 0 | 1 | 0 | - | - | 0% | 52.4 s | 0.5 s | 16.1 s | 4943 MB | fail |
| aws-pillars | local | 2 | ok | 7 | 7 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 7 | 100% | 100% | 100% | 88.8 s | 119.2 s | 0.9 s | 4951 MB | pass |
| aws-pillars | offline | 2 | ok | 7 | 1 | 0% | - | 0% | 0 | 0 | 0 | 1 | 0 | - | - | 0% | 46.3 s | 0.5 s | 15.1 s | 4895 MB | fail |
| gguf-quants | local | 1 | ok | 6 | 12 | 100% | 92% | 100% | 0 | 0 | 0 | 1 | 11 | 100% | 100% | 100% | 76.5 s | 113.3 s | 30.1 s | 4935 MB | pass |
| gguf-quants | offline | 1 | no_sources | 0 | - | - | - | - | - | - | - | - | - | - | - | - | 43.9 s | - s | - s | 5036 MB | - |
| gguf-quants | local | 2 | ok | 6 | 11 | 100% | 73% | 100% | 0 | 0 | 1 | 2 | 9 | 100% | 100% | 100% | 79.3 s | 81.3 s | 63.1 s | 4928 MB | fail |
| gguf-quants | offline | 2 | no_sources | 0 | - | - | - | - | - | - | - | - | - | - | - | - | 44.5 s | - s | - s | 4966 MB | - |
| self-rag | local | 1 | ok | 4 | 10 | 100% | 100% | 33% | 0 | 0 | 0 | 0 | 10 | 100% | 100% | 33% | 97.3 s | 74.9 s | 1.0 s | 4900 MB | fail |
| self-rag | offline | 1 | ok | 4 | 1 | 0% | - | 33% | 0 | 0 | 0 | 1 | 0 | - | - | 33% | 53.5 s | 0.5 s | 14.3 s | 4909 MB | fail |
| self-rag | local | 2 | ok | 4 | 12 | 100% | 100% | 33% | 0 | 0 | 0 | 0 | 12 | 100% | 100% | 33% | 117.7 s | 87.2 s | 1.0 s | 4868 MB | fail |
| self-rag | offline | 2 | ok | 5 | 1 | 0% | - | 33% | 0 | 0 | 0 | 1 | 0 | - | - | 33% | 48.8 s | 0.5 s | 16.3 s | 5040 MB | fail |
