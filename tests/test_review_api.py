"""Review API: page/label state and saving the user label layer (re-assembles the doc)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rsvp.assemble import assemble
from rsvp.extract import extract
from rsvp.rules import write_rules
from rsvp.server import create_app

FIX = Path(__file__).parent / "fixtures" / "cattle-wsj.pdf"


@pytest.fixture
def setup(tmp_path):
    if not FIX.exists():
        pytest.skip("cattle fixture not present")
    d = extract(FIX, library=tmp_path, ocr_mode="never", log=lambda *_: None)
    write_rules(d)
    assemble(d)
    return TestClient(create_app(tmp_path)), d


def test_review_state_has_every_block_with_its_role(setup):
    client, d = setup
    s = client.get(f"/api/docs/{d.name}/review").json()
    assert len(s["roles"]) == len(s["blocks"]) > 0
    assert s["roles"]["p1-b1"]["role"] == "title" and s["roles"]["p1-b1"]["by"] == "rules"
    assert s["doc"]["title"].startswith("Cattle Ranchers")
    assert s["user"]["schema"] == "rsvp-labels/1"


def test_saving_user_labels_reassembles(setup):
    client, d = setup
    url = f"/api/docs/{d.name}"
    first = client.get(url + "/review").json()["doc"]["body"][0]
    user = {"blocks": {first["id"]: {"role": "boilerplate"}},
            "figures": {"p3-f1": {"anchor": {"block": "p2-b1"}}}}
    s = client.put(url + "/labels/user", json=user).json()
    assert s["roles"][first["id"]] == {"role": "boilerplate", "by": "user", "figure": None, "level": None}
    assert first["id"] not in [p["id"] for p in s["doc"]["body"]]
    chart = next(f for f in s["doc"]["figures"] if f["id"] == "p3-f1")
    assert chart["anchor"]["block"] == "p2-b1" and chart["anchor"]["method"] == "manual"
    saved = json.loads((d / "labels.user.json").read_text(encoding="utf-8"))
    assert saved["by"] == "user" and saved["extract_sha"]
    assert s["doc"]["ingest"]["method"] == "rules+user"


def test_undoing_everything_removes_user_layer(setup):
    client, d = setup
    url = f"/api/docs/{d.name}/labels/user"
    client.put(url, json={"blocks": {"p2-b1": {"role": "quote"}}})
    assert (d / "labels.user.json").exists()
    s = client.put(url, json={"blocks": {}, "figures": {}}).json()
    assert not (d / "labels.user.json").exists()
    assert s["doc"]["ingest"]["method"] == "rules"


def test_invalid_user_labels_rejected_and_not_written(setup):
    client, d = setup
    r = client.put(f"/api/docs/{d.name}/labels/user", json={"blocks": {"p99-b1": {"role": "body"}}})
    assert r.status_code == 400
    assert "unknown block" in json.dumps(r.json())
    assert not (d / "labels.user.json").exists()


def test_review_page_served(setup):
    client, d = setup
    assert client.get(f"/review/{d.name}").status_code == 200
