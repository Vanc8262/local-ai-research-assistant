# Stage 0 benchmark results

One row per run and suite. Scores are automatic; `manual_avg_1_to_5` is filled from each run's manual_scores.csv.

| run_id | model | tag | context | suite | score_pct | passed | total | manual_avg_1_to_5 | median_ttft_s | median_gen_tps | peak_vram_mb | min_ram_available_mb | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20261016-203150_qwen3.5-9b | Qwen3.5 9B |  | 8192 | tools | 93.3 | 28 | 30 |  | 2.383 | 51.903 | 7175 | 2543 | valid call format 100%; single 10/10, no_tool 4/4, arguments 4/4, chain 7/8, recovery 3/4 |
| 20261016-203150_qwen3.5-9b | Qwen3.5 9B |  | 8192 | code | 80.0 | 8 | 10 |  | 2.816 | 51.431 | 7175 | 432 | 7 passed on the first try (2 attempts allowed); 2 replies cut off by max_tokens |
| 20261016-203150_qwen3.5-9b | Qwen3.5 9B |  | 8192 | research | 80.0 | 4 | 5 |  | 2.503 | 51.165 | 7137 | 920 | fact recall 0.8, citation accuracy 0.8 (automatic part); 1 replies cut off by max_tokens |
| 20261016-203150_qwen3.5-9b | Qwen3.5 9B |  | 8192 | vision | 100.0 | 5 | 5 |  | 2.857 | 51.48 | 7137 | 1595 |  |
| 20261016-203150_qwen3.5-9b | Qwen3.5 9B |  | 8192 | speed |  | 3 | 3 |  | 2.441 | 51.371 | 7137 | 444 | generation 51.371 tok/s; long input first token 3.603 s |
| 20261016-203150_qwen3.5-9b | Qwen3.5 9B |  | 8192 | headroom |  | 0 | 1 |  | 2.395 | 51.986 | 7137 | 1653 | only 1660 MB of RAM available; Docker not reachable; start Docker and SearXNG for a realistic reading |
| 20261016-211030_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | tools | 86.7 | 26 | 30 |  | 2.88 | 41.443 | 9297 | 299 | valid call format 100%; single 10/10, no_tool 4/4, arguments 4/4, chain 6/8, recovery 2/4 |
| 20261016-211030_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | code | 40.0 | 4 | 10 |  | 3.216 | 35.649 | 9306 | 2702 | 4 passed on the first try (2 attempts allowed); 12 replies cut off by max_tokens |
| 20261016-211030_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | research | 100.0 | 5 | 5 |  | 2.763 | 39.215 | 9306 | 3366 | fact recall 1.0, citation accuracy 1.0 (automatic part) |
| 20261016-211030_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | vision | 100.0 | 5 | 5 |  | 2.753 | 41.041 | 9306 | 3870 |  |
| 20261016-211030_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | speed |  | 3 | 3 |  | 2.569 | 39.803 | 9306 | 2945 | generation 39.803 tok/s; long input first token 4.922 s |
| 20261016-211030_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | headroom |  | 0 | 1 |  | 2.506 | 44.486 | 9306 | 4346 | Docker not reachable; start Docker and SearXNG for a realistic reading |
| 20261024-102529_qwen3.5-9b | Qwen3.5 9B |  | 8192 | tools | 100.0 | 30 | 30 |  | 0.221 | 54.183 | 7160 | 1167 | valid call format 100%; single 10/10, no_tool 4/4, arguments 4/4, chain 8/8, recovery 4/4 |
| 20261024-102529_qwen3.5-9b | Qwen3.5 9B |  | 8192 | code | 90.0 | 9 | 10 |  | 0.564 | 53.735 | 7160 | 377 | 9 passed on the first try (2 attempts allowed) |
| 20261024-102529_qwen3.5-9b | Qwen3.5 9B |  | 8192 | research | 100.0 | 5 | 5 | 4.8 | 0.451 | 53.327 | 7118 | 302 | fact recall 1.0, citation accuracy 1.0 (automatic part) |
| 20261024-102529_qwen3.5-9b | Qwen3.5 9B |  | 8192 | vision | 100.0 | 5 | 5 | 4.8 | 0.681 | 53.996 | 7118 | 650 |  |
| 20261024-102529_qwen3.5-9b | Qwen3.5 9B |  | 8192 | speed |  | 3 | 3 |  | 0.305 | 53.678 | 7118 | 270 | generation 53.678 tok/s; long input first token 1.56 s |
| 20261024-102529_qwen3.5-9b | Qwen3.5 9B |  | 8192 | headroom |  | 0 | 1 |  | 0.187 | 54.174 | 7118 | 667 | only 722 MB of RAM available; Docker not reachable; start Docker and SearXNG for a realistic reading |
| 20261024-105201_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | tools | 90.0 | 27 | 30 |  | 0.784 | 41.014 | 9295 | 428 | valid call format 100%; single 10/10, no_tool 4/4, arguments 4/4, chain 7/8, recovery 2/4 |
| 20261024-105201_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | code | 80.0 | 8 | 10 |  | 1.092 | 36.486 | 9289 | 2748 | 7 passed on the first try (2 attempts allowed); 5 replies cut off by max_tokens |
| 20261024-105201_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | research | 100.0 | 5 | 5 | 5.0 | 0.731 | 38.791 | 9287 | 2010 | fact recall 1.0, citation accuracy 1.0 (automatic part) |
| 20261024-105201_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | vision | 100.0 | 5 | 5 | 4.8 | 0.578 | 40.793 | 9287 | 3059 |  |
| 20261024-105201_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | speed |  | 3 | 3 |  | 0.582 | 39.761 | 9287 | 2054 | generation 39.761 tok/s; long input first token 2.912 s |
| 20261024-105201_gemma-4-12b-qat | Gemma 4 12B QAT |  | 8192 | headroom |  | 0 | 1 |  | 0.378 | 46.226 | 9287 | 3599 | Docker not reachable; start Docker and SearXNG for a realistic reading |
