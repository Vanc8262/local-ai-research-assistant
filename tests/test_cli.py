"""Exit codes of the command line, run through `assistant.__main__.main` with mock servers."""
import json
from types import SimpleNamespace

import httpx
import pytest

import fake_model
from assistant import __main__ as cli
from assistant.gateway import Gateway
from assistant.verify import CAVEAT, failures

CONFIG = """
[assistant]
default_tier = "local"
runs_dir = "{runs}"

[tiers.local]
kind = "local"
base_url = "http://127.0.0.1:1234/v1"
model = "gemma"
"""
REPORT = """# Lazy imports

Python 3.15 adds lazy imports through the new lazy keyword [1].

## Sources

[1] Codex chunk
"""
SOURCES = [{"index": "1", "title": "Codex chunk", "link": "file:///codex/python.pdf#page=3",
            "full_content": "Python 3.15 introduces lazy imports: the new lazy keyword defers loading a module."}]


class NoDecider:
    """Stands in for the decider, which asks for logprobs; it never overrules a verdict."""

    def __init__(self, *args, **kwargs):
        pass

    def ask(self, *args, **kwargs):
        return SimpleNamespace(error="no decider in this test")

    def close(self):
        pass


def model(answer: str | None):
    """A local model that gives `answer`, or is down (HTTP 503) when it is None."""
    def handler(request):
        if answer is None:
            return httpx.Response(503, text="model not loaded")
        return httpx.Response(200, content=fake_model.sse({"content": answer}))
    return httpx.MockTransport(handler)


SUPPORTED = json.dumps({"verdict": "supported", "quote": "the new lazy keyword defers loading a module",
                        "reason": "stated"})


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Writes the config and one research run, and returns a function that runs the CLI on it
    with the given mock model."""
    runs = tmp_path / "runs"
    run_dir = runs / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text(REPORT, encoding="utf-8")
    (run_dir / "sources.json").write_text(json.dumps(SOURCES), encoding="utf-8")
    config = tmp_path / "assistant.toml"
    config.write_text(CONFIG.format(runs=runs.as_posix()), encoding="utf-8")
    monkeypatch.setattr(cli, "Decider", NoDecider)

    def go(answer, *argv):
        transport = model(answer)
        monkeypatch.setattr(cli, "Gateway", lambda settings, log: Gateway(settings, log, transport=transport))
        return cli.main(["--config", str(config), *argv])
    go.run_dir = run_dir
    return go


def test_verify_passes_when_every_claim_is_supported(run, capsys):
    assert run(SUPPORTED, "verify", str(run.run_dir)) == 0
    out = capsys.readouterr().out
    assert "Citation check: pass" in out and CAVEAT in out


def test_verify_fails_when_the_model_is_down(run, capsys):
    """Every claim is unchecked, so nothing was shown; this used to exit 0."""
    assert run(None, "verify", str(run.run_dir)) == 1
    assert "1 unchecked" in capsys.readouterr().out


def test_verify_fails_on_a_partial_claim(run):
    partial = json.dumps({"verdict": "partial", "quote": "", "reason": "only part"})
    assert run(partial, "verify", str(run.run_dir)) == 1


def test_verify_fails_a_report_with_no_claims(run, capsys):
    (run.run_dir / "report.md").write_text("# Empty\n\n## Sources\n", encoding="utf-8")
    assert run(SUPPORTED, "verify", str(run.run_dir)) == 1
    assert "no claims to check" in capsys.readouterr().out


def test_verification_md_carries_the_caveat(run):
    run(SUPPORTED, "verify", str(run.run_dir))
    assert CAVEAT in (run.run_dir / "verification.md").read_text(encoding="utf-8")


def test_failures_names_each_reason():
    summary = {"claims": 4, "uncited": 1, "unsupported": 0, "partial": 2, "unchecked": 1, "dangling_citations": 0}
    assert failures(summary) == ["1 uncited", "2 partial", "1 unchecked"]
    assert failures({**summary, "uncited": 0, "partial": 0, "unchecked": 0}) == []
