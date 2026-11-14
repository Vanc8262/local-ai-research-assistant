# Stage 2 hybrid rerun, 2026-11-14

`runs/eval-20261114-062621-6e2c0d`, hybrid mode, all 5 questions, 2 runs per question, public. It ran on main at e55ca87 (after PR #25), from 06:26 to 10:29 EST, 4 h 3 min. In hybrid mode Gemini writes each report from Tavily search results, and the local model (Gemma 4 E4B QAT on llama-swap) checks and repairs it. 289 tests passed before the run. Edge and the NordVPN service were open. A pass shows the checker accepts a report, not that every sentence is true.

**10 of 10 pass. 5 of 5 questions pass on both runs.**

| | 2026-11-12 morning hybrid | This run |
|---|---|---|
| Pass | 10 of 10 | 10 of 10 |
| Questions passing both runs | 5 of 5 | 5 of 5 |
| Facts | 100% | 100% |
| Claims removed (mean per report) | 1% (4 of 292 claims) | 6% (21 of 353 claims) |
| Accuracy before repair (mean) | 97% | 91% |
| Passes the decider overruled ("Doubted") | 42 | 42 |
| Sources | 201, all Tavily | 204, all Tavily |
| Lowest free RAM | 4.9 GB | 4.17 GB (gguf-quants r2, repair) |
| Slowest verify | 1,286 s (gguf-quants r1) | 2,362 s (gguf-quants r1) |
| Total verify time | about 2.0 h | about 3.3 h |

## Notes

- Sources: hybrid mode searches through Tavily inside LDR. The local search router (Kiwix, free APIs, SearXNG) is not used in this mode, so all 204 sources are Tavily's and there are no router layers to report.
- Removals rose from 1% to 6%, and accuracy before repair fell from 97% to 91%. No report came near the 15% limit. The highest were gguf-quants r2 (6 of 65, 9%), python-315 r2 (2 of 15, 13%) and gguf-quants r1 (4 of 46, 9%). The checker changed since 2026-11-12 (it now also reads the text each source gave, ea064e6), and Gemini's reports differ run to run. This run does not show which of the two caused the change, and I did not inspect the removed claims.
- Verify time grew with report length: gguf-quants produced 46 and 65 claims (45 and 4 on 2026-11-12), and each took about 38 minutes to check.

## Table (`runs/eval-20261114-062621-6e2c0d/summary.md`)

| Question | Mode | Run | Outcome | Sources | Claims before | Coverage before | Accuracy before | Facts before | Doubted | Cited | Rewritten | Removed | Claims | Coverage | Accuracy | Facts | Research | Verify | Repair | Min free RAM | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| python-315 | hybrid | 1 | ok | 19 | 22 | 96% | 90% | 100% | 3 | 0 | 1 | 2 | 20 | 100% | 100% | 100% | 106.0 s | 622.8 s | 136.4 s | 4687 MB | pass |
| python-315 | hybrid | 2 | ok | 30 | 15 | 87% | 69% | 100% | 0 | 3 | 1 | 2 | 13 | 100% | 100% | 100% | 119.6 s | 481.7 s | 224.3 s | 4763 MB | pass |
| lora-vs-qlora | hybrid | 1 | ok | 21 | 30 | 97% | 86% | 100% | 5 | 2 | 1 | 2 | 27 | 100% | 100% | 100% | 100.1 s | 1387.1 s | 239.8 s | 4804 MB | pass |
| lora-vs-qlora | hybrid | 2 | ok | 19 | 27 | 100% | 100% | 100% | 3 | 0 | 0 | 0 | 27 | 100% | 100% | 100% | 109.5 s | 1242.7 s | 1.0 s | 4701 MB | pass |
| aws-pillars | hybrid | 1 | ok | 18 | 47 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 47 | 100% | 100% | 100% | 91.1 s | 885.5 s | 1.0 s | 4802 MB | pass |
| aws-pillars | hybrid | 2 | ok | 17 | 34 | 100% | 94% | 100% | 1 | 0 | 0 | 2 | 32 | 100% | 100% | 100% | 101.7 s | 682.7 s | 80.1 s | 4798 MB | pass |
| gguf-quants | hybrid | 1 | ok | 27 | 46 | 100% | 89% | 100% | 7 | 0 | 1 | 4 | 42 | 100% | 100% | 100% | 125.5 s | 2361.5 s | 412.7 s | 4623 MB | pass |
| gguf-quants | hybrid | 2 | ok | 21 | 65 | 100% | 89% | 100% | 14 | 1 | 0 | 6 | 59 | 100% | 100% | 100% | 114.5 s | 2279.4 s | 376.4 s | 4167 MB | pass |
| self-rag | hybrid | 1 | ok | 18 | 31 | 100% | 94% | 100% | 5 | 1 | 0 | 1 | 30 | 100% | 100% | 100% | 104.5 s | 807.1 s | 73.2 s | 4433 MB | pass |
| self-rag | hybrid | 2 | ok | 14 | 36 | 94% | 97% | 100% | 4 | 1 | 0 | 2 | 34 | 100% | 100% | 100% | 99.2 s | 1147.4 s | 72.0 s | 4981 MB | pass |
