# Stage 0 benchmark harness

Stage 0 answers one question: which local model can reliably call tools and edit code on this PC, and how fast. This harness runs six suites against one model at a time through LM Studio's local server and writes the results to `results/`.

It talks to any OpenAI-compatible server, so the same suites can later be pointed at a free cloud tier for the local-versus-hybrid comparison.

## What it measures

| Suite | What it measures | Size | Scored by |
| --- | --- | --- | --- |
| `tools` | Valid call format, right tool chosen, multi-step chains, recovery from a failed call, and not calling a tool when none is needed | 30 cases | Script |
| `code` | Small changes to a single Python file that must pass unit tests; one retry with the test output | 10 tasks | Unit tests |
| `speed` | Time to first token, tokens per second, prompt processing on a long input; reasoning on versus off | 3 prompts × 3 repeats | Script |
| `research` | A cited summary of three supplied sources | 5 source sets | Script, then you |
| `vision` | Reading charts, a table and a terminal screenshot | 5 images | Script, then you |
| `headroom` | Free RAM and VRAM with the model loaded | 1 reading | Script |

Every suite also records speed and peak memory for the requests it made.

The tool, research and vision data are fictional or generated, so a model cannot answer from memory.

## Setup

In PowerShell, from the repo folder:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
python -m pytest
```

The last command runs the harness's own tests (about 15 seconds, no model needed). They replay a scripted perfect model through every suite, so a pass means every test item is solvable and scored correctly.

## Prepare LM Studio

1. Download the models you want to test.
2. Start the server: the Developer tab in the app, or `lms server start`.
3. In the server settings, turn on the option that separates reasoning from the answer in API responses. The harness also handles inline `<think>` tags, but other reasoning formats would be counted as part of the answer.
4. Load one model with a fixed context size. `--identifier` gives it the id used in `bench.toml`:

   ```powershell
   lms ls
   lms load <model key from lms ls> --context-length 8192 --gpu max --identifier qwen3.5-9b
   ```

   `lms load --estimate-only <model key>` prints a memory estimate without loading.
5. Check what the server reports, and make the ids in `bench.toml` match:

   ```powershell
   python -m bench models
   ```

## Run

```powershell
# Quick check that everything is wired up: 2 items per suite
python -m bench run --model qwen3.5-9b --limit 2 --tag smoke

# The real run
python -m bench run --model qwen3.5-9b

# Then the next model
lms unload --all
lms load <other model key> --context-length 8192 --gpu max --identifier gemma-4-12b-qat
python -m bench run --model gemma-4-12b-qat
```

Other options:

- `--suites tools,code` runs only some suites.
- `--tag q5` labels a run, for example a different quantization.
- `--context 16384` records the context size when you reload the finalist at 16K or 32K.

Delete the `smoke` run folder afterwards so it does not appear in the summary.

## Memory headroom

Run this once per model with everything the assistant will need open at the same time: LM Studio with the model loaded, Docker Desktop, the SearXNG container and a browser.

```powershell
python -m bench headroom --model qwen3.5-9b
```

It warns when less than 2 GB of RAM is available, when the system is using swap heavily, or when less than 500 MB of VRAM is free. This reading decides the RAM upgrade question.

## Results

Each run gets a folder such as `results/20261012-0930_qwen3.5-9b/`:

| File | Contents |
| --- | --- |
| `meta.json` | Model, settings, context size, machine |
| `summary.json` | Score and metrics per suite |
| `<suite>.jsonl` | One line per test item, with the model's raw output and timings |
| `manual_review.md` | The research and vision answers, with a scoring rubric |
| `manual_scores.csv` | Where you enter a 1 to 5 score for each of those answers |

`results/summary.md` and `results/summary.csv` hold one row per run and suite. They are rebuilt after every run, and by `python -m bench report` after you fill in manual scores.

## Reading the numbers

- The suites are small, so a difference of one or two items between models is noise. Look for gaps of several items, or rerun with a different `seed`.
- `tools` and `code` matter most for this project. In `tools`, check the `chain` and `recovery` categories and the valid-call-format rate; those predict whether an editing loop will hold together.
- `passed on the first try` in `code` separates models that get it right from models that need the test output to fix their work.
- The `research` score is only the mechanical part (facts present, cited to the right source). Your manual score covers whether the summary is faithful and readable.
- A note such as `3 replies cut off by max_tokens` means the model ran out of tokens, usually while reasoning. Raise `max_tokens` for that model in `bench.toml` and rerun.
- Tokens per second on very short replies is unreliable; use the `speed` suite's `generate` figure.

## Settings

`bench.toml` holds the server address, defaults and one block per model. Per-model options:

| Option | Use |
| --- | --- |
| `vision = true` | Run the vision suite for this model |
| `temperature`, `max_tokens` | Override the defaults |
| `system_suffix` | Text added to every system prompt, for example `/no_think` on models that honour it |
| `extra_body` | Extra fields merged into every request |

## Limits

- The harness has been tested against a scripted stand-in for the server, not against a live LM Studio. Expect to adjust small things on the first real run, most likely model ids and reasoning settings.
- The reasoning on/off comparison uses LM Studio's own `/api/v1/chat` endpoint, because the OpenAI-compatible endpoint documents no reasoning switch. If a model does not support the setting, that part reports the server's error and the rest of the suite still runs.
- The `code` suite runs model-written code on your machine. Each attempt runs in a temporary folder in a separate process with a 60-second limit, but it is not sandboxed.
- Speed is measured by the client, so it includes local network overhead. Prompt tokens per second is an estimate.
