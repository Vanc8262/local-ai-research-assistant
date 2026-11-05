"""The model vault: one folder of verified, licensed open-weight models that this PC owns.

Layout: <vault_dir>/<publisher>/<repo>/<files>, the layout LM Studio reads, so LM
Studio's models directory can point at the vault and each file is stored once.
<vault_dir>/manifest.json records, per model, where it came from (repo and the
exact commit, or a local import), its license, and every file's size and SHA-256.

Only the models picked in vault.toml go in. Picking is a decision made after
benchmarking, so the vault never adds a model on its own: `vault plan` shows what
the picks would change and `vault sync` stores exactly those picks, nothing else.

Rules the vault enforces:
- Weights are safetensors or GGUF only. Pickle-based formats (.bin, .pt, .pth,
  .ckpt, .pkl) can run code when loaded, and so can a repo's .py files; those are
  refused, never downloaded.
- A weight file's first bytes must match its format, so a pickle renamed to
  .safetensors is refused too.
- Downloads are pinned to a commit and every file is checked against the SHA-256
  Hugging Face publishes for it. A mismatch is deleted, not kept.
- A GGUF file's chat template is a Jinja program; one that reaches for Python
  internals (CVE-2024-34359) is flagged and the model is not recorded.
- Files are made read-only once verified, and `verify` re-hashes them later.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import httpx

from .runlog import RunLog

HF = "https://huggingface.co"
USER_AGENT = "local-ai-research-assistant/0.1 (model vault)"
WEIGHT_SUFFIXES = (".safetensors", ".gguf")
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle", ".npy", ".npz", ".joblib", ".h5", ".msgpack")
CODE_SUFFIXES = (".py", ".pyc", ".so", ".dll", ".exe", ".sh", ".bat", ".ps1")
SIDE_SUFFIXES = (".json", ".txt", ".md", ".jinja", ".model", ".tiktoken", ".yaml", ".yml", ".png", ".jpg")
SIDE_NAMES = ("LICENSE", "NOTICE", "USE_POLICY", "COPYING", ".gitattributes")
# Jinja reaching into Python objects; normal chat templates never need any of these.
TEMPLATE_RED_FLAGS = re.compile(
    r"__class__|__globals__|__builtins__|__import__|__subclasses__|__mro__|__base__|__init__|"
    r"\bos\.|\bsubprocess\b|\bpopen\b|\beval\s*\(|\bexec\s*\(|\bimport\s+\w|\bopen\s*\(")


class VaultError(ValueError):
    """Something the vault refuses to store or cannot do."""


@dataclass
class FileCheck:
    path: str                       # relative to the model's folder
    outcome: str                    # ok, missing, changed, writable, refused, ...
    detail: str = ""


@dataclass
class AddResult:
    name: str
    revision: str = ""
    license: str = ""
    files: list[FileCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        kept = ("downloaded", "present", "imported")
        passed = kept + ("skipped", "not_allowed")
        return all(f.outcome in passed for f in self.files) and any(f.outcome in kept for f in self.files)


# ---------- file kinds and checks ----------

def classify(name: str) -> str:
    """"weight", "side" (config, tokenizer, license...), or "refused"."""
    path = PurePosixPath(name)
    suffix = path.suffix.lower()
    if suffix in WEIGHT_SUFFIXES:
        return "weight"
    if suffix in PICKLE_SUFFIXES or suffix in CODE_SUFFIXES:
        return "refused"
    if suffix in SIDE_SUFFIXES or path.name.split(".")[0].upper() in SIDE_NAMES:
        return "side"
    return "refused"   # unknown kinds are not stored: the vault is a whitelist


def check_format(path: Path, suffix: str = "") -> str:
    """Empty if the file's bytes match its suffix (or `suffix`, for a .part download), else why not."""
    suffix = (suffix or path.suffix).lower()
    with path.open("rb") as handle:
        head = handle.read(8)
        if suffix == ".gguf":
            return "" if head[:4] == b"GGUF" else "not a GGUF file (bad magic)"
        if suffix == ".safetensors":
            if len(head) < 8:
                return "not a safetensors file (too short)"
            (size,) = struct.unpack("<Q", head)
            if size <= 1 or size > 100 * 2**20 or size > path.stat().st_size - 8:
                return "not a safetensors file (bad header length)"
            try:
                header = json.loads(handle.read(size))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return "not a safetensors file (header is not JSON)"
            return "" if isinstance(header, dict) else "not a safetensors file (header is not an object)"
    return ""


def gguf_metadata(path: Path, wanted: tuple[str, ...] = ("tokenizer.chat_template", "general.name",
                                                         "general.license", "general.architecture")) -> dict:
    """The string values of `wanted` keys from a GGUF header (v2/v3). Arrays are skipped."""
    found: dict = {}
    with Path(path).open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise VaultError(f"{path.name}: not a GGUF file")
        (version,) = struct.unpack("<I", handle.read(4))
        if version < 2:
            raise VaultError(f"{path.name}: GGUF v{version} is too old to read")
        _, kv_count = struct.unpack("<QQ", handle.read(16))
        for _ in range(kv_count):
            key = _gguf_string(handle)
            (kind,) = struct.unpack("<I", handle.read(4))
            value = _gguf_value(handle, kind, keep=key in wanted)
            if key in wanted:
                found[key] = value
    return found


_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def _gguf_string(handle, keep: bool = True):
    (size,) = struct.unpack("<Q", handle.read(8))
    if size > 64 * 2**20:
        raise VaultError("GGUF string longer than 64 MB; file is corrupt or hostile")
    if not keep:
        handle.seek(size, os.SEEK_CUR)
        return None
    return handle.read(size).decode("utf-8", errors="replace")


def _gguf_value(handle, kind: int, keep: bool):
    if kind in _FIXED:
        raw = handle.read(_FIXED[kind])
        return raw if keep else None
    if kind == 8:
        return _gguf_string(handle, keep)
    if kind == 9:
        (inner,) = struct.unpack("<I", handle.read(4))
        (count,) = struct.unpack("<Q", handle.read(8))
        if inner in _FIXED:
            handle.seek(_FIXED[inner] * count, os.SEEK_CUR)
        else:
            for _ in range(count):
                _gguf_value(handle, inner, keep=False)
        return None
    raise VaultError(f"unknown GGUF value type {kind}")


def template_flags(template: str | None) -> list[str]:
    """The suspicious constructs in a chat template, if any."""
    return sorted({m.group(0).strip() for m in TEMPLATE_RED_FLAGS.finditer(template or "")})


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 2**20), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_file(path: Path, suffix: str = "") -> str:
    """Empty if a file passes the format and template checks, else why not."""
    suffix = (suffix or path.suffix).lower()
    problem = check_format(path, suffix)
    if problem or suffix != ".gguf":
        return problem
    flags = template_flags(gguf_metadata(path).get("tokenizer.chat_template"))
    return f"chat template uses {', '.join(flags)}" if flags else ""


# ---------- manifest ----------

def read_manifest(vault_dir: Path) -> dict:
    path = Path(vault_dir) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"models": {}}


def write_manifest(vault_dir: Path, manifest: dict) -> None:
    path = Path(vault_dir) / "manifest.json"
    temp = path.with_suffix(".json.part")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def model_dir(vault_dir: Path, name: str) -> Path:
    publisher, _, repo = name.partition("/")
    if not publisher or not repo or "/" in repo or ".." in (publisher, repo):
        raise VaultError(f"model name must be <publisher>/<repo>, not {name!r}")
    return Path(vault_dir) / publisher / repo


def _make_read_only(path: Path) -> None:
    path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def _is_writable(path: Path) -> bool:
    return bool(path.stat().st_mode & stat.S_IWUSR)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- add from Hugging Face ----------

def add(vault_dir: str | Path, repo: str, log: RunLog, *, revision: str = "main",
        include: list[str] | None = None, transport=None) -> AddResult:
    """Download a Hugging Face repo, pinned to one commit, into the vault.

    `include` globs pick files (e.g. "*Q4_K_M.gguf"); side files such as LICENSE
    and config.json come along whenever weights are picked. Pickle, code and unknown
    files are listed as "not_allowed" and never requested.
    """
    vault_dir = Path(vault_dir)
    target = model_dir(vault_dir, repo)
    headers = {"User-Agent": USER_AGENT}
    if os.environ.get("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"   # gated repos only
    client = httpx.Client(headers=headers, follow_redirects=True, transport=transport,
                          timeout=httpx.Timeout(300.0, connect=20.0))
    try:
        info = _repo_info(client, repo, revision)
        commit = info["sha"]
        if COMMIT_RE.match(revision) and commit != revision:
            raise VaultError(f"{repo}: asked for commit {revision[:12]}, Hugging Face returned {commit[:12]}")
        license_ = _license_of(info)
        result = AddResult(name=repo, revision=commit, license=license_)
        files = []
        for sibling in info.get("siblings", []):
            name = sibling["rfilename"]
            kind = classify(name)
            if kind == "refused":
                result.files.append(FileCheck(name, "not_allowed", "pickle, code or unknown file type; not downloaded"))
                continue
            if include and kind == "weight" and not any(fnmatch.fnmatch(name, g) for g in include):
                result.files.append(FileCheck(name, "skipped"))
                continue
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                result.files.append(FileCheck(name, "refused", "path escapes the model folder"))
                continue
            files.append(sibling)
        if not any(classify(s["rfilename"]) == "weight" for s in files):
            raise VaultError(f"{repo}@{commit[:12]}: no safetensors or GGUF weights to download"
                             + (" (check include)" if include else ""))
        manifest = read_manifest(vault_dir)
        entry = {"source": "huggingface", "repo": repo, "revision": commit, "license": license_,
                 "added_at": _now(), "files": {}}
        for sibling in files:
            check, record = _fetch_file(client, repo, commit, sibling, target)
            result.files.append(check)
            log.step("vault", action="download", model=repo, revision=commit, file=check.path,
                     outcome=check.outcome, detail=check.detail, bytes=record.get("bytes"),
                     sha256=record.get("sha256"))
            if record:
                entry["files"][check.path] = record
        if result.ok:
            manifest["models"][repo] = entry
            write_manifest(vault_dir, manifest)
        return result
    finally:
        client.close()


def _repo_info(client: httpx.Client, repo: str, revision: str) -> dict:
    try:
        resp = client.get(f"{HF}/api/models/{repo}/revision/{revision}", params={"blobs": "true"})
    except httpx.HTTPError as exc:
        raise VaultError(f"cannot reach Hugging Face ({type(exc).__name__}: {exc})") from None
    if resp.status_code in (401, 403):
        raise VaultError(f"{repo}: access denied (gated? accept its terms on Hugging Face and set HF_TOKEN)")
    if resp.status_code != 200:
        raise VaultError(f"{repo}@{revision}: Hugging Face answered HTTP {resp.status_code}")
    return resp.json()


def _license_of(info: dict) -> str:
    card = info.get("cardData") or {}
    if card.get("license"):
        return str(card["license"])
    tags = [t for t in info.get("tags", []) if t.startswith("license:")]
    return tags[0].split(":", 1)[1] if tags else "unknown"


def _fetch_file(client, repo: str, commit: str, sibling: dict, target: Path) -> tuple[FileCheck, dict]:
    name = sibling["rfilename"]
    expected = (sibling.get("lfs") or {}).get("sha256", "")
    size = sibling.get("size") or (sibling.get("lfs") or {}).get("size")
    path = target / name
    if path.exists() and expected and sha256_of(path) == expected:
        return FileCheck(name, "present"), {"bytes": path.stat().st_size, "sha256": expected}
    if classify(name) == "weight" and not expected:
        return FileCheck(name, "refused", "Hugging Face lists no SHA-256 for this weight file"), {}
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with client.stream("GET", f"{HF}/{repo}/resolve/{commit}/{name}") as resp:
            if resp.status_code != 200:
                return FileCheck(name, f"http_{resp.status_code}"), {}
            with part.open("wb") as handle:
                for block in resp.iter_bytes(8 * 2**20):
                    handle.write(block)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        return FileCheck(name, "error", type(exc).__name__), {}
    actual = sha256_of(part)
    if expected and actual != expected:
        part.unlink()
        return FileCheck(name, "hash_mismatch", f"expected {expected[:12]}, got {actual[:12]}"), {}
    if size and part.stat().st_size != size:
        part.unlink()
        return FileCheck(name, "size_mismatch", f"expected {size} bytes"), {}
    problem = scan_file(part, path.suffix)
    if problem:
        part.unlink()
        return FileCheck(name, "refused", problem), {}
    if path.exists():
        path.chmod(stat.S_IWUSR | stat.S_IRUSR)
    part.replace(path)
    _make_read_only(path)
    return FileCheck(name, "downloaded"), {"bytes": path.stat().st_size, "sha256": actual}


# ---------- import local files ----------

def import_local(vault_dir: str | Path, source: str | Path, name: str, log: RunLog, *,
                 license_: str = "unknown", origin: str = "", move: bool = False) -> AddResult:
    """Copy (or move) a model folder already on this PC, such as an LM Studio download, into the vault."""
    vault_dir, source = Path(vault_dir), Path(source)
    if not source.is_dir():
        raise VaultError(f"{source} is not a folder")
    target = model_dir(vault_dir, name)
    result = AddResult(name=name, license=license_)
    entry = {"source": "import", "origin": origin or str(source), "license": license_,
             "added_at": _now(), "files": {}}
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = path.relative_to(source).as_posix()
        if classify(rel) == "refused":
            result.files.append(FileCheck(rel, "not_allowed", "pickle, code or unknown file type; left where it is"))
            continue
        problem = scan_file(path)
        if problem:
            result.files.append(FileCheck(rel, "refused", problem))
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.chmod(stat.S_IWUSR | stat.S_IRUSR)
        (shutil.move if move else shutil.copy2)(path, dest)
        _make_read_only(dest)
        digest = sha256_of(dest)
        entry["files"][rel] = {"bytes": dest.stat().st_size, "sha256": digest}
        result.files.append(FileCheck(rel, "imported"))
        log.step("vault", action="import", model=name, file=rel, outcome="imported",
                 bytes=dest.stat().st_size, sha256=digest)
    if not any(classify(f.path) == "weight" and f.outcome == "imported" for f in result.files):
        raise VaultError(f"{source}: no safetensors or GGUF weights to import")
    if result.ok:
        manifest = read_manifest(vault_dir)
        manifest["models"][name] = entry
        write_manifest(vault_dir, manifest)
    return result


# ---------- verify ----------

def verify(vault_dir: str | Path, names: list[str] | None = None) -> dict[str, list[FileCheck]]:
    """Re-hash every recorded file. Anything other than "ok" needs attention."""
    vault_dir = Path(vault_dir)
    manifest = read_manifest(vault_dir)["models"]
    unknown = set(names or ()) - set(manifest)
    if unknown:
        raise VaultError(f"not in the vault: {', '.join(sorted(unknown))}")
    report: dict[str, list[FileCheck]] = {}
    for name, entry in sorted(manifest.items()):
        if names and name not in names:
            continue
        folder = model_dir(vault_dir, name)
        checks = []
        for rel, record in sorted(entry["files"].items()):
            path = folder / rel
            if not path.exists():
                checks.append(FileCheck(rel, "missing"))
            elif sha256_of(path) != record["sha256"]:
                checks.append(FileCheck(rel, "changed", "SHA-256 no longer matches the manifest"))
            elif _is_writable(path):
                checks.append(FileCheck(rel, "writable", "intact, but not read-only"))
            else:
                checks.append(FileCheck(rel, "ok"))
        recorded = set(entry["files"])
        for path in sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []:
            rel = path.relative_to(folder).as_posix()
            if rel not in recorded:
                checks.append(FileCheck(rel, "unrecorded", "file in the folder but not in the manifest"))
        report[name] = checks
    return report


# ---------- picks: the models chosen for the vault ----------

SOURCES = ("huggingface", "import")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class Pick:
    name: str                       # <publisher>/<repo>, also the Hugging Face repo for source = huggingface
    why: str                        # the decision: which bench run or reason chose it
    source: str = "huggingface"
    revision: str = ""              # huggingface: the full 40-character commit, so the pick is exact
    include: list[str] = field(default_factory=list)   # huggingface: weight files to keep, e.g. "*Q4_K_M.gguf"
    path: str = ""                  # import: the folder on this PC, e.g. an LM Studio model folder
    license: str = ""               # import: the base model's license

    def __post_init__(self) -> None:
        model_dir(Path("."), self.name)
        if self.source not in SOURCES:
            raise VaultError(f"{self.name}: source must be one of {SOURCES}, not {self.source!r}")
        if not self.why.strip():
            raise VaultError(f"{self.name}: say why it was picked (why = \"...\")")
        if self.source == "huggingface" and not COMMIT_RE.match(self.revision):
            raise VaultError(f"{self.name}: revision must be a full 40-character commit hash, not {self.revision!r}")
        if self.source == "import" and not (self.path and self.license):
            raise VaultError(f"{self.name}: an import needs path and license")


def load_picks(path: str | Path) -> list[Pick]:
    path = Path(path)
    if not path.exists():
        raise VaultError(f"{path} not found; the vault only stores models picked there")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    picks = [Pick(**entry) for entry in raw.get("model", [])]
    names = [p.name for p in picks]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise VaultError(f"picked twice: {', '.join(duplicates)}")
    return picks


def plan(vault_dir: str | Path, picks: list[Pick]) -> list[tuple[str, str]]:
    """(name, state) for every pick and every stored model.

    States: "stored" (picked and in the vault at that revision), "to add",
    "to update" (stored at another revision), "not picked" (in the vault, not in
    vault.toml; the vault never deletes it, you decide).
    """
    stored = read_manifest(vault_dir)["models"]
    rows = []
    for pick in picks:
        entry = stored.get(pick.name)
        if not entry:
            rows.append((pick.name, "to add"))
        elif pick.source == "huggingface" and entry.get("revision") != pick.revision:
            rows.append((pick.name, "to update"))
        else:
            rows.append((pick.name, "stored"))
    picked = {p.name for p in picks}
    rows += [(name, "not picked") for name in sorted(stored) if name not in picked]
    return rows


def sync(vault_dir: str | Path, picks: list[Pick], log: RunLog, *, only: list[str] | None = None,
         transport=None) -> list[AddResult]:
    """Store each pick that is not in the vault yet. Never removes anything."""
    unknown = set(only or ()) - {p.name for p in picks}
    if unknown:
        raise VaultError(f"not picked in vault.toml: {', '.join(sorted(unknown))}")
    states = dict(plan(vault_dir, picks))
    results = []
    for pick in picks:
        if (only and pick.name not in only) or states[pick.name] == "stored":
            continue
        if pick.source == "huggingface":
            result = add(vault_dir, pick.name, log, revision=pick.revision, include=pick.include or None,
                         transport=transport)
        else:
            result = import_local(vault_dir, pick.path, pick.name, log, license_=pick.license)
        if result.ok:
            manifest = read_manifest(vault_dir)
            manifest["models"][pick.name]["why"] = pick.why
            write_manifest(vault_dir, manifest)
        results.append(result)
    return results
