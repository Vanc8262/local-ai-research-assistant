import hashlib
import json
import pickle
import stat
import struct

import httpx
import pytest

from assistant import vault
from assistant.__main__ import main
from assistant.runlog import RunLog, read_run

COMMIT = "a" * 40
SAFE_TEMPLATE = "{% for m in messages %}<|{{ m['role'] }}|>{{ m['content'] }}{% endfor %}"
EVIL_TEMPLATE = "{{ ''.__class__.__mro__[1].__subclasses__() }}"


def gguf(template: str = SAFE_TEMPLATE) -> bytes:
    def string(text: str) -> bytes:
        raw = text.encode()
        return struct.pack("<Q", len(raw)) + raw
    kvs = [
        string("general.architecture") + struct.pack("<I", 8) + string("gemma4"),
        # an array of strings, like tokenizer.ggml.tokens, which the reader must skip
        string("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, 3) + string("a") + string("b") + string("c"),
        string("general.file_type") + struct.pack("<I", 4) + struct.pack("<I", 15),
        string("tokenizer.chat_template") + struct.pack("<I", 8) + string(template),
    ]
    return b"GGUF" + struct.pack("<IQQ", 3, 0, len(kvs)) + b"".join(kvs) + b"\0" * 64


def safetensors() -> bytes:
    header = json.dumps({"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}).encode()
    return struct.pack("<Q", len(header)) + header + b"\0" * 8


def hub(files: dict[str, bytes], *, seen: list | None = None, tamper: str = "", license_="apache-2.0"):
    """A fake Hugging Face: the revision API plus resolve URLs for `files`."""
    seen = [] if seen is None else seen

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        if request.url.path.startswith("/api/models/"):
            siblings = []
            for name, data in files.items():
                entry = {"rfilename": name, "size": len(data)}
                if name.endswith((".gguf", ".safetensors", ".bin")):
                    entry["lfs"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                siblings.append(entry)
            return httpx.Response(200, json={"sha": COMMIT, "cardData": {"license": license_}, "siblings": siblings})
        name = request.url.path.split(f"/resolve/{COMMIT}/", 1)[1]
        data = files[name]
        if name == tamper:
            data = data[:-1] + b"X"
        return httpx.Response(200, content=data)
    return httpx.MockTransport(handler)


def test_add_pins_commit_checks_hashes_and_refuses_pickle(tmp_path):
    files = {"model-Q4_K_M.gguf": gguf(), "model-Q8_0.gguf": gguf(), "LICENSE": b"Apache 2.0",
             "pytorch_model.bin": pickle.dumps({"w": 1}), "modeling_evil.py": b"import os"}
    seen = []
    log = RunLog(tmp_path / "runs")
    result = vault.add(tmp_path / "v", "google/gemma-4-E4B-it-GGUF", log, include=["*Q4_K_M.gguf"],
                       transport=hub(files, seen=seen))

    outcomes = {f.path: f.outcome for f in result.files}
    assert outcomes == {"model-Q4_K_M.gguf": "downloaded", "model-Q8_0.gguf": "skipped", "LICENSE": "downloaded",
                        "pytorch_model.bin": "not_allowed", "modeling_evil.py": "not_allowed"}
    assert result.ok and result.revision == COMMIT and result.license == "apache-2.0"
    assert not any(p.endswith((".bin", ".py", "Q8_0.gguf")) for p in seen)   # never requested
    assert all(f"/resolve/{COMMIT}/" in p for p in seen if "/resolve/" in p)    # pinned, never main

    folder = tmp_path / "v" / "google" / "gemma-4-E4B-it-GGUF"
    assert not stat.S_IMODE((folder / "model-Q4_K_M.gguf").stat().st_mode) & stat.S_IWUSR
    entry = vault.read_manifest(tmp_path / "v")["models"]["google/gemma-4-E4B-it-GGUF"]
    assert entry["revision"] == COMMIT and set(entry["files"]) == {"model-Q4_K_M.gguf", "LICENSE"}
    assert entry["files"]["model-Q4_K_M.gguf"]["sha256"] == hashlib.sha256(files["model-Q4_K_M.gguf"]).hexdigest()
    assert {s["file"] for s in read_run(log.path) if s["event"] == "step"} == {"model-Q4_K_M.gguf", "LICENSE"}


def test_hash_mismatch_is_deleted_and_not_recorded(tmp_path):
    files = {"model.gguf": gguf()}
    result = vault.add(tmp_path / "v", "a/b", RunLog(tmp_path / "runs"), transport=hub(files, tamper="model.gguf"))
    assert [f.outcome for f in result.files] == ["hash_mismatch"] and not result.ok
    assert not list((tmp_path / "v").rglob("*.gguf*")) and vault.read_manifest(tmp_path / "v")["models"] == {}


def test_evil_chat_template_is_refused(tmp_path):
    files = {"model.gguf": gguf(EVIL_TEMPLATE)}
    result = vault.add(tmp_path / "v", "a/b", RunLog(tmp_path / "runs"), transport=hub(files))
    (check,) = result.files
    assert check.outcome == "refused" and "__class__" in check.detail and not result.ok
    assert not list((tmp_path / "v").rglob("*.gguf*"))


def test_pickle_renamed_to_safetensors_is_refused(tmp_path):
    files = {"model.safetensors": pickle.dumps({"w": 1}) + b"\0" * 32}
    result = vault.add(tmp_path / "v", "a/b", RunLog(tmp_path / "runs"), transport=hub(files))
    assert result.files[0].outcome == "refused" and "safetensors" in result.files[0].detail


def test_repo_with_only_pickle_weights_is_refused(tmp_path):
    with pytest.raises(vault.VaultError, match="no safetensors or GGUF"):
        vault.add(tmp_path / "v", "a/b", RunLog(tmp_path / "runs"),
                  transport=hub({"pytorch_model.bin": pickle.dumps(1), "config.json": b"{}"}))


def test_verify_catches_one_changed_byte_and_extra_files(tmp_path):
    root = tmp_path / "v"
    vault.add(root, "Qwen/Qwen3.5-9B", RunLog(tmp_path / "runs"),
              transport=hub({"model.safetensors": safetensors(), "config.json": b"{}"}))
    assert {c.outcome for c in vault.verify(root)["Qwen/Qwen3.5-9B"]} == {"ok"}

    weights = root / "Qwen" / "Qwen3.5-9B" / "model.safetensors"
    weights.chmod(stat.S_IRUSR | stat.S_IWUSR)
    data = bytearray(weights.read_bytes())
    data[-1] ^= 1
    weights.write_bytes(bytes(data))
    (weights.parent / "notes.txt").write_text("hi")
    checks = {c.path: c.outcome for c in vault.verify(root)["Qwen/Qwen3.5-9B"]}
    assert checks == {"model.safetensors": "changed", "config.json": "ok", "notes.txt": "unrecorded"}
    with pytest.raises(vault.VaultError, match="not in the vault"):
        vault.verify(root, names=["nope/nope"])


def test_import_lm_studio_folder(tmp_path):
    source = tmp_path / "lmstudio" / "google" / "gemma-4-E4B-it-qat-q4_0-gguf"
    source.mkdir(parents=True)
    (source / "gemma-4-e4b-it-qat-q4_0.gguf").write_bytes(gguf())
    (source / "stray.pt").write_bytes(pickle.dumps(1))
    root = tmp_path / "v"
    result = vault.import_local(root, source, "google/gemma-4-E4B-it-qat-q4_0-gguf", RunLog(tmp_path / "runs"),
                                license_="apache-2.0")
    assert {f.path: f.outcome for f in result.files} == {"gemma-4-e4b-it-qat-q4_0.gguf": "imported",
                                                        "stray.pt": "not_allowed"}
    assert (source / "gemma-4-e4b-it-qat-q4_0.gguf").exists()   # copied, not moved, by default
    entry = vault.read_manifest(root)["models"]["google/gemma-4-E4B-it-qat-q4_0-gguf"]
    assert entry["source"] == "import" and entry["license"] == "apache-2.0"
    assert {c.outcome for c in vault.verify(root)["google/gemma-4-E4B-it-qat-q4_0-gguf"]} == {"ok"}


def test_gguf_metadata_and_flags():
    assert vault.template_flags(SAFE_TEMPLATE) == []
    assert "__class__" in vault.template_flags(EVIL_TEMPLATE)
    assert vault.template_flags("{% set x = cycler.__init__.__globals__.os.popen('id') %}")


@pytest.mark.parametrize("name, kind", [
    ("model-00001-of-00004.safetensors", "weight"), ("x.gguf", "weight"), ("tokenizer.json", "side"),
    ("tokenizer.model", "side"), ("LICENSE", "side"), ("chat_template.jinja", "side"),
    ("pytorch_model.bin", "refused"), ("x.pt", "refused"), ("modeling.py", "refused"), ("weird.dat", "refused"),
])
def test_classify(name, kind):
    assert vault.classify(name) == kind


def test_bad_model_names_are_refused(tmp_path):
    for name in ("nopublisher", "a/b/c", "../x"):
        with pytest.raises(vault.VaultError):
            vault.model_dir(tmp_path, name)


def test_cli_scan_list_and_verify(tmp_path, capsys):
    root = tmp_path / "v"
    config = tmp_path / "assistant.toml"
    config.write_text(f'''
[assistant]
runs_dir = "{(tmp_path / 'runs').as_posix()}"
vault_dir = "{root.as_posix()}"
[tiers.local]
kind = "local"
base_url = "http://local/v1"
model = "m"
''', encoding="utf-8")
    good, bad = tmp_path / "good.gguf", tmp_path / "bad.gguf"
    good.write_bytes(gguf())
    bad.write_bytes(gguf(EVIL_TEMPLATE))
    assert main(["--config", str(config), "vault", "scan", str(good)]) == 0
    assert main(["--config", str(config), "vault", "scan", str(bad)]) == 1
    assert "REFUSED" in capsys.readouterr().out

    vault.add(root, "a/b", RunLog(tmp_path / "runs"), transport=hub({"m.gguf": gguf()}))
    assert main(["--config", str(config), "vault", "list"]) == 0
    assert "a/b" in capsys.readouterr().out
    assert main(["--config", str(config), "vault", "verify"]) == 0
    weights = root / "a" / "b" / "m.gguf"
    weights.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert main(["--config", str(config), "vault", "verify", "a/b"]) == 1
    assert "writable" in capsys.readouterr().out


def test_unreachable_hub_is_a_clean_error(tmp_path):
    def fail(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(vault.VaultError, match="cannot reach Hugging Face"):
        vault.add(tmp_path / "v", "a/b", RunLog(tmp_path / "runs"), transport=httpx.MockTransport(fail))


def write_picks(tmp_path, text):
    path = tmp_path / "vault.toml"
    path.write_text(text, encoding="utf-8")
    return path


PICKS = f'''
[[model]]
name = "lmstudio-community/Qwen3.5-9B-GGUF"
revision = "{COMMIT}"
include = ["*Q4_K_M.gguf"]
why = "backup tier"
'''


def test_sync_stores_only_picks_and_never_deletes(tmp_path):
    root = tmp_path / "v"
    files = {"Qwen3.5-9B-Q4_K_M.gguf": gguf(), "Qwen3.5-9B-Q8_0.gguf": gguf()}
    # a model stored earlier that is no longer picked
    vault.add(root, "old/model", RunLog(tmp_path / "runs"), transport=hub({"m.gguf": gguf()}))
    picks = vault.load_picks(write_picks(tmp_path, PICKS))
    assert vault.plan(root, picks) == [("lmstudio-community/Qwen3.5-9B-GGUF", "to add"), ("old/model", "not picked")]

    seen = []
    (result,) = vault.sync(root, picks, RunLog(tmp_path / "runs"), transport=hub(files, seen=seen))
    assert result.ok and {f.path: f.outcome for f in result.files}["Qwen3.5-9B-Q8_0.gguf"] == "skipped"
    stored = vault.read_manifest(root)["models"]
    assert set(stored) == {"lmstudio-community/Qwen3.5-9B-GGUF", "old/model"}   # nothing deleted
    assert stored["lmstudio-community/Qwen3.5-9B-GGUF"]["why"] == "backup tier"
    assert vault.plan(root, picks)[0] == ("lmstudio-community/Qwen3.5-9B-GGUF", "stored")
    assert vault.sync(root, picks, RunLog(tmp_path / "runs"), transport=hub(files)) == []   # already stored

    with pytest.raises(vault.VaultError, match="not picked"):
        vault.sync(root, picks, RunLog(tmp_path / "runs"), only=["google/other"], transport=hub(files))


def test_newer_pinned_revision_is_planned_as_update(tmp_path):
    root = tmp_path / "v"
    picks = vault.load_picks(write_picks(tmp_path, PICKS))
    vault.sync(root, picks, RunLog(tmp_path / "runs"), transport=hub({"x-Q4_K_M.gguf": gguf()}))
    newer = vault.load_picks(write_picks(tmp_path, PICKS.replace(COMMIT, "b" * 40)))
    assert vault.plan(root, newer) == [("lmstudio-community/Qwen3.5-9B-GGUF", "to update")]


@pytest.mark.parametrize("change, message", [
    ('revision = "main"', "40-character commit"),
    ('why = ""', "say why"),
    ('source = "torrent"', "source must be"),
])
def test_bad_picks_are_rejected(tmp_path, change, message):
    key = change.split(" = ")[0]
    lines = [line for line in PICKS.strip().splitlines() if not line.startswith(key)]
    with pytest.raises(vault.VaultError, match=message):
        vault.load_picks(write_picks(tmp_path, "\n".join(lines + [change])))


def test_import_pick_needs_path_and_license(tmp_path):
    with pytest.raises(vault.VaultError, match="path and license"):
        vault.load_picks(write_picks(tmp_path, '[[model]]\nname = "a/b"\nsource = "import"\nwhy = "x"\n'))
    with pytest.raises(vault.VaultError, match="picked twice"):
        vault.load_picks(write_picks(tmp_path, PICKS + PICKS))


def test_shipped_vault_toml_has_no_picks_yet():
    from pathlib import Path
    assert vault.load_picks(Path(__file__).resolve().parent.parent / "vault.toml") == []
