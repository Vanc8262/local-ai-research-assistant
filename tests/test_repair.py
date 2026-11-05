import json

import httpx
import pytest

import fake_model
from assistant.config import Settings, Tier
from assistant.gateway import Gateway
from assistant.repair import RepairError, _only_deletes, _tidy, _with_citations, repair_run
from assistant.runlog import RunLog
from assistant.verify import split_claims, verify_run

PAGE_ONE = "QLoRA stores the base model in 4-bit NF4. Double quantization saves 0.37 bits per parameter."
PAGE_TWO = "Paged optimizers move optimizer state between GPU and CPU memory when the GPU runs out."
SOURCES = [{"index": "1", "title": "QLoRA", "link": "https://example.test/one"},
           {"index": "2", "title": "Paging", "link": "https://example.test/two"}]
REPORT = """# How does QLoRA save memory?

## Storage

QLoRA stores the base model in 4-bit NF4 [1].
Double quantization saves 0.37 bits per parameter and halves training time [1].

## Paging

- Paged optimizers move optimizer state between GPU and CPU memory when the GPU runs out.
- QLoRA was invented by a committee of penguins working on Mars [2].

## Opinion

**Outlook**

Everyone will use this technique for every model by next year, without any doubt.

## Sources

[1] QLoRA
    https://example.test/one
[2] Paging
    https://example.test/two
"""


def settings_for(tmp_path) -> Settings:
    settings = Settings(runs_dir=tmp_path / "runs", default_tier="local")
    settings.tiers["local"] = Tier(name="local", kind="local", base_url="http://local/v1", model="gemma")
    return settings


def make_run(tmp_path, report=REPORT):
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    (run_dir / "sources.json").write_text(json.dumps(SOURCES), encoding="utf-8")
    return run_dir


def judge(prompt: str) -> dict:
    """A careful model: it supports what the pages say and nothing else."""
    if "failed a citation check" in prompt:
        if "halves training time" in prompt:
            return {"action": "rewrite", "sentence": "Double quantization saves 0.37 bits per parameter."}
        return {"action": "remove", "sentence": ""}
    claim = prompt.split("Claim: ", 1)[1].split("\n", 1)[0]
    if "halves" in claim or "penguins" in claim or "Everyone" in claim:
        return {"verdict": "unsupported", "quote": "", "reason": "not stated"}
    for page in (PAGE_ONE, PAGE_TWO):
        for sentence in page.split(". "):
            if sentence.rstrip(".").lower() in claim.lower() and f"{sentence.rstrip('.')}" in prompt:
                return {"verdict": "supported", "quote": sentence, "reason": "stated"}
    return {"verdict": "unsupported", "quote": "", "reason": "not stated"}


def transport(prompts: list, fail: bool = False):
    def handler(request):
        if request.url.host == "local":
            if fail:
                return httpx.Response(500, text="model crashed")
            prompt = json.loads(request.content)["messages"][0]["content"]
            prompts.append(prompt)
            return httpx.Response(200, content=fake_model.sse({"content": json.dumps(judge(prompt))}))
        page = PAGE_ONE if request.url.path == "/one" else PAGE_TWO
        return httpx.Response(200, text=f"<p>{page}</p>", headers={"content-type": "text/html"})
    return httpx.MockTransport(handler)


def repaired(tmp_path, prompts):
    run_dir = make_run(tmp_path)
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=transport(prompts))
    verify_run(run_dir, gateway, log, transport=transport(prompts))
    first_check = len(prompts)
    result = repair_run(run_dir, gateway, log, transport=transport(prompts))
    gateway.close()
    return run_dir, result, first_check


def test_repair_cites_rewrites_and_removes_until_the_report_passes(tmp_path):
    prompts = []
    run_dir, result, _ = repaired(tmp_path, prompts)
    report = (run_dir / "report.md").read_text(encoding="utf-8")

    assert {c.action: c.before for c in result.changes if c.action != "removed"} == {
        "cited": "Paged optimizers move optimizer state between GPU and CPU memory when the GPU runs out.",
        "rewritten": "Double quantization saves 0.37 bits per parameter and halves training time .",
    }
    assert result.count("removed") == 2
    assert result.before["citation_coverage"] < 1 and result.before["citation_accuracy"] < 1
    assert result.after["citation_coverage"] == 1 and result.after["citation_accuracy"] == 1
    assert "Paged optimizers move optimizer state between GPU and CPU memory when the GPU runs out [2]." in report
    assert "Double quantization saves 0.37 bits per parameter [1]" in report
    assert "penguins" not in report and "halves" not in report and "Everyone" not in report


def test_repair_keeps_the_original_and_lists_every_change(tmp_path):
    run_dir, result, _ = repaired(tmp_path, [])
    assert (run_dir / "report.original.md").read_text(encoding="utf-8") == REPORT
    assert (run_dir / "verification.original.json").exists()
    saved = json.loads((run_dir / "repair.json").read_text(encoding="utf-8"))
    assert len(saved["changes"]) == len(result.changes) == 4
    listing = (run_dir / "repair.md").read_text(encoding="utf-8")
    assert "penguins" in listing and "1 cited, 1 rewritten, 2 removed" in listing


def test_repair_only_asks_about_changed_sentences(tmp_path):
    prompts = []
    _, _, first_check = repaired(tmp_path, prompts)
    # each failed claim tries the other sources, rewrites ask once each, then 1 check of the new sentence
    repeated = [p for p in prompts[first_check:] if "QLoRA stores the base model" in p.split("Passages")[0]]
    assert repeated == []
    assert len(prompts) - first_check <= 1 + 1 + 2 + 2 + 2 + 1


def test_repair_leaves_the_sources_section_and_a_second_repair_changes_nothing(tmp_path):
    prompts = []
    run_dir, _, _ = repaired(tmp_path, prompts)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert report.endswith(REPORT[REPORT.index("## Sources"):])
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=transport(prompts))
    again = repair_run(run_dir, gateway, log, transport=transport(prompts))
    gateway.close()
    assert again.changes == []
    assert (run_dir / "report.md").read_text(encoding="utf-8") == report
    assert (run_dir / "report.original.md").read_text(encoding="utf-8") == REPORT


def test_a_model_failure_stops_the_repair_and_restores_the_report(tmp_path):
    prompts = []
    run_dir = make_run(tmp_path)
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=transport(prompts))
    verify_run(run_dir, gateway, log, transport=transport(prompts))
    gateway.close()

    gateway = Gateway(settings, log, transport=transport([], fail=True))
    with pytest.raises(RepairError):
        repair_run(run_dir, gateway, log, transport=transport([], fail=True))
    gateway.close()
    assert (run_dir / "report.md").read_text(encoding="utf-8") == REPORT


def test_a_model_failure_reloads_and_tries_once_more(tmp_path):
    prompts, reloads, failures = [], [], [True]
    working = transport(prompts)

    def handler(request):
        if request.url.host == "local" and failures:
            failures.pop()
            return httpx.Response(500, text="out of memory")
        return working.handler(request)

    flaky = httpx.MockTransport(handler)
    run_dir = make_run(tmp_path)
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=flaky)
    result = repair_run(run_dir, gateway, log, transport=flaky, reloader=lambda: reloads.append(1))
    gateway.close()
    assert reloads == [1] and result.after["citation_accuracy"] == 1


def test_citations_lent_by_a_removed_sentence_stay_on_the_borrower(tmp_path):
    prompts = []
    report = ("# Q\n\n- Double quantization saves 0.37 bits per parameter. QLoRA was invented by a "
              "committee of penguins working on Mars [1].\n\n## Sources\n\n[1] QLoRA\n    https://example.test/one\n")
    run_dir = make_run(tmp_path, report)
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=transport(prompts))
    verify_run(run_dir, gateway, log, transport=transport(prompts))
    result = repair_run(run_dir, gateway, log, transport=transport(prompts))
    gateway.close()
    assert "Double quantization saves 0.37 bits per parameter [1]." in (run_dir / "report.md").read_text(encoding="utf-8")
    assert result.after["uncited"] == 0 and result.after["claims"] == 1


def repair_one(tmp_path, sentence: str, answers: dict):
    """A one-sentence report; `answers` maps a word in the prompt to the model's reply."""
    report = f"# Q\n\n{sentence}\n\n## Sources\n\n[1] QLoRA\n    https://example.test/one\n"
    run_dir = make_run(tmp_path, report)

    def handler(request):
        if request.url.host == "local":
            prompt = json.loads(request.content)["messages"][0]["content"]
            kind = "rewrite" if "failed a citation check" in prompt else "check"
            source = "two" if "(Paging)" in prompt else "one"
            return httpx.Response(200, content=fake_model.sse({"content": json.dumps(answers[kind, source])}))
        page = PAGE_ONE if request.url.path == "/one" else PAGE_TWO
        return httpx.Response(200, text=f"<p>{page}</p>", headers={"content-type": "text/html"})

    mock = httpx.MockTransport(handler)
    settings = settings_for(tmp_path)
    log = RunLog(settings.runs_dir)
    gateway = Gateway(settings, log, transport=mock)
    result = repair_run(run_dir, gateway, log, transport=mock)
    gateway.close()
    return result, (run_dir / "report.md").read_text(encoding="utf-8")


NO = {"verdict": "unsupported", "quote": "", "reason": "not stated"}


def test_a_rewrite_that_changes_the_claim_to_fit_its_source_is_refused(tmp_path):
    result, report = repair_one(tmp_path, "The AWS framework has six pillars for reviewing cloud workloads [1].", {
        ("check", "one"): NO, ("check", "two"): NO,
        ("rewrite", "one"): {"action": "rewrite", "sentence": "The AWS framework has five pillars for reviewing cloud workloads."},
    })
    assert [c.action for c in result.changes] == ["removed"]
    assert "five" not in report and "six" not in report


def test_a_claim_moves_to_another_source_that_supports_it_as_written(tmp_path):
    sentence = "Paged optimizers move optimizer state between GPU and CPU memory when the GPU runs out"
    result, report = repair_one(tmp_path, f"{sentence} [1].", {
        ("check", "one"): NO,
        ("check", "two"): {"verdict": "supported", "quote": PAGE_TWO, "reason": "stated"},
    })
    assert [c.action for c in result.changes] == ["cited"]
    assert f"{sentence} [2]." in report
    assert result.after["citation_accuracy"] == 1


def test_a_move_needs_a_quote_that_shares_the_claims_words(tmp_path):
    result, report = repair_one(tmp_path, "The framework has six distinct pillars covering every cloud workload design [1].", {
        ("check", "one"): NO,
        ("check", "two"): {"verdict": "supported", "quote": PAGE_TWO, "reason": "generous"},
        ("rewrite", "one"): {"action": "remove", "sentence": ""},
    })
    assert [c.action for c in result.changes] == ["removed"] and "six" not in report


def test_a_rewrite_may_only_delete_words():
    original = "Double quantization saves 0.37 bits per parameter and halves training time ."
    assert _only_deletes("Double quantization saves 0.37 bits per parameter.", original)
    assert not _only_deletes("Double quantization saves 0.5 bits per parameter.", original)
    assert not _only_deletes("Double quantization reduces training time.", original)


def test_citations_go_before_the_closing_punctuation():
    assert _with_citations("It saves memory.", ["1", "2"]) == "It saves memory [1][2]."
    (claim,) = split_claims("It saves a great deal of memory overall [1][2]. ")
    assert claim.citations == ["1", "2"]


def test_tidy_drops_what_removals_leave_behind():
    report = ("# Title\n\n## Empty\n\n**Label**\n\n- \n\n## Kept\n\nThe main points include:\n\n"
              "### Sub\n\nA real sentence.\n\n## Sources\n\n[1] x\n")
    assert _tidy(report) == "# Title\n\n## Kept\n\n### Sub\n\nA real sentence.\n\n## Sources\n\n[1] x\n"


def test_tidy_keeps_lists_and_the_line_that_introduces_them():
    report = ("# Title\n\nThe six pillars are [1]:\n1. **Security**\n2. **Sustainability**\n\n"
              "## Next\n\nA real sentence.\n\n## Sources\n\n[1] x\n")
    assert _tidy(report) == report


def test_numbered_list_labels_are_not_claims():
    report = ("1. **Performance Efficiency** (also referred to as Performance)\n"
              "2.  **4-bit Quantization of the Base Model Weights**\n"
              "3. The pillar covers selecting resource types and sizes for each workload.\n")
    assert [c.text for c in split_claims(report)] == [
        "The pillar covers selecting resource types and sizes for each workload."]
