# Local AI Research & Implementation Assistant

A local-first assistant that researches a topic and then implements the result in a code repository. Existing open-source engines do the searching and editing; this project is the orchestration layer on top of them: a router that decides which model handles each step, a policy that keeps private code on the machine, and a run log that makes local-only and hybrid runs comparable.

**Status:** Stage 2: in the latest eval, with two runs per question and a stricter gate, 7 of 10 local, 8 of 10 hybrid and 3 of 6 codex reports pass ([results](results/stage2_eval_2026-11-08.md)). Stage 0 chose Gemma 4 E4B QAT as the default local model and Qwen3.5 9B as the backup (see [Stage 0 results](#stage-0-results)). See [bench/README.md](bench/README.md) to run the harness.

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
| Environment | Docker and WSL2 for services; llama.cpp (`llama-server`, switched by llama-swap) on the Windows host for GPU access |

## Architecture

Diagram: [architecture and roadmap in Lucidchart](https://lucid.app/lucidchart/e69a0a96-1ba7-483e-8d6d-a11ad69f0bb4/edit?invitationId=inv_44ca14c2-e204-4f9a-ab1c-3cc3aca7dd2e). It shows every component, which paths leave the PC, and the status of each stage.

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
 (llama.cpp)     (Gemini Flash, Groq)  (started by a person,
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
| Model serving | llama.cpp `llama-server` behind llama-swap (OpenAI-compatible API) | Model profiles, server flags |
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

**Vault selection step.** After each round of model benchmarking (Stage 0, the planned Heretic run, any later candidate), Van picks which models and which weight files go into the [model vault](#model-vault). Only those picks are stored; benchmarking a model never adds it on its own.

## Running it (Stage 1)

Services run in Docker; models run on the Windows host through llama-swap (see [Model server](#model-server)).

```powershell
docker compose up -d                                   # SearXNG on http://127.0.0.1:8888
python -m assistant tiers                              # which tiers are configured and reachable
python -m assistant ask "Explain KV cache in one paragraph"            # default local tier
python -m assistant ask --public -t local -t gemini "Explain KV cache in one paragraph"
python -m assistant search "llama.cpp KV cache quantization"
python -m assistant show <run_id>                      # the logged steps of a run
python -m assistant proxy                              # tiers as an OpenAI endpoint on http://127.0.0.1:8765/v1
python -m assistant codex fetch                        # download codex/catalog.toml into the codex folder
python -m assistant codex index                        # embed the codex PDFs (nomic-embed-text)
python -m assistant research "What is new in Python 3.15?"             # cited report, local only
python -m assistant research --public -t gemini --search tavily "What is new in Python 3.15?"
python -m assistant research --search codex "How does QLoRA differ from LoRA?"   # cites codex pages
python -m assistant verify <run_id>                    # check every claim's citations against its sources
python -m assistant repair <run_id>                    # fix or remove the claims that failed verify
python -m assistant eval [--runs 2]                    # Stage 2 eval: 5 questions x 2 runs; local, hybrid and codex; verified and repaired
python -m assistant vault plan                         # what the picks in vault.toml would store
python -m assistant vault sync                         # store exactly those picks, verified
python -m assistant vault verify                       # re-hash every stored file
```

Tiers are defined in [`assistant.toml`](assistant.toml). A cloud tier reads its key from an environment variable (`GEMINI_API_KEY` for Gemini) and never from a file in the repo. Prompts count as private unless `--public` is given, and private prompts are refused by cloud tiers. Every model call and search is a line in `runs/<run_id>.jsonl` with its tier, token counts and timing.

Tools that make their own model calls, such as Local Deep Research in Stage 2, point at `python -m assistant proxy` instead of the model server. The request's model name picks the tier (`local`, `local-backup` or `gemini`), and every call is logged and privacy-checked like `ask`. A proxy session is private unless started with `--public`. In a public session, a failed cloud call is retried once on the local tier.

`research` runs Local Deep Research from its own venv (`[research] ldr_python`) with the proxy as its only model provider, and saves `report.md`, `sources.json` and LDR's raw `result.json` under `runs/<run_id>/`. Tavily sees the query, so like a cloud tier it needs `--public`.

`codex index` reads each downloaded PDF page by page, splits the text into chunks of about 1,000 characters and embeds them with `text-embedding-nomic-embed-text-v1.5`, which llama-swap keeps loaded next to Gemma (about 0.9 GB of RAM and 0.3 GB of VRAM). The FAISS index is saved in `<codex_dir>/.index`, outside the repo, and is rebuilt only when the set of PDFs changes. `research --search codex` gives that index to Local Deep Research as its search engine, searching it two ways at once: by meaning (embeddings) and by exact words and figures (BM25), so a "48GB" or a "Q4_K_M" is found even where the embedding blurs it, and each source is a page of a codex file (`file:///…/react.pdf#page=3`). The codex is private, so codex research refuses `--public` and embeddings must come from a server on this PC. The verifier checks a codex citation against the chunk the model was given instead of fetching it. Local Deep Research will not mix a private engine with web search in one run, so the codex and the web are searched in separate runs.

`verify` is the Stage 2 gate. It splits a report into claims and flags any claim without a citation, and any citation to a source that does not exist. For each cited page it checks that the claim's version numbers and figures appear in the page, then asks a local model whether the page's most relevant passages support the claim. The model has to quote the supporting sentence, and a quote that is not in the page does not count. Results go to `verification.md` and `verification.json` in the run folder, with citation coverage (claims cited) and citation accuracy (cited claims supported). The command exits with status 1 while any claim is uncited or unsupported, or cites a missing source, so it can gate a script.

`repair` handles each claim that failed `verify`. If another likely source supports the claim as written, the claim cites that source instead. Otherwise a cited claim may be shortened to the part its sources support, and a claim that cannot be saved is removed. The report is then checked again, and whatever still fails is removed. A rewrite may only delete words, because a source can be the one that is wrong: an old page counting five AWS pillars must not turn a correct "six" into a wrong "five" that passes the check. The original is kept as `report.original.md`, and `repair.md` lists every change. If the model cannot answer, the repair reloads it and tries once more, then stops and puts the report back. Removing claims always raises the citation scores, so `eval` also records the claims removed and the expected facts before and after, and a report fails the gate if repair removed more than 15% of its claims or it has fewer than 75% of the question's expected facts. Passing shows that the checker accepts what is left, not that every sentence is true: its model is sometimes generous, and a hand review of the changes is still worthwhile.

`verify` also asks the decider (`assistant/decide.py`, the same local model) whether the passages support each claim the model passed. A pass it doubts (it answers false, or its confidence is below 0.5) drops to partial, and `verification.md` counts these. In a hand test it caught 3 of 5 known weak passes and kept both good ones. `eval` runs every question twice, and also in codex mode for the questions marked `codex = true`. It writes free RAM and the top processes every 15 seconds to `ram.csv`, with the lowest point in `summary.md`.

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
- LM Studio's `llama-server` holds 5-7 GB of system RAM per loaded model, even with every layer on the GPU, and grows during long runs. The cause turned out to be its 8 GiB prompt cache; see [Model server](#model-server).
- Only one model fits in VRAM at a time, so a switch to the backup costs a reload.

## Model server

The eval and the gateway reach the local models through llama-swap on `127.0.0.1:1234`, which starts and stops llama.cpp's `llama-server` with the flags in [`services/llama-swap/config.yaml`](services/llama-swap/config.yaml). Nothing depends on LM Studio: llama.cpp comes from its own GitHub releases and the models live in `D:\models`, in `<publisher>\<repo>\<file>.gguf` folders.

```powershell
# once
winget install mostlygeek.llama-swap
powershell -ExecutionPolicy Bypass -File scripts\install_llama_cpp.ps1   # newest CUDA build into ~\tools\llama.cpp; -Tag to pin one
powershell -ExecutionPolicy Bypass -File scripts\copy_models.ps1         # LM Studio's models -> D:\models (copies, checks sizes)

# before research or an eval
powershell -ExecutionPolicy Bypass -File scripts\start_llama_swap.ps1
```

New models come straight from Hugging Face, into the same layout:

```powershell
.venv\Scripts\python.exe -m pip install huggingface_hub
.venv\Scripts\hf download lmstudio-community/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir D:\models\lmstudio-community\Qwen3.5-9B-GGUF
```

Run `vault scan <file>` on a new GGUF before loading it. A new model for the eval also needs an entry in `config.yaml` and a tier in `assistant.toml`; a new benchmark model needs a file pattern in `scripts/run_stage0.ps1`, which starts `llama-server` itself (stop llama-swap first, both use port 1234).

Why: under LM Studio, `llama-server` grows from about 6 to 12 GB during a long verification, because LM Studio leaves the 8 GiB host prompt cache on and runs 4 slots. Free RAM fell to 86 MB in the Nov 8 eval. Measured on 2026-11-11, re-verifying the same report (50 model calls, Gemma 4 E4B, 32K):

| Server | llama-server peak (private) | Lowest free RAM | Speed |
|---|---|---|---|
| LM Studio, defaults | 12.1 GB | 324 MB | 70 tok/s |
| LM Studio, Keep Model in Memory off | 12.1 GB | 1.6 GB | 70 tok/s |
| llama-swap, `-cram 0 -np 1 -lm mmap --no-mmproj` | 4.6 GB | 7.4 GB | 77 tok/s |

All three gave the same verdicts. Gemma's lazy embeddings flag (`-lzm on`) saved nothing and cost speed, so it is off. Under llama-swap, `reload_model` asks it to unload, and the next request loads the model fresh. Gemma and the embedding model stay loaded together (Gemma 4.5 GB, nomic 0.9 GB private during a codex run). Loading Qwen unloads them; Qwen's server used 7.3 GB private on a short check, which still needs a look. Checked on 2026-11-11 with one aws-pillars eval run per mode: lowest free RAM was 6.3 GB in hybrid and 2.1 GB in local (where Docker's WSL VM took 2 GB), with llama-server flat at 4.6 GB private. `model_server = "lmstudio"` in `assistant.toml` still drives LM Studio through `lms`, for anyone who keeps it installed.

## Local decisions (option scoring)

Some steps need a decision, not text: which command a request needs, which tier runs a step, whether a passage supports a claim, whether a shell command is destructive. `assistant/decide.py` answers these the way a "System One" model such as TypeSafe's Jev does, but on the local model already loaded, so it costs nothing and adds no RAM. Each question has fixed options shown under letters; the decider reads the probability the model gives each letter for its first token instead of parsing generated text. Question types follow Jev's: `choice`, `score` and `noul` (true or false).

```powershell
.\.venv\Scripts\python.exe -m assistant decide probe -t local -t local-backup   # which method works on each tier
.\.venv\Scripts\python.exe -m assistant decide check -t local -t local-backup   # score the cases in evals/decide_cases.toml
.\.venv\Scripts\python.exe -m assistant decide check -t local --debias          # also ask with the options reversed
```

Decisions run on local tiers only and are logged as `decide` steps. If the server returns no token probabilities, the decider falls back to the letter the model writes at temperature 0, which gives a decision but no probability. The probabilities are not calibrated; thresholds come from the check's "confidence when right / when wrong" lines. The cases follow the use cases ranked on 2026-11-08: request routing, model routing, eval grading, bulk labelling, agent control and map-reduce features first, guardrails second.

## Model vault

The vault keeps permanent local copies of the open-weight models chosen to keep, so they never depend on a hosted service. It lives outside the repo at `vault_dir` (`D:/model-vault`, the 2 TB disk), in the same `<publisher>/<repo>` layout as `D:\models`, with `manifest.json` recording each model's source, exact commit, license, the reason it was picked, and every file's size and SHA-256. Research notes: [Owning open model weights locally](https://claude.ai/artifact/4rQnYU23cB5b3ZVQFGCY8P).

**Only picked models go in.** Picking is its own decision step after benchmarking: once a model's bench results are in, Van decides whether it is kept, and only then is it added to [`vault.toml`](vault.toml) with the bench run that justified it. `vault plan` shows what the picks would change and `vault sync` stores exactly those. Nothing is added automatically, and nothing is deleted: a model dropped from `vault.toml` shows as "not picked" until it is removed by hand. The file ships with no picks.

What the vault enforces:

- Weights are safetensors or GGUF only. Pickle formats (`.bin`, `.pt`, `.ckpt`, ...) and a repo's `.py` files can run code on load and are never downloaded. A pickle renamed to `.safetensors` fails the format check.
- A Hugging Face pick names a full 40-character commit, and every weight file must match the SHA-256 Hugging Face publishes for it; a mismatch is deleted.
- A GGUF chat template that reaches for Python internals (`__class__`, `__globals__`, `os.`, ..., as in CVE-2024-34359) is refused. `vault scan <file>` runs the same checks on any file before it is loaded.
- Stored files are read-only, and `vault verify` re-hashes them, flagging changed, missing, writable or unrecorded files.

Still to come: backups to an external drive with restic, signing the manifest with OpenSSF `model_signing`, serving the picked models from the vault instead of `D:\models`, and the manifest hash in each run-log step.

## Planned model: Gemma 4 E4B Heretic

[Heretic](https://github.com/p-e-w/heretic) (AGPL-3.0) removes a model's refusals without retraining. It ablates the direction in the model's activations that separates prompts it refuses from prompts it answers, and tunes that edit to keep the model as close to the original as it can. The candidate is a ready-made copy of the default model, [`llmfan46/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF`](https://huggingface.co/llmfan46/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF) at Q4_K_M (5.34 GB). Making one on this PC is not practical: Heretic's FAQ asks for about 16 GB of VRAM for an 8B model and about 24 GB of system RAM to save it.

What has been published, all by the uploader: 3 refusals out of 100 test prompts against 99 for the stock model, KL divergence 0.0076 from the stock model, and MMLU 68.6% against 69.5%. Nobody has published what the edit does to tool calling or code editing, which this project depends on, so the benchmark measures that.

| Test | Run | Compared with |
| --- | --- | --- |
| Tool calling, code editing | All six suites at 8K, seed 42 | Stock E4B, run `20261024-185125` |
| Repeat with a second seed | `tools` and `code`, seed 7 | Stock E4B, run `20261024-205639_seed7` |
| Long context | `tools` at 32K | Stock E4B at 32K (86.7%) |
| Research, vision, speed, headroom | Included in the six-suite run | Stock E4B |

```powershell
.venv\Scripts\hf download llmfan46/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF --include "*Q4_K_M*" "mmproj*" --local-dir D:\models\llmfan46\gemma-4-E4B-it-ultra-uncensored-heretic-GGUF
powershell -ExecutionPolicy Bypass -File scripts\run_stage0.ps1 -Models gemma-4-e4b-heretic
```

**Gate to keep it:** tools within 3 points of the stock model on both seeds, with recovery no worse than 1 of 4 and a 100% valid call format; code 9 or 10 of 10; research and vision within 5 points; speed and VRAM unchanged.

**Use if it passes:** a third local model for chat only. Research and code runs stay on the stock Gemma E4B and Qwen. A model edited to follow every instruction will also follow instructions planted in the web pages and files it reads, so it stays out of the automated pipeline. If it fails, it is not used.

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
