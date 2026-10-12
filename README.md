# Local AI Research & Implementation Assistant

A local-first assistant that researches a topic and then implements the result in a code repository. Existing open-source engines do the searching and editing; this project is the orchestration layer on top of them: a router that decides which model handles each step, a policy that keeps private code on the machine, and a run log that makes local-only and hybrid runs comparable.

**Status:** planning complete, Stage 0 (model benchmark) not started. No code yet.

## Goals

- **Research:** produce a cited report from web, academic and local-document sources on a single 12 GB GPU.
- **Implementation:** complete a fixed suite of small repo tasks on a git branch with tests passing.
- **Routing:** log every step with the tier that handled it, token counts and latency.
- **Cost and privacy:** $0 recurring cost, and no private source code leaves the machine through an automated path.

## Constraints

| Area | Decision |
| --- | --- |
| Hardware | RTX 3060 12 GB, 16 GB RAM, Windows. Local models in the 9B to 12B class, starting at 8K context |
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
