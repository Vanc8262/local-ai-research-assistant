# Research Bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `assistant eval` into a versioned, frozen research bench. A candidate local model runs the whole pipeline, and a pinned local referee scores the final reports, ranking candidates on quality with speed as the tiebreaker.

**Architecture:** A bench spec file (`evals/bench_v1.toml`) freezes questions, splits, the referee pin, fit limits and the pass line. `assistant eval --bench … --candidate …` runs the existing pipeline with the candidate in the local tier and records each attempt in a committed ledger. A separate `assistant referee` pass scores saved run folders with no candidate loaded, and `assistant scorecard` compares candidates with a paired bootstrap tie rule.

**Tech Stack:** Python 3.11+, stdlib (`tomllib`, `csv`, `random`, `hashlib`), `httpx`, pytest. llama-swap and llama.cpp serve the models on the PC.

**Spec:** `docs/superpowers/specs/2026-11-15-research-bench-design.md` (approved by Van 2026-11-15). Read it with this plan.

## Global Constraints

- $0 a month. The referee and all candidates are local tiers (`kind = "local"`, loopback `base_url`). No cloud judge.
- Private prompts never reach a cloud tier. Keep the existing default-deny checks. Every referee `gateway.ask` call passes `private=True`.
- Cloud keys come only from environment variables, never from a file in the repo.
- Only Van picks models for `vault.toml`. Nothing in this plan writes to it.
- The bench `code` suite stays off. Nothing here runs model-written code.
- Every scorecard and referee summary prints: "A referee pass means the referee accepted the report, not that every sentence is true."
- Fit limits (spec 6.4): fully on the GPU at 32K context; free RAM never below 2048 MB; at most 60 minutes per report end to end; at most 10% of runs ending without a report.
- Pass line (spec 6.6): facts right ≥ 0.75 and errors through ≤ 0.05 on the test split, plus a fit pass. Van sets the final values before the freeze.
- Referee minimums (spec 6.3): recall on `supported` ≥ 0.90 and recall on `not_supported` ≥ 0.85 on the calibration set.
- Retest cap (spec 2 and 6.8): at most 3 test-split attempts per (bench version, candidate GGUF sha256), meaning the first attempt plus 2 retests. An identical candidate key (GGUF sha256 + git commit + tier settings hash) never runs twice on the test split.
- The referee reads only what the run stored (`sources.json` `full_content` and `snippet`, plus `pages/<index>.txt`). It never fetches or searches.
- Referee sampling: temperature 0, seed 20261115, thinking enabled.
- All file I/O uses `encoding="utf-8"`. Windows paths must work (`pathlib` only).
- Tests use fakes and need no model, as the existing suite does (`tests/fake_model.py`, `httpx.MockTransport`).

## Review Focus

1. **A citation to a number missing from `sources.json`** must count as `not_supported` with reason "no such source". It must not crash the referee or be skipped. Test: `test_referee_dangling_citation_is_not_supported` (Task 2).
2. **A referee reply that isn't valid JSON, or is empty, or names an unknown verdict.** Retry once with "Reply with the JSON object only", then record `unclear` with `referee_error` set. Never raise. Test: `test_referee_bad_reply_becomes_unclear` (Task 2).
3. **A run that ended without a report** (outcome other than `ok`) counts toward the fit error share, and its question scores facts right = 0 and is left out of errors through. Otherwise a model could look better by failing hard questions. Test: `test_quality_counts_failed_run_as_zero_facts` (Task 6).
4. **An expected fact that appears only in an unsupported, unclear or uncited sentence** must not count as found. Test: `test_facts_right_ignores_unsupported_sentences` (Task 2).
5. **An eval that crashes or is stopped partway** still uses up its attempt. The ledger row is written with status `started` before the first question runs. A rerun of the same key is refused even when the first attempt never finished. Test: `test_ledger_counts_started_attempts` (Task 4).

---

## File Structure

| File | Responsibility |
|---|---|
| `evals/bench_v1.toml` (create) | The frozen bench: version, question files and splits, referee pin, candidates, fit limits, pass line |
| `assistant/bench_spec.py` (create) | Load and validate a bench spec; pin checks; candidate key |
| `assistant/referee.py` (create) | Statements from a report, stored source text, one judge call, `referee.json` per run, facts right |
| `assistant/calibration.py` (create) | Sample statement–source pairs from dev runs for labelling, judge them, compute recall |
| `assistant/ledger.py` (create) | The test-split attempt ledger and the retest cap |
| `assistant/fit.py` (create) | Fit checks from summary rows and the llama-swap log |
| `assistant/scorecard.py` (create) | Per-candidate quality, paired bootstrap tie rule, ranking, markdown and CSV |
| `assistant/evaluate.py` (modify) | `run_eval` takes a candidate tier and bench stamp |
| `assistant/manifest.py` (modify) | Hash the candidate and referee GGUFs |
| `assistant/__main__.py` (modify) | `eval --bench/--candidate/--split`, `referee`, `referee calibrate`, `scorecard` |
| `assistant.toml`, `services/llama-swap/config.yaml` (modify) | `[tiers.referee]`; a `referee` model entry and group |
| `results/bench_ledger.csv` (create, committed) | One row per test-split attempt |
| `evals/bench_test_questions.toml` (create in Task 8, by a fresh session) | ~25 test questions |
| `tests/test_bench_spec.py`, `tests/test_referee.py`, `tests/test_calibration.py`, `tests/test_ledger.py`, `tests/test_fit.py`, `tests/test_scorecard.py`, `tests/test_bench_questions.py` (create) | Tests per module |
| `README.md` (modify) | "Research bench" section and the loop rules |

---

### Task 1: Bench spec file and loader

**Files:**
- Create: `evals/bench_v1.toml`, `assistant/bench_spec.py`, `tests/test_bench_spec.py`

**Interfaces:**
- Produces:
  - `@dataclass BenchSpec: version: int; frozen: bool; frozen_on: str; questions: dict[str, str]` (split → path), `referee: RefereePin; candidates: dict[str, CandidateRef]; fit: FitLimits; pass_line: PassLine; runs: int; search: str; tavily_fallback: bool`
  - `@dataclass RefereePin: tier: str; gguf_env: str; sha256: str; prompt_version: int; min_supported_recall: float; min_not_supported_recall: float`
  - `@dataclass CandidateRef: tier: str; gguf_env: str`
  - `@dataclass FitLimits: min_free_ram_mb: int; max_report_minutes: float; max_error_share: float; context: int`
  - `@dataclass PassLine: min_facts_right: float; max_errors_through: float`
  - `load_bench(path: str | Path) -> BenchSpec`, which raises `BenchSpecError(ValueError)` on missing keys, an unknown split name (only `dev` and `test`), or `frozen = true` with an empty `referee.sha256`
  - `check_referee_pin(spec: BenchSpec, env: Mapping[str, str], hasher=sha256_file) -> None`, which raises `BenchSpecError` when the env var is unset, the file is missing, or the hash differs (when `spec.referee.sha256` is empty, it only checks that the file exists)
  - `tier_settings_hash(tier: Tier) -> str`: sha256 hex of `json.dumps({model, temperature, max_tokens, extra_body}, sort_keys=True)`, first 12 characters
  - `candidate_key(gguf_sha256: str, git_commit: str, tier: Tier) -> str` = `f"{gguf_sha256[:12]}-{git_commit[:9]}-{tier_settings_hash(tier)}"`
  - `REFEREE_CAVEAT = "A referee pass means the referee accepted the report, not that every sentence is true."`

- [ ] **Step 1: Write `evals/bench_v1.toml`** with `version = 1`, `frozen = false`, `frozen_on = ""`, `runs = 1`, `search = "searxng"`, `tavily_fallback = false`; `[questions] dev = ["evals/research_questions.toml", "evals/heldout_questions.toml"]`, `test = ["evals/bench_test_questions.toml"]` (each split takes a list of paths: adjust the `questions` type to `dict[str, list[str]]`); `[referee] tier = "referee"`, `gguf_env = "REFEREE_GGUF"`, `sha256 = ""`, `prompt_version = 1`, `min_supported_recall = 0.90`, `min_not_supported_recall = 0.85`; `[candidates.gemma-e4b] tier = "local"`, `gguf_env = "GEMMA_GGUF"`; `[candidates.qwen-9b] tier = "local-backup"`, `gguf_env = "QWEN_GGUF"`; `[fit] min_free_ram_mb = 2048`, `max_report_minutes = 60`, `max_error_share = 0.10`, `context = 32768`; `[pass_line] min_facts_right = 0.75`, `max_errors_through = 0.05`. Copy the spec's section 6.8 loop rules into a header comment.

- [ ] **Step 2: Write failing tests**

```python
def test_load_bench_v1():
    spec = load_bench(ROOT / "evals/bench_v1.toml")
    assert spec.version == 1 and spec.frozen is False
    assert spec.fit.min_free_ram_mb == 2048 and spec.fit.max_report_minutes == 60
    assert spec.pass_line.min_facts_right == 0.75 and spec.pass_line.max_errors_through == 0.05
    assert spec.referee.min_supported_recall == 0.90 and spec.referee.min_not_supported_recall == 0.85
    assert set(spec.questions) == {"dev", "test"}

def test_frozen_needs_referee_hash(tmp_path):  # write a copy with frozen = true, sha256 = ""
    with pytest.raises(BenchSpecError, match="sha256"): load_bench(path)

def test_unknown_split_rejected(tmp_path):  # [questions] train = [...]
    with pytest.raises(BenchSpecError, match="train"): load_bench(path)

def test_referee_pin_mismatch(tmp_path):
    gguf = tmp_path / "r.gguf"; gguf.write_bytes(b"x")
    spec = replace(load_bench(...), referee=replace(spec.referee, sha256="0" * 64))
    with pytest.raises(BenchSpecError, match="hash"): check_referee_pin(spec, {"REFEREE_GGUF": str(gguf)})
    check_referee_pin(replace(spec, referee=replace(spec.referee, sha256=sha256_file(gguf))), {"REFEREE_GGUF": str(gguf)})

def test_candidate_key_changes_with_settings():
    a = Tier(name="local", kind="local", base_url="http://127.0.0.1:1234/v1", model="m", temperature=1.0)
    b = replace(a, temperature=0.5)
    assert candidate_key("a" * 64, "c" * 40, a) != candidate_key("a" * 64, "c" * 40, b)
    assert candidate_key("a" * 64, "c" * 40, a).startswith("aaaaaaaaaaaa-ccccccccc-")
```

- [ ] **Step 3: Run** `python -m pytest tests/test_bench_spec.py -v`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement `assistant/bench_spec.py`** with the interfaces above. Reuse `assistant.manifest.sha256_file`.
- [ ] **Step 5: Run** `python -m pytest tests/test_bench_spec.py -v`. Expected: PASS.
- [ ] **Step 6: Commit** `git add evals/bench_v1.toml assistant/bench_spec.py tests/test_bench_spec.py && git commit -m "Bench spec v1 and loader (research bench, task 1)"`

---

### Task 2: Referee scoring of one run folder

**Files:**
- Create: `assistant/referee.py`, `tests/test_referee.py`

**Interfaces:**
- Consumes: `assistant.verify.split_claims`, `quote_in_source`, `json_object`; `assistant.text.best_passages`; `assistant.evaluate.Question`, `fact_pattern`; `Gateway.ask(tier, messages, purpose=, private=, max_tokens=, temperature=, extra_body=)`; `bench_spec.REFEREE_CAVEAT`.
- Produces:
  - `LABELS = ("supported", "not_supported", "unclear")`
  - `@dataclass Statement: text: str; raw: str; citations: list[str]` (verbatim report spans from `split_claims`: `raw` is the sentence as written)
  - `@dataclass Judgement: statement: str; index: str; verdict: str; quote: str = ""; reason: str = ""; referee_error: str = ""`
  - `statements(report: str) -> list[Statement]`: `split_claims` output with exact duplicate texts merged (citations unioned in order)
  - `stored_source_text(run_dir: Path, index: str, source: dict) -> str`: `pages/<index>.txt` if present, plus `full_content` when its `squash` isn't already inside, plus `snippet`. No network.
  - `judge(statement: str, index: str, source: dict | None, text: str, gateway, tier: str) -> Judgement`
  - `score_run(run_dir: Path, question: Question, gateway, tier: str) -> dict`, which writes `run_dir / "referee.json"` and returns the summary
  - Summary keys: `statements, cited, uncited, supported, not_supported, unclear, referee_errors, facts_right, errors_through, prompt_version`
  - `statement_verdict(judgements: list[Judgement]) -> str`: `supported` if any is supported, else `not_supported` if any is, else `unclear`
  - `PROMPT_VERSION = 1`

Rules the tests pin:
- An uncited statement counts as `not_supported` (the pipeline promises every claim is cited) and is also counted in `uncited`. It makes no model call.
- A citation whose index isn't in `sources.json` gives `not_supported`, reason `"no such source"`, and makes no model call.
- A `supported` verdict whose quote fails `quote_in_source` becomes `unclear` with reason prefixed `"quote not in source: "`.
- `errors_through = not_supported / statements` (rounded to 3 places; `None` when there are no statements). `unclear` counts in the denominator, not the numerator.
- `facts_right` = the share of `question.expect` groups with any term matching `fact_pattern` in the `raw` text of a statement whose verdict is `supported`. `None` when the question has no `expect`.
- Passages: `best_passages(statement, text)` with up to 6 passages. Pass that count as an argument, or call it twice over halves; `PASSAGES` in `text.py` stays 3 for the pipeline. Add the snippet. The prompt asks for reasoning, then a final JSON `{"verdict": "supported|not_supported|unclear", "quote": "...", "reason": "..."}`. Parse the last JSON object in the reply. Call with `temperature=0`, `extra_body={"seed": 20261115, "chat_template_kwargs": {"enable_thinking": True}}`, `max_tokens=4000`, `purpose="referee"`, `private=True`.

- [ ] **Step 1: Write failing tests.** Use a fake gateway class with `ask()` returning `ChatResult`-like objects from a scripted list, and build a run folder in `tmp_path` with `report.md`, `sources.json` and `pages/1.txt`.

```python
def test_statements_are_verbatim_spans():
    s = statements("# Q\n\nBM25 uses term frequency [1]. It saturates [1].\n\n## Sources\n[1] x https://a")
    assert [x.raw.strip() for x in s] == ["BM25 uses term frequency [1].", "It saturates [1]."]

def test_supported_with_matching_quote(tmp_path): ...  # verdict "supported", quote taken from pages/1.txt
def test_quote_not_in_source_becomes_unclear(tmp_path): ...  # j.verdict == "unclear"; j.reason.startswith("quote not in source")
def test_referee_dangling_citation_is_not_supported(tmp_path):  # cites [7], sources has 1
    summary = score_run(run, q, gw, "referee"); assert summary["not_supported"] == 1 and gw.calls == 0
def test_uncited_counts_as_not_supported_without_a_call(tmp_path): ...
def test_referee_bad_reply_becomes_unclear(tmp_path):  # replies "nope", then ""
    j = ...; assert j.verdict == "unclear" and j.referee_error; assert gw.calls == 2
def test_facts_right_ignores_unsupported_sentences(tmp_path):
    # expect = [["term frequency"], ["saturation"]]; "term frequency" sentence supported, "saturation" sentence not_supported
    assert score_run(...)["facts_right"] == 0.5
def test_errors_through_denominator_includes_unclear(tmp_path):  # 1 supported, 1 not_supported, 2 unclear
    assert score_run(...)["errors_through"] == 0.25
def test_referee_reads_no_network(tmp_path):  # gateway fake only; any httpx use would fail: patch httpx.Client to raise
def test_referee_json_written(tmp_path):
    data = json.loads((run / "referee.json").read_text(encoding="utf-8"))
    assert data["summary"]["prompt_version"] == 1 and data["caveat"] == REFEREE_CAVEAT and len(data["judgements"]) == 2
```

- [ ] **Step 2: Run** `python -m pytest tests/test_referee.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `assistant/referee.py`.** `referee.json` holds `{"summary": …, "caveat": REFEREE_CAVEAT, "statements": [{text, raw, citations, verdict}], "judgements": [asdict(j)…]}`.
- [ ] **Step 4: Run** `python -m pytest tests/test_referee.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Referee: FACT-style scoring of a saved run (research bench, task 2)"`

---

### Task 3: Referee tier, pin and `assistant referee` command

**Files:**
- Modify: `assistant.toml` (add `[tiers.referee]`), `services/llama-swap/config.yaml` (model `"referee"` on `${env.REFEREE_GGUF}`, `-c 32768 ${lean}`, in its own `exclusive: true` group `"referee"`), `scripts/start_llama_swap.ps1` (read `REFEREE_GGUF` if set; don't fail when unset), `assistant/manifest.py` (add `REFEREE_GGUF` to `GGUF_ENVS`), `assistant/__main__.py`
- Test: `tests/test_referee.py`, `tests/test_cli.py`, `tests/test_assistant.py`

**Interfaces:**
- Consumes: Task 1's `load_bench`, `check_referee_pin`; Task 2's `score_run`; `evaluate.load_questions`, `evaluate.reload_model`.
- Produces:
  - `score_eval(eval_dir: Path, spec: BenchSpec, settings: Settings, gateway, *, env=os.environ, reloader=reload_model) -> list[dict]`. It checks the pin first, unloads the candidate once (`reloader(settings, spec.referee.tier)`), scores every `outcome == "ok"` row's run folder, and writes `eval_dir / "referee.csv"` (`question, run, mode, run_id` plus the summary keys) and `referee.md`. A run folder that already has a `referee.json` with the same `prompt_version` and referee sha256 is reused, not re-judged.
  - CLI: `python -m assistant referee runs/eval-<id> --bench evals/bench_v1.toml` (exit 0 when every ok run was scored, 1 otherwise)

`[tiers.referee]`: `kind = "local"`, `base_url = "http://127.0.0.1:1234/v1"`, `model = "referee"`, `label = "Referee (pinned, see evals/bench_v1.toml)"`, `temperature = 0.0`, `max_tokens = 4000`, `extra_body = { seed = 20261115 }`. `tests/test_assistant.py` checks that sampling settings match between `assistant.toml` and `bench.toml`. If that test covers every tier, exclude `referee` there with a comment.

- [ ] **Step 1: Write failing tests:** `test_score_eval_refuses_wrong_pin` (BenchSpecError, no gateway calls); `test_score_eval_skips_failed_runs` (a `no_report` row isn't scored and is absent from referee.csv); `test_score_eval_reuses_existing_referee_json`; `test_cli_referee_exit_codes` in `tests/test_cli.py`; `test_referee_tier_is_local_loopback` (load `assistant.toml`, `settings.tier("referee").kind == "local"`).
- [ ] **Step 2: Run** `python -m pytest tests/test_referee.py tests/test_cli.py tests/test_assistant.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the config entries, `score_eval`, the `referee` subcommand, and the manifest env.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Referee tier, pin check and 'assistant referee' (research bench, task 3)"`

---

### Task 4: Candidate runs, bench stamping and the attempt ledger

**Files:**
- Create: `assistant/ledger.py`, `results/bench_ledger.csv` (header only), `tests/test_ledger.py`
- Modify: `assistant/evaluate.py` (`run_eval`), `assistant/manifest.py` (`build_manifest`), `assistant/__main__.py` (`eval` flags)

**Interfaces:**
- Consumes: Task 1's `BenchSpec`, `candidate_key`; `manifest.sha256_file`, `manifest._git`.
- Produces:
  - `LEDGER_FIELDS = ("bench_version", "candidate", "candidate_key", "gguf_sha256", "git_commit", "split", "eval_id", "status", "started_utc", "note")`
  - `class RetestLimit(Exception)`
  - `MAX_TEST_ATTEMPTS = 3`
  - `check_attempt(ledger: Path, version: int, gguf_sha256: str, key: str) -> None`, which raises `RetestLimit` when the same `key` already has any test row for this version, or when this version already has `MAX_TEST_ATTEMPTS` test rows for `gguf_sha256`
  - `record_attempt(ledger: Path, row: dict) -> None` (append; `status` = `started`) and `finish_attempt(ledger: Path, eval_id: str, status: str) -> None` (`passed`, `missed` or `error`)
  - `run_eval(..., candidate_tier: str | None = None, bench: dict | None = None)`: with `candidate_tier`, local, codex and offline modes write with it and `verify_tier` defaults to it, so the candidate also checks and decides. `bench` is merged into `manifest.json` under `"bench"` (`version`, `split`, `candidate`, `candidate_key`, `candidate_gguf_sha256`).
  - CLI: `python -m assistant eval --bench evals/bench_v1.toml --candidate gemma-e4b --split dev|test`. With `--bench`, `--questions`, `--runs` and the Tavily setting come from the spec; `--mode` defaults to `local` and may add `hybrid` as a reference row. `--split test` requires `spec.frozen`, calls `check_referee_pin`, `check_attempt`, then `record_attempt` before the first question. `--split dev` records nothing.

- [ ] **Step 1: Write failing tests**

```python
def test_ledger_counts_started_attempts(tmp_path):
    led = tmp_path / "l.csv"; record_attempt(led, {... "candidate_key": "k1", "gguf_sha256": "g", "bench_version": 1, "split": "test", "status": "started"})
    with pytest.raises(RetestLimit): check_attempt(led, 1, "g", "k1")
def test_two_retests_then_refused(tmp_path):  # k1, k2, k3 recorded for g -> k4 raises; version 2 allowed
def test_other_model_unaffected(tmp_path):  # three rows for g; check_attempt(led, 1, "h", "x") passes
def test_dev_split_writes_no_ledger_row(tmp_path):  # cmd_eval via main([...--split dev]) with fake runner
def test_test_split_refused_when_not_frozen(tmp_path)  # exit code 2, message names "frozen"
def test_candidate_tier_used_for_writer_and_checker(tmp_path):
    # run_eval with candidate_tier="cand" (a second local Tier in settings_for); the fake model server sees every
    # proxy, verify and decide request addressed to model "cand"
def test_manifest_has_bench_block(tmp_path)
```

- [ ] **Step 2: Run** `python -m pytest tests/test_ledger.py tests/test_evaluate.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `ledger.py`, the `run_eval` parameters (thread `candidate_tier` into `_one` as the writer tier for non-cloud modes and as the default `verify_tier`), the manifest block, and the CLI. After the eval, `finish_attempt` sets `error` when any row's outcome isn't `ok` for all questions (the referee decides passed or missed later, in Task 6).
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "eval --bench/--candidate/--split with a committed attempt ledger and retest cap (research bench, task 4)"`

---

### Task 5: Calibration set and referee check

**Files:**
- Create: `assistant/calibration.py`, `tests/test_calibration.py`
- Modify: `assistant/__main__.py`

**Interfaces:**
- Consumes: Task 2's `statements`, `stored_source_text`, `judge`; `stats.wilson`; Task 1's `BenchSpec`.
- Produces:
  - `PAIR_FIELDS = ("pair_id", "question", "mode", "run_id", "statement", "index", "source_title", "source_url", "label", "labeller", "note")`
  - `sample_pairs(eval_dirs: list[Path], out: Path, dev_ids: set[str], n: int = 150, seed: int = 20261115, runs_dir: Path | None = None) -> list[dict]`. Only rows whose question is in `dev_ids` and `outcome == "ok"`, and only cited statements. Stratify by the pipeline's own verification verdict for that claim (from `verification.json`), aiming for one third each of supported, partial or unsupported, and claims repair removed (from `repair.json`), so hard cases are in. Top up from whatever remains. Raise `ValueError` when an eval holds a question not in `dev_ids`.
  - `spot_check(labelled: Path, out: Path, n: int = 25, seed: int = 20261116) -> list[dict]`: a random `n` labelled rows written with `label` and `labeller` blanked, for Van
  - `judge_pairs(labelled: Path, runs_dir: Path, gateway, tier: str) -> list[dict]` (adds `referee_verdict`)
  - `recall(rows: list[dict]) -> dict`: `{"supported": (hits, total, wilson), "not_supported": (...), "unclear_labels": int}`. Rows labelled `unclear` are excluded from both recalls.
  - `meets(result: dict, spec: BenchSpec) -> bool`
  - `agreement(claude: Path, van: Path) -> tuple[int, int]` (agreeing, compared)
  - CLI: `python -m assistant referee calibrate sample <eval dirs> --bench … --out results/calibration_v1.csv`; `… calibrate spot results/calibration_v1.csv --out results/calibration_v1_spot.csv`; `… calibrate agree results/calibration_v1.csv results/calibration_v1_spot.csv` (prints "Van disagrees on N of 25", exit 1 when N > 3); `… calibrate run results/calibration_v1.csv --bench … [--tier referee]` (prints recalls and PASS or FAIL against the spec minimums, writes `results/calibration_v1_<tier>.md`)

- [ ] **Step 1: Write failing tests:** `test_sample_refuses_test_questions`; `test_sample_is_seeded_and_stratified` (same seed gives the same ids; at least one pair from each stratum present when available); `test_spot_check_blanks_labels`; `test_recall_excludes_unclear`; `test_meets_uses_spec_minimums` (0.92 and 0.86 pass; 0.92 and 0.80 fail); `test_agreement_counts`.
- [ ] **Step 2: Run** `python -m pytest tests/test_calibration.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `calibration.py` and the subcommands.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Referee calibration: sample, spot-check, judge, recall against spec minimums (research bench, task 5)"`

---

### Task 6: Fit checks and the scorecard

**Files:**
- Create: `assistant/fit.py`, `assistant/scorecard.py`, `tests/test_fit.py`, `tests/test_scorecard.py`
- Modify: `assistant/__main__.py`, `assistant/evaluate.py` (offload hook in `run_eval`: `offload_probe: Callable[[], tuple[int, int] | None] | None = None`, called once after the first question)

**Interfaces:**
- Consumes: Task 1's `BenchSpec`, `FitLimits`, `PassLine`; Task 3's `referee.csv`; `summary.csv` and `manifest.json` per eval; Task 4's `finish_attempt`.
- Produces:
  - `parse_offload(log_text: str) -> tuple[int, int] | None`: the last `offloaded (\d+)/(\d+) layers to GPU` in a llama-server log
  - `fetch_llama_swap_log(base_url: str, get=httpx.get) -> str` (GET `<root>/logs`, the llama-swap log endpoint; returns "" on any error). Verify the path on the PC in Task 9. If it differs, fix it there.
  - `fit_failures(rows: list[dict], limits: FitLimits, offload: tuple[int, int] | None) -> list[str]`, with codes from `{"gpu", "ram", "time", "errors"}`. `gpu` fails when offload is known and n < m. When unknown, it's reported as `gpu?` in the scorecard but not failed. `time` uses `research_s + verify_s + repair_s` > `max_report_minutes * 60` on any ok row. `errors` uses the share of rows with outcome other than `ok` > `max_error_share`.
  - `eval --bench` passes `offload_probe=lambda: parse_offload(fetch_llama_swap_log(tier.base_url))`, and `run_eval` saves the result into `manifest.json` `bench.offload`.
  - `per_question(eval_dir: Path) -> dict[str, dict]`: question → `{facts_right, errors_through, minutes}`. A failed run gives `facts_right = 0.0`, `errors_through = None` and minutes from what ran.
  - `paired_bootstrap(a: dict[str, float], b: dict[str, float], n: int = 10000, seed: int = 20261115) -> tuple[float, float, float]` (mean difference a−b and its 95% percentile interval, over shared questions, skipping None)
  - `rank(cards: list[Card], pass_line: PassLine) -> list[Card]`, with `@dataclass Card: candidate, candidate_key, eval_id, bench_version, fit: list[str], facts_right, facts_ci, errors_through, errors_ci, median_minutes, per_question, flags: list[str], passes: bool`
  - `scorecard_markdown(cards, spec, calibration_md: str | None) -> str` and `write_scorecard(out: Path, cards, spec)` (`.md` plus `.csv`)
  - CLI: `python -m assistant scorecard runs/eval-A runs/eval-B … --bench evals/bench_v1.toml --out results/bench_v1_scorecard.md`. It refuses to put evals with different bench versions or splits on one card. On the test split, it calls `finish_attempt(..., "passed" | "missed")` per eval.

Ranking algorithm (the tests determine it): fit failures are listed last and unranked. Among fit passes, sort with a comparison function. A beats B on facts if the `paired_bootstrap` interval of facts (A−B) lies wholly above 0. If facts are tied (the interval contains 0), compare errors through: lower wins when its interval lies wholly on that side. If both are tied, the lower `median_minutes` wins. `facts_ci` and `errors_ci` per card are percentile bootstrap intervals over questions. `passes = not fit and facts_right >= min_facts_right and errors_through <= max_errors_through`. `flags` includes `"referee family"` when the candidate's GGUF file name and the referee's share the first token (split on `-` or `_`, case-insensitive, for example `gemma` or `qwen3.5`). Every scorecard prints `REFEREE_CAVEAT` and the referee's calibration recalls.

- [ ] **Step 1: Write failing tests:** `test_parse_offload` (two lines, the last one wins; None when absent); `test_fit_ram_time_errors` (one test per code, with the spec's exact limits 2048, 60, 0.10); `test_gpu_unknown_is_not_a_failure`; `test_quality_counts_failed_run_as_zero_facts`; `test_paired_bootstrap_identical_is_tied` (interval contains 0); `test_rank_quality_beats_speed` (A clearly more facts and slower ranks above B); `test_rank_tie_goes_to_faster` (equal per-question facts and errors; the faster one is first); `test_rank_fit_failure_last`; `test_scorecard_refuses_mixed_versions`; `test_scorecard_has_caveat`; `test_referee_family_flag`.
- [ ] **Step 2: Run** `python -m pytest tests/test_fit.py tests/test_scorecard.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `fit.py`, `scorecard.py`, the offload hook and the `scorecard` subcommand.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Fit checks and bench scorecard with paired bootstrap tie rule (research bench, task 6)"`

---

### Task 7: Test-question validator and README

**Files:**
- Create: `tests/test_bench_questions.py`
- Modify: `README.md` (a new "Research bench" section: what it is, the commands in order, the four loop rules from spec 6.8, the caveat), `assistant/evaluate.py` docstring (one paragraph pointing to the bench)

**Interfaces:**
- Consumes: Task 1's `load_bench`; `evaluate.load_questions`, `fact_pattern`.

The validator runs on every file in the spec's `test` split and is skipped (`pytest.skip("test questions not written yet")`) while `evals/bench_test_questions.toml` doesn't exist. It also runs on the dev files for checks 1, 2 and 6.

- [ ] **Step 1: Write the tests:**
  1. `test_question_ids_unique_across_splits`
  2. `test_dev_and_test_disjoint` (no shared id or identical text)
  3. `test_test_questions_count` (≥ 25)
  4. `test_test_questions_public_and_not_codex`
  5. `test_no_fact_leaks_into_question_text`: for every test question, no `expect` term matches `fact_pattern` in `question.text`
  6. `test_every_question_has_expect_groups` (≥ 3 groups, no empty group)
- [ ] **Step 2: Run** `python -m pytest tests/test_bench_questions.py -v`. Expected: the dev-file checks PASS and the test-file checks SKIP.
- [ ] **Step 3: Write the README section.**
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Bench question checks and README section (research bench, task 7)"`, push the branch, and open the PR (code only; the bench isn't frozen yet).

---

### Task 8: Test questions (fresh session, Van approves)

Not code. It's done by a **fresh session that has seen no eval results, run folders, claim labels or checker behaviour**. Hand it only: spec section 6.7, the topic list (Python, software engineering, cloud architecture and engineering, ML, AI, LLM agents, RAG, local models, data science and analytics), the `expect` conventions (the header of `evals/heldout_questions.toml`, without its questions), and the dev question texts so it can avoid them.

- [ ] **Step 1:** The fresh session writes `evals/bench_test_questions.toml` with 27 questions, so two can be dropped in review. Each has `public = true`, no `codex`, and 3–5 `expect` groups of facts a correct answer states.
- [ ] **Step 2:** It runs `python -m pytest tests/test_bench_questions.py -v`. Expected: PASS (no skips).
- [ ] **Step 3:** Post the questions to Van for review. He approves, edits or drops questions until 25 or more remain.
- [ ] **Step 4:** Commit with "Test questions approved by Van, <date>" in the message.

---

### Task 9: Calibrate, pick the referee, freeze, run round 1 (on Van's PC)

Runs on the PC through Remote Control after the PR from Task 7 merges. Each command's output goes into `results/bench_v1_freeze.md`.

- [ ] **Step 1: Dev data.** Use the saved dev evals in `runs/` (the 11-14 and held-out evals). If there are too few cited claims for 150 pairs, run `python -m assistant eval --bench evals/bench_v1.toml --candidate gemma-e4b --split dev --mode local`.
- [ ] **Step 2: Sample.** Run `python -m assistant referee calibrate sample <dev eval dirs> --bench evals/bench_v1.toml --out results/calibration_v1.csv`. Expected: 150 rows, no test question ids.
- [ ] **Step 3: Claude labels** all 150 against the stored source text, with the strict rule (overstating counts as not supported), with `labeller = "Claude"`.
- [ ] **Step 4: Spot-check.** Run `… calibrate spot …` and send `results/calibration_v1_spot.csv` to Van. When it comes back, run `… calibrate agree …`. Expected: "Van disagrees on N of 25" with N ≤ 3. If N > 3, stop and revisit the labelling rule with Van before going further.
- [ ] **Step 5: Referee candidates.** For each of Qwen3.5 9B Q4_K_M, Gemma 4 12B QAT, and Granite 4.2 8B (if `D:\models` has it), set `REFEREE_GGUF`, restart llama-swap, and run `… calibrate run results/calibration_v1.csv --bench evals/bench_v1.toml`. Check the llama-swap `/logs` path from Task 6 here. Expected: recalls with intervals per model. Prompt changes are allowed only in this step, and each change bumps `PROMPT_VERSION` and is re-run on all three.
- [ ] **Step 6: Pick.** Choose the model with the higher minimum of the two recalls among those meeting both minimums. If none meet them, try a Q6 or Q8 quant of the best one. If that still fails, stop and report the numbers to Van. Never lower the minimums.
- [ ] **Step 7: Van confirms the fit limits and pass line,** then freeze: set `referee.sha256`, `prompt_version`, `frozen = true` and `frozen_on`. Commit `evals/bench_v1.toml` and `results/bench_v1_freeze.md` together.
- [ ] **Step 8: Round 1.** Run `python -m assistant eval --bench evals/bench_v1.toml --candidate gemma-e4b --split test --mode local --mode hybrid`, then the same with `--candidate qwen-9b --mode local`, both within the same three days. Then run `python -m assistant referee runs/eval-<id> --bench …` for each, then `python -m assistant scorecard <both evals> --bench … --out results/bench_v1_scorecard.md`.
- [ ] **Step 9: Stage 2 result.** Report the scorecard to Van with the caveat. Stage 2 passes if the default meets the pass line. If it misses, the ledger allows two retests after dev-split pipeline work.

---

## Self-review notes

- **Spec coverage:** 6.1 → T1 and T4; 6.2 → T2 and T3; 6.3 → T5 and T9; 6.4 → T6; 6.5 → T6; 6.6 → T1 and T6; 6.7 → T7 and T8; 6.8 → T1 (header), T4 (ledger, refusal) and T7 (README); 6.9 → T3 and T6; section 2 retests → T4; section 2 hand work → T5 and T8.
- **Extraction choice (spec section 8, builder's call):** statements come from the deterministic `split_claims`, so they are verbatim spans by construction. No model rewrites them. Duplicates are merged.
- **Failed run counts as 0 facts:** Claude's call, added in Review Focus 3, so failing hard questions can't raise a score.
