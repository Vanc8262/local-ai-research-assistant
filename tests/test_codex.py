import hashlib
from pathlib import Path

import httpx
import pytest

from assistant.__main__ import main
from assistant.codex import CatalogError, Item, fetch, load_catalog, read_manifest
from assistant.runlog import RunLog, read_run

PDF = b"%PDF-1.7\n" + b"x" * 2000

CATALOG = '''
[[item]]
id = "react"
topic = "llm-agents"
title = "ReAct"
authors = "Yao et al."
year = 2023
url = "https://arxiv.org/pdf/2210.03629"
licence = "arXiv non-exclusive"

[[item]]
id = "d2l"
topic = "ml"
title = "Dive into Deep Learning"
url = "https://d2l.ai/"
kind = "html"

[[item]]
id = "login-wall"
topic = "cloud"
title = "A PDF behind a login page"
url = "https://example.org/whitepaper.pdf"
'''


def server(seen: list):
    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "arxiv.org":
            return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        return httpx.Response(200, text="<html>Please sign in</html>")
    return httpx.MockTransport(handler)


def write_catalog(tmp_path, text=CATALOG):
    path = tmp_path / "catalog.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_fetch_downloads_records_and_rejects_non_pdf(tmp_path):
    codex = tmp_path / "codex"
    log = RunLog(tmp_path / "runs")
    seen = []
    outcomes = fetch(load_catalog(write_catalog(tmp_path)), codex, log, delay_s=0, transport=server(seen))

    assert [(item.id, outcome) for item, outcome in outcomes] == [
        ("react", "downloaded"), ("d2l", "skipped_html"), ("login-wall", "not_pdf")]
    assert (codex / "llm-agents" / "react.pdf").read_bytes() == PDF
    assert not list(codex.rglob("*.part")) and not (codex / "cloud" / "login-wall.pdf").exists()
    (row,) = read_manifest(codex).values()
    assert row["id"] == "react" and row["file"] == "llm-agents/react.pdf"
    assert row["sha256"] == hashlib.sha256(PDF).hexdigest() and row["licence"] == "arXiv non-exclusive"
    assert [(s["id"], s["outcome"]) for s in read_run(log.path)] == [
        ("react", "downloaded"), ("login-wall", "not_pdf")]
    assert len(seen) == 2   # the html item is never requested


def test_rerun_skips_what_is_already_there(tmp_path):
    codex, catalog = tmp_path / "codex", load_catalog(write_catalog(tmp_path))
    fetch(catalog, codex, RunLog(tmp_path / "runs"), only=["react"], delay_s=0, transport=server([]))
    seen = []
    outcomes = fetch(catalog, codex, RunLog(tmp_path / "runs"), only=["react"], delay_s=0, transport=server(seen))
    assert [o for _, o in outcomes] == ["present"] and seen == []

    (codex / "llm-agents" / "react.pdf").write_bytes(b"%PDF-corrupted")   # checksum no longer matches
    outcomes = fetch(catalog, codex, RunLog(tmp_path / "runs"), only=["react"], delay_s=0, transport=server(seen))
    assert [o for _, o in outcomes] == ["downloaded"] and len(seen) == 1


def test_http_error_is_reported_and_leaves_no_file(tmp_path):
    codex = tmp_path / "codex"
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    outcomes = fetch(load_catalog(write_catalog(tmp_path)), codex, RunLog(tmp_path / "runs"),
                     only=["react"], delay_s=0, transport=transport)
    assert [o for _, o in outcomes] == ["http_404"]
    assert read_manifest(codex) == {} and not list(codex.rglob("*.pdf*"))


@pytest.mark.parametrize("change, message", [
    ({"topic": "astrology"}, "topic"),
    ({"url": "http://arxiv.org/pdf/1"}, "https"),
    ({"kind": "epub"}, "kind"),
])
def test_bad_entries_are_rejected(change, message):
    fields = {"id": "x", "topic": "rag", "title": "t", "url": "https://arxiv.org/pdf/1", **change}
    with pytest.raises(CatalogError, match=message):
        Item(**fields)


def test_duplicate_ids_and_unknown_only_are_rejected(tmp_path):
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog(write_catalog(tmp_path, CATALOG + CATALOG))
    with pytest.raises(CatalogError, match="not in the catalog"):
        fetch(load_catalog(write_catalog(tmp_path)), tmp_path / "codex", RunLog(tmp_path / "runs"),
              only=["nope"], transport=server([]))


def test_cli_list(tmp_path, capsys):
    config = tmp_path / "assistant.toml"
    config.write_text(f'''
[assistant]
runs_dir = "{(tmp_path / 'runs').as_posix()}"
codex_dir = "{(tmp_path / 'codex').as_posix()}"
[tiers.local]
kind = "local"
base_url = "http://local/v1"
model = "m"
''', encoding="utf-8")
    assert main(["--config", str(config), "codex", "list", "--catalog", str(write_catalog(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "react" in out and "html" in out and "0 of 3" in out


def test_shipped_catalog_loads():
    items = load_catalog(Path(__file__).resolve().parent.parent / "codex" / "catalog.toml")
    assert len(items) >= 20 and all(item.licence for item in items)


def test_page_items_are_printed_to_pdf_by_the_browser(tmp_path, monkeypatch):
    import subprocess
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out = next(a for a in cmd if a.startswith("--print-to-pdf=")).split("=", 1)[1]
        Path(out).write_bytes(PDF)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setenv("CODEX_BROWSER", str(tmp_path / "edge.exe"))
    (tmp_path / "edge.exe").write_bytes(b"")
    monkeypatch.setattr("assistant.codex.subprocess.run", fake_run)
    catalog = write_catalog(tmp_path, '''
[[item]]
id = "whatsnew-3.15"
topic = "python"
title = "What's New In Python 3.15"
url = "https://docs.python.org/3.15/whatsnew/3.15.html"
kind = "page"
''')
    codex = tmp_path / "codex"
    outcomes = fetch(load_catalog(catalog), codex, RunLog(tmp_path / "runs"), delay_s=0, transport=server([]))

    assert [outcome for _, outcome in outcomes] == ["downloaded"]
    assert calls[0][0] == str(tmp_path / "edge.exe") and calls[0][-1].endswith("3.15.html")
    assert (codex / "python" / "whatsnew-3.15.pdf").read_bytes() == PDF
    assert read_manifest(codex)["whatsnew-3.15"]["file"] == "python/whatsnew-3.15.pdf"


TEXT_CATALOG = '''
[[item]]
id = "k-quants-pr"
topic = "local-models"
title = "k-quants"
url = "https://github.com/ggml-org/llama.cpp/pull/1684"
kind = "text"

[[item]]
id = "llama-cpp-readme"
topic = "local-models"
title = "llama.cpp README"
url = "https://github.com/ggml-org/llama.cpp/blob/master/README.md"
kind = "text"

[[item]]
id = "landing"
topic = "local-models"
title = "A landing page"
url = "https://example.org/notes.md"
kind = "text"
'''


def test_text_items_keep_the_pr_description_and_raw_files(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"title": "k-quants", "body": "This PR adds 2-6 bit k-quants.",
                                             "comments": 99})
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="# llama.cpp\r\n\r\n| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n")
        return httpx.Response(200, text="<!DOCTYPE html><html>Sign in</html>")

    codex = tmp_path / "codex"
    outcomes = fetch(load_catalog(write_catalog(tmp_path, TEXT_CATALOG)), codex, RunLog(tmp_path / "runs"),
                     delay_s=0, transport=httpx.MockTransport(handler))
    assert [o for _, o in outcomes] == ["downloaded", "downloaded", "not_text"]
    assert seen[:2] == ["https://api.github.com/repos/ggml-org/llama.cpp/pulls/1684",
                        "https://raw.githubusercontent.com/ggml-org/llama.cpp/master/README.md"]
    pr = (codex / "local-models" / "k-quants-pr.md").read_text(encoding="utf-8")
    assert pr == "# k-quants\n\nThis PR adds 2-6 bit k-quants."
    readme = (codex / "local-models" / "llama-cpp-readme.md").read_text(encoding="utf-8")
    assert "\r" not in readme and "| 1 | 2 |" in readme
    rows = read_manifest(codex)
    assert rows["k-quants-pr"]["file"] == "local-models/k-quants-pr.md" and "landing" not in rows
