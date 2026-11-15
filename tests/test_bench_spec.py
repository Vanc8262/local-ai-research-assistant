import re
from dataclasses import replace
from pathlib import Path

import pytest

import httpx

from assistant.bench_spec import (BenchSpecError, REFEREE_CAVEAT, candidate_key, check_referee_pin,
                                  check_served_model, load_bench, question_file_problems, same_model_file,
                                  served_model_path)
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


def test_frozen_needs_the_prompt_hash(tmp_path):
    text = SPEC.read_text(encoding="utf-8").replace("frozen = false", "frozen = true")
    text = text.replace('sha256 = ""', 'sha256 = "' + "a" * 64 + '"')
    path = tmp_path / "bench.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BenchSpecError, match="prompt_hash"):
        load_bench(path)
    path.write_text(text.replace('prompt_hash = ""', 'prompt_hash = "' + "b" * 64 + '"'), encoding="utf-8")
    assert load_bench(path).referee.prompt_hash == "b" * 64


def test_referee_table_defaults_and_hash_normalisation(tmp_path):
    spec = load_bench(SPEC)
    assert spec.referee.prompt_hash == "" and spec.referee.max_referee_failed == 0.02
    text = SPEC.read_text(encoding="utf-8")
    text = re.sub(r"^(prompt_hash|max_referee_failed) =.*\n", "", text, flags=re.M)
    assert "prompt_hash" not in text and "max_referee_failed" not in text
    text = text.replace('sha256 = ""', 'sha256 = " ' + "AB" * 32 + ' "')
    path = tmp_path / "bench.toml"
    path.write_text(text, encoding="utf-8")
    old = load_bench(path)   # both keys are optional; the pin is kept in lower case
    assert old.referee.prompt_hash == "" and old.referee.max_referee_failed == 0.02
    assert old.referee.sha256 == "ab" * 32


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
    upper = replace(spec, referee=replace(spec.referee, sha256=" " + sha256_file(gguf).upper() + " "))
    check_referee_pin(upper, env)   # Get-FileHash prints upper case
    check_referee_pin(good, env, hasher=lambda path: sha256_file(path).upper())


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


# --- the served model file (llama-swap's /upstream/<model>/props) -------------------------------

class Reply:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def test_served_model_path_reads_props():
    seen = []
    get = lambda url, **kw: seen.append(url) or Reply(200, {"model_path": "D:\\models\\r.gguf", "n_ctx": 1})
    assert served_model_path("http://127.0.0.1:1234/v1", "referee", get=get) == "D:\\models\\r.gguf"
    assert seen == ["http://127.0.0.1:1234/upstream/referee/props"]


@pytest.mark.parametrize("get", [
    lambda url, **kw: Reply(404, {}),
    lambda url, **kw: Reply(200, {"n_ctx": 1}),
    lambda url, **kw: Reply(200, ValueError("not json")),
    lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("refused")),
])
def test_served_model_path_fails_when_it_cannot_tell(get):
    with pytest.raises(BenchSpecError, match="served"):
        served_model_path("http://127.0.0.1:1234/v1", "referee", get=get)


def test_same_model_file(tmp_path):
    a = tmp_path / "a" / "Model.gguf"
    a.parent.mkdir()
    a.write_bytes(b"12345")
    assert same_model_file(str(a), str(a))
    assert same_model_file(str(tmp_path / "a" / "." / "Model.gguf"), str(a))
    link = tmp_path / "link.gguf"
    link.symlink_to(a)
    assert same_model_file(str(link), str(a))
    other = tmp_path / "b" / "model.GGUF"   # another copy: same name (any case) and size
    other.parent.mkdir()
    other.write_bytes(b"54321")
    assert same_model_file(str(other), str(a))
    other.write_bytes(b"123456")
    assert not same_model_file(str(other), str(a))
    different = tmp_path / "a" / "other.gguf"
    different.write_bytes(b"12345")
    assert not same_model_file(str(different), str(a))
    assert not same_model_file(str(tmp_path / "gone.gguf"), str(a))   # not a file here: can't be the same


def test_check_served_model_fails_closed_or_warns(tmp_path):
    tier = Tier(name="referee", kind="local", base_url="http://127.0.0.1:1234/v1", model="referee")
    want = tmp_path / "r.gguf"
    want.write_bytes(b"r")
    wrong = tmp_path / "w.gguf"
    wrong.write_bytes(b"w")
    assert check_served_model(tier, str(want), "referee", strict=True, fetch=lambda b, m: str(want)) is None
    with pytest.raises(BenchSpecError, match="w.gguf") as exc:
        check_served_model(tier, str(want), "referee", strict=True, fetch=lambda b, m: str(wrong))
    assert "r.gguf" in str(exc.value)

    def down(base, model):
        raise BenchSpecError("could not read the served model file: refused")
    with pytest.raises(BenchSpecError, match="refused") as exc:
        check_served_model(tier, str(want), "referee", strict=True, fetch=down)
    assert "override" not in str(exc.value) and "--" not in str(exc.value)   # no way around it is offered
    warning = check_served_model(tier, str(want), "candidate", strict=False, fetch=down)
    assert "refused" in warning and "not checked" in warning
    assert "w.gguf" in check_served_model(tier, str(want), "candidate", strict=False, fetch=lambda b, m: str(wrong))


def test_question_file_problems(tmp_path):
    good = tmp_path / "evals" / "a.toml"
    good.parent.mkdir()
    good.write_text("x", encoding="utf-8")
    bench = {"question_files": {"evals/a.toml": sha256_file(good)}}
    assert question_file_problems(bench, tmp_path) == []
    bench["question_files"]["evals/a.toml"] = sha256_file(good).upper()
    assert question_file_problems(bench, tmp_path) == []
    good.write_text("y", encoding="utf-8")
    (problem,) = question_file_problems(bench, tmp_path)
    assert "evals/a.toml" in problem and "changed" in problem
    assert "gone" in question_file_problems({"question_files": {"evals/b.toml": "0" * 64}}, tmp_path)[0]
    assert question_file_problems({"question_files": {str(good): sha256_file(good)}}, tmp_path / "x") == []
    assert "question_files" in question_file_problems({}, tmp_path)[0]
