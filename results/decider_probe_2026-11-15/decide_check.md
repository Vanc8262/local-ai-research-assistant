# Decider check

## local

- Correct: 41 of 44 (93%)
- By split: test 39 of 42, dev 2 of 2 (dev cases came from eval failures or tuned the decider, so the test split is the fair score)
- Methods: chat 44
- Median time per decision: 0.105 s
- Mean confidence when right: 0.901, when wrong: 0.227
- Decisions where under half the model's weight fell on an option letter: 0

| Use case | Priority | Correct |
| --- | --- | --- |
| request routing | P1 | 5 of 5 |
| model routing | P1 | 4 of 6 |
| eval grading | P1 | 11 of 11 |
| bulk labelling | P1 | 5 of 5 |
| agent control | P1 | 4 of 5 |
| map-reduce features | P1 | 5 of 5 |
| guardrails | P2 | 7 of 7 |

| Case | Expected | Got | p(expected) | Confidence | Note |
| --- | --- | --- | --- | --- | --- |
| model-05 | true | false | 0.36 | 0.05 | 'B':0.635 'A':0.364 'I':0.001 'The':0.000 'This':0.000 'None |
| model-06 | false | true | 0.33 | 0.08 | 'A':0.667 'B':0.331 'The':0.001 'I':0.000 'T':0.000 'This':0 |
| agent-05 | false | true | 0.10 | 0.55 | 'A':0.904 'B':0.096 'T':0.000 'True':0.000 'C':0.000 'Testin |
