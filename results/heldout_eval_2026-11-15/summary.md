# Stage 2 research eval

Gate: coverage 100%, accuracy at least 95% (and at least 80% before repair), no unchecked claims, at least 6 claims, at most 15% and at most 5 claims removed or rewritten, at least 75% of expected facts in the report body. "Before" columns are the report as written. "Doubted" counts passes the decider overruled. A pass shows the checker accepts the report, not that every sentence is true.

| Question | Mode | Run | Outcome | Sources | Claims before | Coverage before | Accuracy before | Facts before | Doubted | Cited | Rewritten | Removed | Claims | Coverage | Accuracy | Facts | Research | Verify | Repair | Min free RAM | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bm25 | local | 1 | ok | 8 | 16 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 16 | 100% | 100% | 100% | 141.3 s | 138.1 s | 1.0 s | 2882 MB | pass |
| bm25 | hybrid | 1 | ok | 17 | 44 | 98% | 72% | 100% | 4 | 1 | 1 | 11 | 33 | 100% | 100% | 100% | 106.5 s | 1058.0 s | 585.7 s | 3283 MB | fail (accuracy_before changed_share changed_count) |
| hnsw | local | 1 | ok | 8 | 16 | 100% | 100% | 50% | 0 | 0 | 0 | 0 | 16 | 100% | 100% | 50% | 126.5 s | 202.8 s | 1.0 s | 3589 MB | fail (facts) |
| hnsw | hybrid | 1 | ok | 16 | 35 | 100% | 94% | 100% | 2 | 2 | 0 | 0 | 35 | 100% | 100% | 100% | 105.9 s | 1062.4 s | 19.5 s | 3622 MB | pass |
| flash-attention | local | 1 | ok | 8 | 13 | 100% | 77% | 75% | 0 | 0 | 1 | 2 | 11 | 100% | 100% | 75% | 103.0 s | 233.8 s | 99.0 s | 3532 MB | fail (accuracy_before changed_share) |
| flash-attention | hybrid | 1 | ok | 16 | 29 | 100% | 97% | 100% | 1 | 0 | 0 | 1 | 28 | 100% | 100% | 100% | 111.8 s | 1438.8 s | 316.1 s | 3670 MB | pass |
| rope | local | 1 | ok | 10 | 12 | 100% | 100% | 50% | 0 | 0 | 0 | 0 | 12 | 100% | 100% | 50% | 109.5 s | 82.8 s | 1.0 s | 3654 MB | fail (facts) |
| rope | hybrid | 1 | ok | 20 | 33 | 97% | 97% | 100% | 0 | 0 | 0 | 2 | 31 | 100% | 100% | 100% | 113.6 s | 675.5 s | 124.5 s | 3624 MB | fail (unchecked) |
| kv-cache | local | 1 | ok | 9 | 8 | 100% | 100% | 67% | 0 | 0 | 0 | 0 | 8 | 100% | 100% | 67% | 123.9 s | 76.2 s | 1.3 s | 3595 MB | fail (facts) |
| kv-cache | hybrid | 1 | ok | 23 | 38 | 100% | 95% | 33% | 6 | 0 | 0 | 2 | 36 | 100% | 100% | 33% | 98.8 s | 1057.8 s | 72.0 s | 3552 MB | fail (facts) |
| speculative-decoding | local | 1 | ok | 8 | 18 | 100% | 94% | 75% | 0 | 1 | 0 | 0 | 18 | 100% | 100% | 75% | 114.7 s | 149.3 s | 40.4 s | 4041 MB | pass |
| speculative-decoding | hybrid | 1 | ok | 20 | 38 | 97% | 97% | 100% | 2 | 0 | 0 | 2 | 36 | 100% | 100% | 100% | 102.3 s | 981.7 s | 49.7 s | 4081 MB | pass |
| moe-routing | local | 1 | ok | 9 | 13 | 100% | 91% | 100% | 0 | 0 | 0 | 2 | 11 | 100% | 100% | 100% | 113.6 s | 109.6 s | 114.0 s | 4012 MB | fail (unchecked changed_share) |
| moe-routing | hybrid | 1 | ok | 18 | 33 | 100% | 97% | 100% | 1 | 0 | 1 | 0 | 33 | 100% | 100% | 100% | 101.5 s | 799.2 s | 58.1 s | 2941 MB | pass |
| dpo | local | 1 | ok | 8 | 20 | 100% | 95% | 75% | 1 | 0 | 0 | 1 | 19 | 100% | 100% | 75% | 150.9 s | 152.7 s | 22.3 s | 3929 MB | pass |
| dpo | hybrid | 1 | ok | 24 | 49 | 98% | 98% | 100% | 19 | 1 | 0 | 1 | 48 | 100% | 100% | 100% | 99.4 s | 1694.2 s | 63.5 s | 3956 MB | pass |
| react-pattern | local | 1 | ok | 8 | 12 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 12 | 100% | 100% | 100% | 130.3 s | 93.9 s | 1.0 s | 3964 MB | pass |
| react-pattern | hybrid | 1 | ok | 25 | 42 | 98% | 98% | 100% | 3 | 0 | 0 | 2 | 40 | 100% | 100% | 100% | 102.4 s | 1126.0 s | 86.6 s | 3911 MB | pass |
| free-threaded-python | local | 1 | ok | 8 | 7 | 100% | 86% | 25% | 0 | 0 | 0 | 1 | 6 | 100% | 100% | 25% | 110.7 s | 53.9 s | 24.3 s | 3925 MB | fail (facts) |
| free-threaded-python | hybrid | 1 | ok | 23 | 41 | 100% | 100% | 100% | 3 | 0 | 0 | 0 | 41 | 100% | 100% | 100% | 91.5 s | 901.5 s | 1.0 s | 3929 MB | pass |
| pandas-arrow | local | 1 | ok | 8 | 15 | 100% | 100% | 67% | 0 | 0 | 0 | 0 | 15 | 100% | 100% | 67% | 123.7 s | 112.7 s | 1.0 s | 3918 MB | fail (facts) |
| pandas-arrow | hybrid | 1 | ok | 21 | 34 | 100% | 94% | 67% | 1 | 1 | 1 | 0 | 34 | 100% | 100% | 67% | 113.2 s | 569.9 s | 46.8 s | 3910 MB | fail (facts) |
| polars-lazy | local | 1 | ok | 10 | 28 | 96% | 82% | 100% | 2 | 5 | 0 | 1 | 27 | 100% | 100% | 100% | 86.7 s | 198.3 s | 80.6 s | 3942 MB | pass |
| polars-lazy | hybrid | 1 | ok | 23 | 45 | 98% | 100% | 100% | 8 | 0 | 0 | 1 | 44 | 100% | 100% | 100% | 105.3 s | 1490.2 s | 17.5 s | 3888 MB | pass |
| cap-theorem | local | 1 | ok | 13 | 9 | 100% | 78% | 100% | 0 | 2 | 0 | 0 | 9 | 100% | 100% | 100% | 120.9 s | 127.7 s | 17.3 s | 3868 MB | fail (accuracy_before) |
| cap-theorem | hybrid | 1 | ok | 24 | 44 | 100% | 98% | 75% | 6 | 0 | 0 | 1 | 43 | 100% | 100% | 75% | 108.2 s | 898.3 s | 33.1 s | 3809 MB | pass |
| k8s-hpa | local | 1 | ok | 8 | 4 | 100% | 100% | 75% | 0 | 0 | 0 | 0 | 4 | 100% | 100% | 75% | 117.6 s | 42.2 s | 1.0 s | 3858 MB | fail (few_claims) |
| k8s-hpa | hybrid | 1 | ok | 22 | 37 | 100% | 92% | 75% | 2 | 1 | 1 | 1 | 36 | 100% | 100% | 75% | 95.7 s | 997.2 s | 80.9 s | 3843 MB | pass |
| terraform-state | local | 1 | ok | 8 | 9 | 100% | 100% | 100% | 0 | 0 | 0 | 0 | 9 | 100% | 100% | 100% | 96.5 s | 52.9 s | 1.0 s | 3808 MB | pass |
| terraform-state | hybrid | 1 | ok | 16 | 41 | 98% | 95% | 100% | 7 | 0 | 1 | 2 | 39 | 100% | 100% | 100% | 139.1 s | 833.9 s | 98.9 s | 3758 MB | pass |
| shap | local | 1 | ok | 15 | 11 | 100% | 82% | 100% | 0 | 0 | 1 | 1 | 10 | 100% | 100% | 100% | 100.4 s | 188.4 s | 60.6 s | 3792 MB | fail (changed_share) |
| shap | hybrid | 1 | ok | 23 | 36 | 100% | 94% | 100% | 3 | 1 | 0 | 1 | 35 | 100% | 100% | 100% | 103.6 s | 1005.3 s | 71.8 s | 3825 MB | pass |
| k-fold-cv | local | 1 | ok | 8 | 7 | 100% | 100% | 33% | 0 | 0 | 0 | 0 | 7 | 100% | 100% | 33% | 99.8 s | 54.5 s | 1.0 s | 3779 MB | fail (facts) |
| k-fold-cv | hybrid | 1 | ok | 26 | 39 | 100% | 95% | 67% | 2 | 2 | 0 | 0 | 39 | 100% | 100% | 67% | 100.2 s | 1039.1 s | 21.0 s | 3841 MB | fail (facts) |
| hyde | local | 1 | ok | 8 | 13 | 100% | 85% | 67% | 1 | 0 | 0 | 2 | 11 | 100% | 100% | 67% | 108.9 s | 90.3 s | 60.1 s | 3728 MB | fail (changed_share facts) |
| hyde | hybrid | 1 | ok | 23 | 33 | 100% | 97% | 100% | 0 | 0 | 1 | 0 | 33 | 100% | 100% | 100% | 108.4 s | 866.3 s | 56.9 s | 2773 MB | pass |
| cross-encoder-rerank | local | 1 | ok | 8 | 5 | 100% | 100% | 33% | 0 | 0 | 0 | 0 | 5 | 100% | 100% | 33% | 111.8 s | 42.4 s | 1.0 s | 3274 MB | fail (few_claims facts) |
| cross-encoder-rerank | hybrid | 1 | ok | 26 | 35 | 100% | 97% | 100% | 4 | 0 | 0 | 1 | 34 | 100% | 100% | 100% | 126.4 s | 1210.8 s | 26.3 s | 3218 MB | pass |
| gptq-awq | local | 1 | ok | 8 | 8 | 100% | 100% | 75% | 0 | 0 | 0 | 0 | 8 | 100% | 100% | 75% | 123.0 s | 76.8 s | 1.0 s | 3325 MB | pass |
| gptq-awq | hybrid | 1 | ok | 17 | 27 | 96% | 96% | 100% | 5 | 1 | 0 | 1 | 26 | 100% | 100% | 100% | 103.4 s | 976.9 s | 41.8 s | 3384 MB | pass |

**local:** 20 reports, mean coverage 100% (before repair 100%), mean accuracy 100% (before 93%), mean facts 73% (before 73%), mean claims removed 4%, 7 pass the gate.
Tavily fallback (SearXNG returned nothing): 2 of these reports used it, for 13 sources in all.
Sources by search layer: offline 26, free APIs 105, SearXNG 34, Tavily 13 (of 178; offline and free APIs together 74%).
Questions that passed on every run: 7 of 20.
Pass rate 7/20, 95% Wilson interval 18% to 57%. Runs of the same question are not independent, so with more than one run per question the true interval is wider.

**hybrid:** 20 reports, mean coverage 100% (before repair 99%), mean accuracy 100% (before 95%), mean facts 91% (before 91%), mean claims removed 4%, 15 pass the gate.
Questions that passed on every run: 15 of 20.
Pass rate 15/20, 95% Wilson interval 53% to 89%. Runs of the same question are not independent, so with more than one run per question the true interval is wider.

**local vs hybrid, paired by question and run:** 20 pairs; both pass 6, neither 4, only local 1, only hybrid 9. Exact McNemar p = 0.0215 (under 0.05 is a real difference).

**Lowest free RAM:** 2773 MB, during hyde hybrid run 1: verify. Samples every 15 s are in ram.csv.
