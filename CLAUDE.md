# CLAUDE.md

## Checks

```powershell
py -3 -m venv .venv                        # first time only; needs Python 3.11 or newer
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pytest       # about 2 minutes, no model or LM Studio needed
```

## Rules

- Prompts count as private unless `--public` is given, and private prompts never reach a cloud tier. Keep that check default-deny.
- Cloud keys come from environment variables, never from a file in the repo.
- Only Van picks models for `vault.toml`. Benchmarking a model never adds it.
- The `bench` `code` suite runs model-written code on this PC without a sandbox. Don't run real models through it unless asked.
- Passing `assistant verify` shows the checker accepts a report, not that every sentence is true. Say so when reporting results.
