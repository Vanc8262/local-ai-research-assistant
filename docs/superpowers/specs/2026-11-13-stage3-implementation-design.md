# Stage 3 design: the implementation loop

Status: draft for Van's approval, revised 2026-11-14 after Stage 2 closed. Written from the "Review stages and design plan" thread (2026-11-13), where Van approved the six fixes in section 4 and ranked the coding jobs in section 2. The 2026-11-14 revision brings it in line with what Stage 2 changed: llama-swap with standalone llama.cpp instead of LM Studio, the local search router with Tavily as a public-only fallback, slower verification of long reports, and the measured RAM figures. Revised parts are marked *(2026-11-14)*.

## 1. Intent

Stage 3 builds the half of the assistant that writes code: it takes a task, edits a repo on a git branch with a local model, runs the tests, retries a few times, and either finishes with passing tests or stops cleanly with a report. The brief's exit gate is "at least 6 of 10 tasks pass unattended; failures stop cleanly". This design keeps that ratio but on 20 harder tasks (12 of 20), because the bench's 10 code tasks are already solved 10 of 10 by Gemma 4 E4B and would make the gate meaningless.

Why it matters: it is the second of the brief's four "working" conditions, and Stage 5 (one real task from a single request) chains Stage 2's report into this loop. It is also the portfolio's main measured result: what a 4B to 9B local model on a 12 GB GPU can do unattended, and which cheap changes (rules, compression, a backup model) move that number.

## 2. What the tasks must look like (Van's ranking)

Van ranked the jobs he wants to hand the assistant, most wanted first:

| Rank | Job | Tasks out of 20 | Examples |
|---|---|---|---|
| 1 | Research to code | 8 | Implement something specified only by a Stage 2 report: a QLoRA memory estimator (4-bit NF4 plus double quantization), a Self-RAG style passage filter, a GGUF quant-name parser and file-size estimator, reciprocal rank fusion, BM25 scoring, MMR selection |
| 2 | Data and analysis | 6 | Fix a CSV loader's mixed date formats, a group-by summary that handles missing values, a weekly resample, merging and de-duplicating two tables, IQR outlier flags, a chained-assignment bug in pandas |
| 3 | Upkeep | 4 | Replace a removed or deprecated API (`datetime.utcnow`, an old pandas call), add type hints until `mypy --strict` passes on a module, add docstrings with doctests that pass |
| 4 | Refactors | 2 | Split a long function, remove duplicated code into a helper; behavior must not change |

Rules for every task:

- Python 3.12 only, with pytest; pandas and numpy are the only third-party libraries.
- Each task is a small repo of 3 to 8 files. At least half the tasks need edits in two or more files.
- **Visible tests** sit in the repo and the model can read and run them. **Hidden tests** are copied in only at scoring time and decide pass or fail. Visible tests cover the main case; hidden tests cover edges. A research-to-code task may ship only one or two visible tests.
- Research-to-code tasks include the frozen `report.md` of a real Stage 2 run. The task author checks the report by hand: the report must describe the algorithm well enough, and the hidden tests follow the correct algorithm, not the report's mistakes. If no existing run covers a topic, the author runs `assistant research` first and freezes that output.
- *(2026-11-14)* Which reports to freeze: only reports that passed the gate in the closing runs (local `eval-20261113-113015-8f7ed7`, hybrid `eval-20261114-062621-6e2c0d`, codex `eval-20261113-183211-75c5f1`). For GGUF tasks use a codex report, because web reports still misread llama.cpp's flattened quantize table (a known Stage 2 limit). For QLoRA tasks, check that the report names NF4, double quantization and paged optimizers; one Tavily-off run left all three out. Stage 2 reports are short (9 to 25 claims), so a report that does not specify the algorithm is replaced by another run, never patched by the author.
- *(2026-11-14)* New freeze runs use the default search router (offline copies, then free APIs, then SearXNG; Tavily only for public questions). Hybrid runs take up to 39 minutes per report to verify, so freeze runs are an evening batch, not part of task writing.
- Every task ships a reference solution. A task is accepted into the set only when the starting repo fails the hidden tests and the reference solution passes them, checked by a unit test in `tests/`.
- Refactor tasks use characterization tests (hidden) that pin current behavior, plus a check that the target function got shorter or the duplication is gone.

## 3. Goals, non-goals, anti-goals

**Goals**
- A task loop: branch, edit, test, retry, stop, report, with every model call logged by tier, tokens and speed.
- A sandbox: model-written code and tests never run on the Windows host.
- A 20-task eval with two runs per task, and a gate of 12 of 20 in each run.
- Clean stops: every failure leaves main untouched, the branch inspectable, a failure report, and no container left running.
- Four measured comparisons, run one at a time: model choice, the agent-rules-books rules, output trimming against Headroom compression (section 6).

**Non-goals**
- Cloud tiers. Stage 3 is local only; every call is private.
- Van's real repos. That is Stage 5.
- OpenCode or a home-made editor. Aider is the executor (section 4, fix 3).
- Choosing rules by task type, or the `mini`/`full` rules versions.

**Anti-goals** (would make Stage 3 a failure even if the gate passes)
- Passing by editing tests. Any change to a test file, or to a file the task does not allow, is an automatic fail.
- Model-written code running on the host, even once.
- A gate met with tasks so close to the bench or to well-known exercises that the model has memorized them.
- A change kept because it won by one task out of 40 trials.

## 4. The six approved fixes

These were approved by Van on 2026-11-13. Fixes 1 to 4 are Stage 3. Fixes 5 and 6 belong to Stages 2 and 4 but are recorded here so they are not lost.

| # | Gap | Approaches considered | Picked |
|---|---|---|---|
| 1 | The bench tasks are too easy for a meaningful gate | A: 20 new tasks weighted by Van's ranking, hidden tests, gate 12 of 20. B: 10 tasks, same weighting (faster, but a 1-task gap stays noise) | A |
| 2 | Model-written code runs unsandboxed | A: a Docker container per test run with no network and a throwaway copy. B: a separate low-rights Windows user (still reaches the network and files) | A |
| 3 | Aider or OpenCode | A: Aider (edits by text, few tool calls, leaves context for code). B: a 5-task bakeoff first (half a day). C: our own edit loop (portfolio value, but against the reuse plan) | A |
| 4 | Too many A/B arms for the task count | One change at a time, two runs each: model, then rules, then compression; keep a change only if it wins clearly | as stated |
| 5 | Web, academic and codex sources are never in one report | A: separate codex and web passes, then a local merge step that keeps every citation; arXiv and OpenAlex queried directly as an academic search tool. B: patch Local Deep Research to mix engines (a fork to maintain) | A |
| 6 | A query written from private text could reach Tavily in Stage 5 | Anything a model writes from private input is private too, including search queries; the Stage 4 test proves it | as stated |

## 5. How the loop works

```
assistant code run <task> [--tier local] [--rules refactoring,legacy] [--compress headroom]
        |
  1. copy the task repo to runs/<run_id>/work, git init, commit "start", branch assistant/<task>
  2. Aider edits the work tree, calling the model through `assistant proxy` (private session)
  3. visible tests run in the sandbox container
  4. pass  -> commit, go to 6
     fail  -> trimmed failures go back to Aider; up to 3 attempts in all
  5. limit reached, timeout, forbidden edit or error -> stop cleanly (section 5.4)
  6. hidden tests run in the sandbox -> score; write result.json and summary.md
```

### 5.1 Executor: Aider on the host, through our proxy

- Aider lives in its own venv (like LDR), e.g. `~/.venvs/aider`, called as a subprocess. The project venv does not import it.
- Aider's model endpoint is a private `assistant proxy` that the loop starts for each task run, on a free port, with that run's own log, and stops afterwards. Every Aider call is then logged in `runs/<run_id>.jsonl` with tier, tokens and timing, and the privacy check applies without changing `assistant/proxy.py`. The model is picked per attempt by the model name Aider sends (`local` or `local-backup`), which the proxy already maps to a tier. *(Builder-check answer, my call.)*
- Aider runs non-interactively: `--message` with the task text, `--yes-always`, `--no-auto-commits` (the loop commits; git stays on so Aider can read the repo), analytics and update checks off, the files the task allows passed with `--file`, and `report.md` passed with `--read` for research tasks.
- **The loop owns testing and retries, not Aider.** One attempt is one Aider invocation; Aider's auto-test and auto-lint are off, so the number of model calls per attempt stays fixed and Aider never sees untrimmed test output. After each attempt the loop runs the visible tests in the sandbox (5.2) and decides whether to retry. *(Builder-check answer, my call.)*
- Edit format: the builder picks `diff` or `whole` from a 5-task smoke run on Gemma and records the choice and its numbers. The rest of Stage 3 uses that one format.
- *(2026-11-14)* **Model server.** Models are served by llama-swap with standalone llama.cpp on 127.0.0.1:1234 (`services/llama-swap/config.yaml`); LM Studio is gone. Gemma (with the embedding model) and Qwen sit in exclusive groups, so asking for the other model unloads the current one and loads the next. The loop logs that load time as its own step and does not count it toward the 15-minute task timeout. Before a task run the loop checks the server the way `evaluate.py` already does and reloads once on failure.
- *(2026-11-14)* **Context budget.** Both models run at 32K context with up to 6,000 output tokens. Task text, the repo map, the allowed files, the rules (about 950 tokens in phase 3) and `report.md` must fit in the rest. Aider's repo map is capped (`--map-tokens 1024` to start), and the loop logs prompt tokens per call, so a task that overflows shows up as a logged cause, not a silent failure. Gemma's tool score fell to 87% at 16 to 32K in Stage 0, which is another reason Aider's text edits fit better than tool calls.
- Aider executes no code at all in this setup; only the loop's test runs do, and those run in the container. Aider's shell suggestions are disabled (`--no-suggest-shell-commands`).
- *(2026-11-14)* Aider's own network use (update checks, analytics, model metadata downloads) is turned off or pre-cached, and a smoke test checks that a task run makes no outbound request other than to the local proxy. Task code is treated as private, like every Stage 3 call.

### 5.2 Sandbox

- One image, `assistant-sandbox:py312`, built from `python:3.12-slim` with pinned pytest, pandas, numpy and mypy, defined in `services/sandbox/Dockerfile`.
- Each test run: `docker run --rm --network none --memory 768m --cpus 2 --pids-limit 256 --read-only --tmpfs /tmp -v <copy of work tree>:/work` with a 120 s limit. The container gets a fresh copy of the work tree, not the work tree itself, so nothing it writes comes back.
- Hidden tests are copied into that copy only at scoring time; they never enter the work tree Aider sees.
- Docker's WSL VM is capped at 2 GB; one container at 768 MB fits. SearXNG is not needed during Stage 3 and should be stopped for eval runs. *(2026-11-14)* kiwix-serve (about 50 MB) may stay up.
- *(2026-11-14)* **RAM.** With llama-swap, free RAM stayed above 4.5 GB in local evals and above 4.17 GB in the 4-hour hybrid run. Stage 3 adds Aider's Python process and the sandbox container; their RAM is not measured yet, so the smoke run measures it first. The eval samples free RAM into `ram.csv` as Stage 2 does, and stops a task with `low_ram` if free RAM drops under 1.5 GB (the threshold is my call, to be checked against the smoke run).
- A unit test proves the container has no network and cannot see the host's files outside the mount.

### 5.3 Retries and feedback

- Up to 3 attempts per task (the count is a setting). Attempt 2 and 3 get the failing test names and short tracebacks (`pytest -q --tb=short`, cut to the first 60 lines), not the whole output. This trimming is always on; it is the baseline that Headroom is compared against.
- The backup model as a final attempt (Gemma, Gemma, then Qwen) is tested as an arm in phase 2, not built in by default.

### 5.4 Clean stops

Every run ends with exactly one stop reason in `result.json`:

| Stop reason | Meaning |
|---|---|
| `passed` | Hidden tests pass |
| `failed_tests` | Attempts used up, hidden tests fail |
| `forbidden_edit` | A test file or a file outside the task's allowed list changed |
| `timeout` | The task took over 15 minutes in all |
| `model_error` | The model server failed after one reload (as in `repair`) |
| `sandbox_error` | Docker failed (not a model failure; reported apart from the score) |
| `low_ram` | *(2026-11-14)* Free RAM fell under the floor (5.2); reported apart from the score, like `sandbox_error` |

On any stop: the branch keeps the last attempt as a commit for inspection, the starting commit is untouched, the container is gone (`--rm`, plus a check), and `summary.md` says what happened in a few lines. The loop never merges, and it never touches a repo outside `runs/`.

### 5.5 What is logged per task

Pass or fail, stop reason, attempts, wall time, model load time *(2026-11-14)*, model calls by tier, prompt and completion tokens, tokens per second, lines added and removed, files touched, rules and compression settings, Headroom ratio and retrieve calls when on, and lowest free RAM (reusing the eval's `ram.csv` sampler).

## 6. The eval and the comparisons

`assistant code eval [--runs 2]` runs every task, writes `results/stage3_eval_<date>.md` in the same style as the Stage 2 write-ups, and exits non-zero below the gate.

**Gate:** at least 12 of 20 in each of the two runs, and every failure with a clean stop (no `sandbox_error` left unexplained, no running containers, main untouched). The gate applies to the best configuration after phase 4; phase 1 is a baseline, not a gate attempt.

**Task difficulty is frozen before any model sees the tasks** *(builder-check answer, my call)*. The difficulty target is "a careful junior developer finishes it in 10 to 30 minutes". After the first model run a task may change only to fix a defect (an ambiguous spec, a wrong hidden test, a reference solution that does not pass), and each fix is listed in the write-up. A low score is never a reason to make tasks easier: if the gate fails, the next step is improving the loop, and Stage 3 stays open.

Comparisons, one at a time, each 20 tasks × 2 runs, in this order. The winner of each phase becomes the baseline for the next.

| Phase | Compare | Notes |
|---|---|---|
| 1 | Baseline: Aider + Gemma 4 E4B, trimmed feedback | Also fixes the edit format (5.1) |
| 2 | Gemma vs Qwen3.5 9B vs Gemma with a Qwen final attempt | Qwen was best at tools in Stage 0; Gemma is faster. *(2026-11-14)* The Qwen-final arm pays a llama-swap model swap on every task that reaches attempt 3; wall time is reported with and without load time |
| 3 | No rules vs `refactoring.nano` + `working-effectively-with-legacy-code.nano` | Vendored into `rules/` from ciembor/agent-rules-books at commit 893a88a with the MIT notice, about 950 tokens, passed to Aider with `--read`. Optional add-on: after tests pass, the decider asks each file's final-checklist questions about the diff and flags (never blocks) a "no". *(2026-11-14)* The decider is Gemma; if phase 2 picks Qwen, the checklist runs as one batch after all tasks so the models are not swapped per task |
| 4 | Trimmed feedback vs Headroom `compress()` | Headroom compresses **only the test-failure feedback** (the full pytest output instead of the 60-line trim), never the file contents or repo map: Aider's `diff` format needs exact file text, and compressing it would break edits. Library only, no `[ml]` extra, `HEADROOM_TELEMETRY=off`. Log the ratio *(builder-check answer, my call)* |

**When a change is kept:** it passes at least 2 more of the 40 trials than the baseline, and its median diff size is no more than 25% larger. Otherwise the simpler setup stays. With 40 trials this is still a direction, not proof, and the write-up says so.

**Rough cost:** a task takes about 2 to 6 minutes, so one arm (40 trials) is about 2 to 3 hours on the PC, and the four phases about 20 hours in all, spread over several evenings. *(2026-11-14)* Stage 2's evals ran longer than first estimated (5 hours for the full eval), so the smoke run re-estimates this before phase 1 starts.

## 7. Related fixes outside Stage 3

### 7.1 One report from web, academic and codex sources (Stage 2 follow-up)

- `assistant research --search mixed` runs two passes: the codex pass (private, local only) and the web pass. A local merge step then writes one report from both sets of claims, keeping each claim's citation; `verify` already checks each citation against its own source, so it needs no change.
- Because the codex is private, the merged report is private and the merge runs on a local tier. The web pass uses only the question text, so it may use Tavily only when the question is public.
- *(2026-11-14)* The academic search tool is already built: the search router (`assistant/sources.py`, PR #25) calls arXiv, OpenAlex and Hugging Face papers directly, paced and cached, next to the Kiwix offline copies. The web pass of `--search mixed` is simply the router. What is left to build is the two-pass run and the merge step. No email or other personal detail goes in any request.
- *(2026-11-14)* Merged reports will be longer, and verification time grows with claims (up to 39 minutes for a 65-claim hybrid report). The merge step keeps at most 30 claims (my call), dropping the least-supported ones first.

### 7.2 Private-derived text stays private (Stage 4)

- The gateway marks every model output from a private request as private. A search query, plan or summary written from private input inherits that mark, and a private item never reaches a cloud tier or a cloud search tool (Tavily, and later Groq or other free tiers).
- *(2026-11-14)* **The search router widens this.** Today a private research run already sends its search text to the router's live layer (arXiv, OpenAlex, Hugging Face, GitHub, Stack Exchange, Hacker News) and to SearXNG's engines. That is fine for a question Van typed, but in Stage 5 a model will write queries from private code, and those would leave the PC through these public services. **Van's decision (2026-11-14):** a private-derived query may use only the offline layer (Kiwix copies and the codex), so `--search offline` is forced for it; a question Van types himself keeps today's behavior. Offline-only passed 2 of 10 in Stage 2, so Stage 5 relies on the codex for private work. Stage 4 builds this rule and its test.
- The Stage 4 gate test plants a canary string in a private repo file, runs the full pipeline against fake cloud tiers, a fake Tavily *(2026-11-14)* and fake live-API and SearXNG endpoints that record every request, and fails if the canary, or any line of that file, appears in any outbound request.

## 8. Order of work

Before Stage 3 starts:
1. Sign off Stage 2. *(2026-11-14)* Done: Stage 2 closed on 2026-11-14 (PR #26 and the closing report). The README status line is updated, and the roadmap's Stage 3 gate now reads 12 of 20 in each of two runs. Still to do: the Lucid diagram's stage status.
2. Run the bench `code` suites once on llama.cpp (not run since LM Studio was removed). This runs model code unsandboxed, so it needs Van's go-ahead under CLAUDE.md.

Stage 3 build, each step a PR with tests that run without a model:
3. Sandbox image and runner, with the no-network and no-host-files tests. Tests that need Docker are marked `sandbox` and skip when Docker is not running, so `pytest` still passes without Docker or a model, as CLAUDE.md promises. *(Builder-check answer, my call.)*
4. Task format and the first 6 tasks (2 per top three categories) with their acceptance test, to try the format before writing all 20.
5. The task loop with Aider through the proxy, stop reasons and logging; tested against `tests/fake_model.py`.
6. The remaining 14 tasks.
7. `assistant code eval` and the write-up format.
8. Phases 1 to 4 on the PC. Covered by Van's go-ahead for sandboxed runs (section 10).

Alongside, not blocking: the Heretic bench and Van's vault picks (only models Van picks go into `vault.toml`; benchmarking never adds one).

## 9. Decided by Van, and left to the builder

**Decided by Van (2026-11-13):** task mix and ranking (section 2); all six fixes (section 4); Aider as the executor; the agent-rules-books arm with the optional checklist step; Headroom as an A/B, not a default. *(2026-11-14)* Search queries a model writes from private input use only the offline copies and the codex (7.2).

**Left to the builder, recorded when made:** Aider's edit format (from the smoke run); the exact Aider flags; the contents of the 20 tasks within the mix above; the sandbox limits if 768 MB or 120 s prove too tight; how the loop's code is split into modules.

## 10. When real models may run (answered)

**When may real models run?** Three steps need a real model before phase 1: the 5-task edit-format smoke run (sandboxed), new `assistant research` runs to freeze reports for the research-to-code tasks (no code is executed), and the bench `code` suite on llama.cpp (unsandboxed). **Van's answer (2026-11-13):** go-ahead now for all sandboxed Stage 3 runs (smoke runs, phases 1 to 4) and the research runs that freeze task reports. The unsandboxed bench `code` run still needs its own OK.

## Builder check

A fresh reviewer read this design as the builder on 2026-11-13. Its questions and their answers are marked "builder-check answer" above: the loop owns retries (5.1), one proxy per task run (5.1), Headroom compresses only test feedback (6), the gate applies to the best configuration with tasks frozen before any model sees them (6), and Docker tests skip without Docker (8). The one question only Van could answer is in section 10, now answered.
