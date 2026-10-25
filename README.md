# Local AI Research & Implementation Assistant

A local-first assistant that researches a topic and then implements the result in a code repository. Existing open-source engines do the searching and editing; this project is the orchestration layer on top of them: a router that decides which model handles each step, a policy that keeps private code on the machine, and a run log that makes local-only and hybrid runs comparable.

**Status:** Stage 1 in progress (model gateway, run log, SearXNG). Stage 0 complete: Gemma 4 E4B QAT is the default local model and Qwen3.5 9B the backup (see [Stage 0 results](#stage-0-results)). See [bench/README.md](bench/README.md) to run the harness.

## Goals

- **Research:** produce a cited report from web, academic and local-document sources on a single 12 GB GPU.
- **Implementation:** complete a fixed suite of small repo tasks on a git branch with tests passing.
- **Routing:** log every step with the tier that handled it, token counts and latency.
- **Cost and privacy:** $0 recurring cost, and no private source code leaves the machine through an automated path.

## Constraints

| Area | Decision |
| --- | --- |
| Hardware | RTX 3060 12 GB, 16 GB RAM, Windows. Local models in the 4B to 9B class, up to 32K context |
| Budget | Free cloud tiers only for now; a paid, hard-capped tier may be added later |
| Code locality | Code from public GitHub repos may go to cloud tiers; code from private or unpublished repos never does |
| Autonomy | The assistant edits repos on its own, on a git branch, gated by tests. It never commits to main |
| Environment | Docker and WSL2 for services; LM Studio on the Windows host for GPU access |

## Architecture

```
                 Orchestrator
     request -> research -> plan -> implement -> report
            |                            |
     Research engine               Code executor
  (Local Deep Research,         (Aider or OpenCode,
        SearXNG)                 decided in Stage 3)
            |                            |
            +----------- Router ---------+
          every model call passes through here
                        |
     +------------------+------------------+
     |                  |                  |
 Local model      Free cloud tier     Manual hand-off
 (LM Studio)     (Gemini Flash, Groq)  (started by a person,
 private code,   no private code,       never routed)
 or by default   quota left

 Run log: tier, tokens, latency and result for every step
```

The code-locality check is default-deny. Anything read from a repo is tagged private unless the router confirms the repo's GitHub remote is public. Files that git ignores, such as `.env`, stay private even in a public repo.

| Step | Carries private code? | Tier |
| --- | --- | --- |
| Summarize or extract from web sources | No | Local first |
| Split a research question into queries; write the long report | No | Free cloud, local fallback |
| Write an implementation plan | No | Free cloud, local fallback |
| Read, edit and test code in a public repo | No | Local first; free cloud when the local model fails |
| Read, edit and test code in a private repo | Yes | Local only |
| Retry limit reached | Either | Stop and hand off to a person |

## Build versus reuse

| Piece | Reused | Written here |
| --- | --- | --- |
| Model serving | LM Studio local server (OpenAI-compatible API) | Model profiles |
| Web search | SearXNG in Docker | Source policy and rate limiting |
| Research engine | [Local Deep Research](https://github.com/LearningCircuit/local-deep-research) through its API | A wrapper that returns a report plus structured findings |
| Code editing | Aider or OpenCode as the executor | The task loop: branch, edit, test, retry, stop |
| Router | None | Tier selection, code-locality check, quotas, fallback |
| Evaluation | None | Step-level run log, task suites, local-only versus hybrid comparison |

## Roadmap

| Stage | What gets built | Exit gate |
| --- | --- | --- |
| 0. Baseline | Model downloads and a benchmark harness | One default model and one fallback chosen from measured numbers |
| 1. Skeleton | Docker Compose with SearXNG, one model client, run log | The same prompt runs on the local model and on Gemini through one interface |
| 2. Research | Local Deep Research wired in; local documents indexed | Every claim in a report traces to a source |
| 3. Implementation | The code-editing loop on a sandbox repo, local model only | At least 6 of 10 tasks pass unattended; failures stop cleanly |
| 4. Router and policy | Code-locality check, escalation rules, quota tracking, fallback | A test proves private code cannot reach a cloud tier |
| 5. End to end | Research to plan to implementation to summary on a real repo | One real task completed from a single request |

The thresholds are proposals and will be revised once Stage 0 produces real numbers.

## Running it (Stage 1)

Services run in Docker; models run in LM Studio on the Windows host.

```powershell
docker compose up -d                                   # SearXNG on http://127.0.0.1:8888
python -m assistant tiers                              # which tiers are configured and reachable
python -m assistant ask "Explain KV cache in one paragraph"            # default local tier
python -m assistant ask --public -t local -t gemini "Explain KV cache in one paragraph"
python -m assistant search "llama.cpp KV cache quantization"
python -m assistant show <run_id>                      # the logged steps of a run
python -m assistant proxy                              # tiers as an OpenAI endpoint on http://127.0.0.1:8765/v1
python -m assistant codex fetch                        # download codex/catalog.toml into the codex folder
python -m assistant research "What is new in Python 3.15?"             # cited report, local only
python -m assistant research --public -t gemini --search tavily "What is new in Python 3.15?"
```

Tiers are defined in [`assistant.toml`](assistant.toml). A cloud tier reads its key from an environment variable (`GEMINI_API_KEY` for Gemini) and never from a file in the repo. Prompts count as private unless `--public` is given, and private prompts are refused by cloud tiers. Every model call and search is a line in `runs/<run_id>.jsonl` with its tier, token counts and timing.

Tools that make their own model calls, such as Local Deep Research in Stage 2, point at `python -m assistant proxy` instead of LM Studio. The request's model name picks the tier (`local`, `local-backup` or `gemini`), and every call is logged and privacy-checked like `ask`. A proxy session is private unless started with `--public`. In a public session, a failed cloud call is retried once on the local tier.

`research` runs Local Deep Research from its own venv (`[research] ldr_python`) with the proxy as its only model provider, and saves `report.md`, `sources.json` and LDR's raw `result.json` under `runs/<run_id>/`. Tavily sees the query, so like a cloud tier it needs `--public`.

## Stage 0 results

Five models were benchmarked at 8K context on the RTX 3060, each with its maker's sampling settings ([results/summary.md](results/summary.md)). The two leaders were run again with a second seed and at 16K and 32K context.

| Model | Tools | Code | Research | Vision | Speed | VRAM at 8K / 32K |
| --- | --- | --- | --- | --- | --- | --- |
| **Gemma 4 E4B QAT (default)** | 90-97% | 100% (both seeds) | 98% | 100% | 74 tok/s | 4.8 / 5.2 GB |
| **Qwen3.5 9B Q4_K_M (backup)** | 100% | 90% | 80-100% | 100% | 54 tok/s | 7.1 / 7.9 GB |
| Granite 4.2 8B Q4_K_M | 97% | 70% | 98% | text only | 55 tok/s | 6.8 GB at 8K |
| Qwen3.5 9B Q6_K | 80% | 90% | 98% | 100% | 42 tok/s | 8.5 GB at 8K |
| Gemma 4 12B QAT | 90% | 80% | 100% | 100% | 40 tok/s | 9.3 GB at 8K |

Ranges cover the second seed. Research and vision show the automatic score; manual 1-5 scores are in each run's `manual_scores.csv`.

**Decision:** Gemma 4 E4B QAT is the default. It is the fastest, the most reliable on code, and leaves about 4 GB of system RAM free, which the 16 GB machine needs once Docker and SearXNG run. Its weak spot is recovering after a tool error (1 to 3 of 4), so Qwen3.5 9B, which handled tool calls best, is the backup for tool-heavy steps and for retries.

**Limits found:**
- Both leaders run 32K context fully on the GPU with no loss of speed.
- LM Studio's `llama-server` holds 5-7 GB of system RAM per loaded model, even with every layer on the GPU. Turning off "Keep Model in Memory" and turning on mmap did not change that. The figure grows during long runs, probably from the prompt cache, so long sessions should reload the model now and then.
- Only one model fits in VRAM at a time, so a switch to the backup costs a reload.

## Stage 0 plan

Candidate models: Qwen3.5 9B and Gemma 4 12B QAT, with Gemma 4 E4B QAT and Ministral 3 8B Reasoning as optional extras.

| Test | What it measures | Size | Scored by |
| --- | --- | --- | --- |
| Tool calling | Valid call format, right tool chosen, 3-step chains, recovery from a bad argument | 30 scripted prompts | Script |
| Code editing | Small changes to a sandbox repo that must pass unit tests | 10 tasks | Unit tests |
| Speed | Tokens per second, time to first token, VRAM and RAM in use; reasoning on and off | Every run | Script |
| Research summary | A summary of supplied sources with correct citations | 5 source sets | Manual rubric |
| Vision | Reading charts and screenshots | 5 images | Manual |
| Headroom | Free RAM with the model, Docker and SearXNG all running | 1 run per model | Script |

Everything runs at 8K context first; 16K and 32K are tested only on the finalist.

The harness for these tests is in [`bench/`](bench/README.md).
