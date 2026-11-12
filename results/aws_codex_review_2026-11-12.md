# Hand review: AWS codex reports from the Nov 8 eval

Runs `20261108-122515-7630c0` (run 1, 13 claims, 6 removed) and `20261108-124754-b2c81b` (run 2, 10 claims, 5 removed), question aws-pillars in codex mode. I read each removed claim against the three chunks the model was given (pages 7, 13 and 71 of the AWS Well-Architected Framework PDF).

## The 11 removed claims

| Cause | Run 1 | Run 2 | Verdict on the removal |
|---|---|---|---|
| Correct claim, quote check failed on a PDF ligature | 2 | 1 | wrong removal (bug, fixed) |
| Correct claim, the decider doubted it (p 0.55 and 0.73) | 1 | 0 | wrong removal |
| Comment on the sources, not a fact ("the snippets do not detail…") or an uncited summary | 3 | 0 | fair |
| Description of a pillar the given chunks never describe (security, performance efficiency, cost optimization, sustainability) | 0 | 4 | fair: padding, though partly true of the real framework |

## Findings

1. **Ligature bug in the quote check.** The PDF text has typographic ligatures (`eﬀectively`, `eﬃciency`). `quote_in_source` keeps only a to z and 0 to 9, so `eﬀectively` became `eectively` and never matched the model's `effectively`. Four claims quoting the operational excellence definition word for word were marked "quote not in source"; three were removed and one was re-cited. Fixed by NFKC-normalising text before the match (`assistant/verify.py`, with a test). All four quotes now pass.
2. **Retrieval gives the model too little.** Each run got only 3 chunks, all overview pages. The pillar table on page 7 is cut off after "protect", so only operational excellence has a description in context. Run 1 said so honestly (and those sentences were removed); run 2 filled the gap with plausible text (removed as unsupported). Both are symptoms of the same cause.
3. **The decider removed one supported claim.** "These four pillars are listed alongside security and operational excellence [1], [3]" is supported by page 13.

With the bug fixed, run 1 would still lose 4 of 13 (31%) and run 2 4 of 10 (40%), so both would still fail the 15% limit. The next fix is retrieval: more chunks per search for a small codex, or the neighbouring chunk of each hit, so a table split across chunks arrives whole.
