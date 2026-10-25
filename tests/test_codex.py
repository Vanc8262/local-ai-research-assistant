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
