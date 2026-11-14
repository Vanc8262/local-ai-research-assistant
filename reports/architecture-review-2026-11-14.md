# Architecture review — 2026-11-14

This is a read-only structural review by the `ecc:architect` agent. It covers `assistant/`, `bench/` (except `bench/data/code_tasks/`), `tests/`, `pyproject.toml`, `vault.toml`, `assistant.toml` and `bench.toml`.

- No files were edited, no tests were run and no models were run.
- **Confirmed** means the agent read the code at that line. After the agent finished, the main session read the code again for findings 1, 2, 4, 5, 6 and 11, and they hold.
- **Suspected** means the impact was not shown in code.

## Findings, most important first

### 1. `assistant verify` exits 0 even when nothing was checked (confirmed)
- **Where:** `assistant/__main__.py:252`. The exit code fails only on `uncited`, `unsupported` or `dangling_citations`.
- **What's wrong:** `partial` and `unchecked` claims are ignored. A claim becomes "unchecked" when:
  - the model is down (`verify.py:408-409`, then `parse_verdict("")` at `verify.py:281`);
  - the tier is not ready (`verify.py:405-407`);
  - the source can't be read (`verify.py:392-393`).
  So if llama-swap is down, every claim is unchecked and verify still passes. A report with no claims also passes.
- **Inconsistency:** `_repair` (`__main__.py:278`) does count `partial`.
- **Missing caveat:** `verification.md` (`verify.py:522-546`) never says that a pass means the checker accepts the report, not that every sentence is true. Only `evaluate.py:219` says it.
- **Why it matters:** this is the overclaim the CLAUDE.md rule warns about, and here it can happen with zero checks run. No test covers the verify CLI exit code.
- **Smallest fix:**
  - also fail on `summary["unchecked"]`, `summary["partial"]`, or zero claims;
  - add the caveat line to `verification_markdown`.

### 2. Privacy relies only on the `kind` label; a "local" tier's URL is never checked (confirmed)
- **Where:** `gateway.py:47` (`tier.kind != "local"`), `decide.py:221` and `research.py:99` all trust `Tier.kind`. `Tier.__post_init__` (`config.py:27-31`) only checks that `kind` is "local" or "cloud".
- **What's wrong:** a tier block in `assistant.toml` with `kind = "local"` and a remote `base_url` (a typo or a copied block) sends private prompts off the PC with no warning.
- **What already exists:** `codex.py:294-298` already checks hostnames against `LOOPBACK`.
- **Smallest fix:** in `Tier.__post_init__`, raise if `kind == "local"` and `urlparse(base_url).hostname` is not in `LOOPBACK`.

### 3. Search queries from private research runs leave the PC by default (confirmed; Van's decision)
- **Where:**
  - `research.py:122-128` builds the router with no privacy input;
  - `make_router` (`research.py:204-212`) takes only `offline`;
  - `sources.py:325-329` queries the live APIs and SearXNG whenever `offline` is false.
- **What happens:** a private `assistant research "q"` sends the question and LDR's derived queries to:
  - arXiv, OpenAlex, Hugging Face papers, Stack Exchange and Hacker News;
  - GitHub, with `GITHUB_TOKEN` attached (`sources.py:180-181`), which ties the queries to Van's account;
  - SearXNG, which forwards them to Google, Brave and DuckDuckGo.
- **Inconsistency:** Tavily is blocked for private runs (`research.py:40, 99`), but these services are not.
- **Is it a violation?** Not strictly: the rule says "cloud tier", and `sources.py:20` and `search.py:27` document this. But it is default-allow egress sitting next to a default-deny model check.
- **Smallest fix, if wanted:** pass `private` into `make_router`. For private runs without `public_search`, set `live=[]` and `searxng_url=""`, or make `offline` the private default. The eval is unaffected because it uses `public_search=True`.

### 4. The bench `code` suite gives model-written code every secret, and runs by default (confirmed)
- **Where:** `bench/suites/code_editing.py:87-92` calls `subprocess.run` with no `env=`.
- **What's wrong:**
  - the child process inherits `GEMINI_API_KEY`, `TAVILY_API_KEY`, `HF_TOKEN` and `GITHUB_TOKEN`, and has full network and file access;
  - the docstring (lines 4-5, "throwaway directory in a separate process") reads like isolation;
  - `code` is in the default `--suites` (`bench/__main__.py:136`), so `bench run --model X` runs it without being asked.
- **Smallest fix:**
  - pass a minimal `env` (PATH, SYSTEMROOT, TEMP);
  - say "not a sandbox" in the docstring;
  - leave `code` out of the default `--suites`.

### 5. The LDR subprocess gets every cloud key, even in private runs (confirmed that it happens; impact suspected)
- **Where:** `research.py:160` sets `env = {**os.environ, ...}`. LDR is third-party code.
- **Overclaiming test:** `test_research.py:144` is named "...key stays out of ldr", but it checks only the job JSON, not the subprocess environment.
- **Smallest fix:** remove each tier's `api_key_env` plus `TAVILY_API_KEY`, `HF_TOKEN` and `GITHUB_TOKEN` from `env`, and pass only the keys a public run needs.

### 6. The bench config reads an API key from a file in the repo (confirmed)
- **Where:** `bench/config.py:51` accepts `api_key` from `[server]` in `bench.toml`. `bench.toml:8` currently holds the placeholder `"local"`.
- **Why it matters:** this breaks "keys come from environment variables, never a file".
- **Smallest fix:** remove `"api_key"` from the tuple at line 51 and delete `bench.toml:8`. The key then comes from `BENCH_API_KEY` or the default.

### 7. The assistant's core depends on the bench package (confirmed)
- **Where:** `gateway.py:9` and `proxy.py:19` import `bench.client`; `evaluate.py:32` imports `bench.sysmon`. Bench never imports assistant, so there is no cycle.
- **Why it matters:** the product's model client lives inside the test harness, so a bench change can break the gateway.
- **Smallest fix:** move `client.py` and `sysmon.py` into `assistant/` (or a small shared package) and have bench import from there.

### 8. Text helpers in `verify.py` cause an import cycle and leak into LDR's process (confirmed)
- **The cycle:** `sources.py:39` and `web_pages.py:25` import `html_to_text` and `best_passages` from `verify.py`. That pulls in verify, then gateway, then config, then sources again. The deferred import at `config.py:78` works around this cycle.
- **LDR side effect:** `ldr_runner.py:80-81` imports `web_pages` inside LDR's venv, which drags in `assistant.verify`, `assistant.gateway` and `bench.client`. This contradicts `ldr_runner.py:4-5` ("imports nothing from the assistant package").
- **Smallest fix:** move `html_to_text`, `best_passages` and their helpers into a new `assistant/text.py` that imports nothing from the package.

### 9. `repair.py` depends on seven private functions in `verify.py` (confirmed)
- **Where:** `repair.py:37-39` imports `_check`, `_is_lead_in`, `_check_key`, `_json_object`, `_ReloadingGateway`, `_source_text` and `_squash`.
- **Duplication:** the citation-check HTTP client is built identically in two places (`verify.py:360-361`, `repair.py:122-123`).
- **Why it matters:** an internal change to verify can silently break repair.
- **Smallest fix:** drop the underscores on the shared names and add one `citation_client(transport)` helper.

### 10. The eval treats every question as public (confirmed)
- **Where:** `evaluate.py:45` (hybrid mode is `("gemini", "tavily", True)`) and `evaluate.py:144` (`public_search=True` for all modes). `Question` (`evaluate.py:59-65`) has no privacy field.
- **Why it matters:** a private question added to `evals/research_questions.toml` would go to Gemini and Tavily.
- **Smallest fix:** add `public: bool = False` to `Question`. When it is false, skip hybrid mode and set `public_search=False`.

### 11. Dead code (confirmed by grep)
- `ChatResult.to_record` (`bench/client.py:88`): nothing calls it. The only `to_record` caller, `decide_check.py:56`, uses the one in `decide.py`.
- `Decider.decide` (`decide.py:197`): nothing calls it, including tests.
- `backup_tier` (`config.py:87, 124`): it is only validated and printed (`__main__.py:63`). The proxy falls back to `default_tier` instead (`proxy.py:79`). Either wire it in or document it as a label only.

### 12. Small duplication and drift (confirmed)
- `LOOPBACK` is defined twice (`codex.py:47`, `web_pages.py:28`).
- The sampling settings for gemma-4-e4b-qat and qwen3.5-9b are repeated in `assistant.toml` and `bench.toml`.
- The default `base_url` in `bench/config.py:27` is `http://localhost:1234/v1`, against the project's use of 127.0.0.1.
- The `run_dir` resolution is copied in `cmd_verify` and `cmd_repair` (`__main__.py:230, 256`).

### 13. Test gaps and brittleness (confirmed)
- **No CLI tests** for the exit codes of `verify`, `repair`, `research --verify/--repair` or `eval`. That is how finding 1 went unnoticed.
- **Brittle:**
  - `test_vault.py:264` (`test_shipped_vault_toml_has_no_picks_yet`) will fail the first time Van picks a model, so the correct action breaks the suite;
  - `test_assistant.py:35` pins the shipped model name.
- **Fix:** test only the structure of the shipped configs (the file parses, local tiers are loopback, cloud tiers name an env var), not their contents.

### 14. Minor (confirmed)
- `proxy.py:80`: the fallback `gateway.ask` is not wrapped. A `TierNotReady` would drop the connection instead of returning a 503.
- `__main__.py:16`: the `repair` line in the usage text is not indented.

## Fix these first
1. **Verify exit code:** fail on unchecked, partial or zero claims, and add the "checker accepts, not true" caveat to `verification.md` (finding 1).
2. **Close privacy and secret leaks:** reject local tiers whose URL is not loopback (finding 2), and strip secrets from the environment of the `code` suite and LDR subprocesses (findings 4 and 5).
3. **Private search:** decide whether private research may use the live and SearXNG search layers (finding 3). If not, gate `make_router` on privacy.

## Checked and clean
- **Model privacy is default-deny:**
  - `Gateway.ask(private=True)` is the default, and the check is `kind != "local"` (`gateway.py:40, 47`).
  - CLI `ask` passes `private=not args.public` (`__main__.py:78`). The proxy defaults to private (`__main__.py:144`, `proxy.py:34`).
  - Research defaults to `public=False` (`research.py:84, 99`), and codex research refuses `--public` (`research.py:96`).
  - Verify and repair default to private (`verify.py:348`, `repair.py:107`), and `research --verify` forces private (`__main__.py:225`).
  - The decider refuses non-local tiers (`decide.py:219-224`).
  - Codex embeddings must be on loopback (`codex.py:294-298`).
  - Privacy tests exist in `test_assistant`, `test_proxy`, `test_research`, `test_decide` and `test_codex_index`.
- **Keys come from the environment:**
  - tier keys (`config.py:35`), Tavily (`research.py:104, 125`), source keys (`sources.py:153, 180, 200`) and `HF_TOKEN` (`vault.py:237`);
  - `assistant.toml` holds no keys, and `.gitignore` covers `.env*`;
  - the one exception is bench (finding 6).
- **`vault.toml` is never written:** `vault.py` only reads it (`load_picks`, line 446). Bench never touches the vault.
- **Coupling direction:** bench does not import assistant.

## Not covered
- `scripts/*.ps1`, `services/`, the codex catalog, and the contents of `evals/*.toml`.
- The research, vision and headroom bench suites beyond grep.
- The test suite was not run.
