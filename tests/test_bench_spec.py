import re
from dataclasses import replace
from pathlib import Path

import pytest

from assistant.bench_spec import (BenchSpecError, REFEREE_CAVEAT, candidate_key, check_referee_pin,
                                  load_bench)
from assistant.config import Tier
from assistant.manifest import sha256_file

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "evals" / "bench_v1.toml"


def _variant(tmp_path, old, new):
    text = SPEC.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "bench.toml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


def test_load_bench_v1():
    spec = load_bench(ROOT / "evals/bench_v1.toml")
    assert spec.version == 1 and spec.frozen is False
    assert spec.fit.min_free_ram_mb == 2048 and spec.fit.max_report_minutes == 60
    assert spec.pass_line.min_facts_right == 0.75 and spec.pass_line.max_errors_through == 0.05
    assert spec.referee.min_supported_recall == 0.90 and spec.referee.min_not_supported_recall == 0.85
    assert set(spec.questions) == {"dev", "test"}
    assert spec.questions["test"] == ["evals/bench_test_questions.toml"]
    assert spec.candidates["qwen-9b"].tier == "local-backup"
    assert spec.runs == 1 and spec.search == "searxng" and spec.tavily_fallback is False


def test_frozen_needs_referee_hash(tmp_path):
    path = _variant(tmp_path, "frozen = false", "frozen = true")
    with pytest.raises(BenchSpecError, match="sha256"):
        load_bench(path)


def test_unknown_split_rejected(tmp_path):
    path = _variant(tmp_path, "[questions]\n", '[questions]\ntrain = ["x.toml"]\n')
    with pytest.raises(BenchSpecError, match="train"):
        load_bench(path)


def test_missing_key_rejected(tmp_path):
    path = _variant(tmp_path, "runs = 1\n", "")
    with pytest.raises(BenchSpecError, match="runs"):
        load_bench(path)


def test_referee_pin_mismatch(tmp_path):
    gguf = tmp_path / "r.gguf"
    gguf.write_bytes(b"x")
    spec = load_bench(SPEC)
    env = {"REFEREE_GGUF": str(gguf)}
    check_referee_pin(spec, env)  # empty sha256: only the file must exist
    bad = replace(spec, referee=replace(spec.referee, sha256="0" * 64))
    with pytest.raises(BenchSpecError, match="hash"):
        check_referee_pin(bad, env)
    good = replace(spec, referee=replace(spec.referee, sha256=sha256_file(gguf)))
    check_referee_pin(good, env)


def test_referee_pin_unset_or_missing(tmp_path):
    spec = load_bench(SPEC)
    with pytest.raises(BenchSpecError, match="REFEREE_GGUF"):
        check_referee_pin(spec, {})
    with pytest.raises(BenchSpecError, match="missing|exist"):
        check_referee_pin(spec, {"REFEREE_GGUF": str(tmp_path / "nope.gguf")})


def test_candidate_key_changes_with_settings():
    a = Tier(name="local", kind="local", base_url="http://127.0.0.1:1234/v1", model="m", temperature=1.0)
    b = replace(a, temperature=0.5)
    assert candidate_key("a" * 64, "c" * 40, a) != candidate_key("a" * 64, "c" * 40, b)
    assert candidate_key("a" * 64, "c" * 40, a).startswith("aaaaaaaaaaaa-ccccccccc-")
    assert re.fullmatch(r"a{12}-c{9}-[0-9a-f]{12}", candidate_key("a" * 64, "c" * 40, a))


def test_caveat_text():
    assert REFEREE_CAVEAT.startswith("A referee pass means the referee accepted the report")
