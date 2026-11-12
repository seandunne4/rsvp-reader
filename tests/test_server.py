"""Server API: library listing, doc/figure serving, progress and settings, upload jobs."""

import json
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from rsvp.server import create_app


@pytest.fixture
def lib(tmp_path):
    d = tmp_path / "demo-1234abcd"
    (d / "figures").mkdir(parents=True)
    (d / "figures" / "p1-f1.png").write_bytes(b"\x89PNG fake")
    (d / "extract.json").write_text("{}", encoding="utf-8")
    doc = {
        "schema": "rsvp-doc/1", "id": d.name, "title": "Demo Reading", "byline": "By Someone",
        "source": {"file": "source.pdf", "original_name": "demo.pdf", "sha256": "x", "pages": 1},
        "ingest": {"method": "rules", "warnings": [], "created_at": "2026-01-01T00:00:00+00:00"},
        "sections": [{"id": "s0", "title": None}],
        "body": [{"id": "p1-b1", "type": "para", "section": "s0", "text": "one two three four"},
                 {"id": "p1-b2", "type": "para", "section": "s0", "text": "five six seven eight"}],
        "figures": [], "endnotes": [], "recaps": [], "stats": {"words": 8, "paragraphs": 2},
    }
    (d / "doc.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(lib):
    return TestClient(create_app(lib))


def test_library_lists_docs_with_progress(client):
    r = client.get("/api/docs").json()
    assert [d["title"] for d in r["docs"]] == ["Demo Reading"]
    assert r["docs"][0]["progress"] == 0
    client.put("/api/docs/demo-1234abcd/progress", json={"block": "p1-b2", "word": 2})
    d = client.get("/api/docs").json()["docs"][0]
    assert d["progress"] == 0.75  # 6 of 8 words read


def test_doc_and_pages(client):
    assert client.get("/api/docs/demo-1234abcd").json()["title"] == "Demo Reading"
    assert client.get("/").status_code == 200
    assert client.get("/read/demo-1234abcd").status_code == 200
    assert client.get("/read/nope").status_code == 404


def test_files_are_confined_to_figures_and_pages(client):
    assert client.get("/api/docs/demo-1234abcd/files/figures/p1-f1.png").status_code == 200
    assert client.get("/api/docs/demo-1234abcd/files/extract.json").status_code == 404
    assert client.get("/api/docs/demo-1234abcd/files/figures/../doc.json").status_code == 404
    assert client.get("/api/docs/..%2F..%2Fsecret/files/figures/x.png").status_code == 404


def test_progress_newest_write_wins(client, lib):
    url = "/api/docs/demo-1234abcd/progress"
    assert client.put(url, json={"block": "p1-b2", "word": 1, "updated_at": "2026-05-01T10:00:00.000+00:00"}).json()["saved"]
    stale = client.put(url, json={"block": "p1-b1", "word": 0, "updated_at": "2026-05-01T09:00:00.000+00:00"}).json()
    assert stale["saved"] is False and stale["progress"]["block"] == "p1-b2"
    assert json.loads((lib / "demo-1234abcd" / "progress.json").read_text())["word"] == 1


def test_settings_defaults_and_update(client, lib):
    assert client.get("/api/settings").json()["wpm"] == 300
    s = client.put("/api/settings", json={"wpm": 2000, "autopause": False}).json()
    assert s["wpm"] == 1000 and s["autopause"] is False and s["recaps_auto"] is False
    assert json.loads((lib / "settings.json").read_text())["autopause"] is False


def test_upload_runs_pipeline(client, tmp_path):
    pdf = tmp_path / "upload-sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Small Test Title", fontsize=24)
    y = 160
    for i in range(12):
        page.insert_text((72, y), f"This is sentence number {i} of a plain test paragraph for the reader.", fontsize=11)
        y += 15
    doc.save(pdf)
    with open(pdf, "rb") as fh:
        job = client.post("/api/upload", files={"file": ("upload-sample.pdf", fh, "application/pdf")}).json()["job"]
    for _ in range(100):
        st = client.get(f"/api/jobs/{job}").json()
        if st["status"] != "processing":
            break
        time.sleep(0.2)
    assert st["status"] == "done", st
    titles = [d["title"] for d in client.get("/api/docs").json()["docs"]]
    assert "A Small Test Title" in titles


def test_upload_rejects_non_pdf(client):
    r = client.post("/api/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
