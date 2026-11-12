"""Assembler and label-layer behaviour on a small synthetic extraction (no PDFs needed)."""

import json

import pytest

from rsvp import labels as L
from rsvp.assemble import assemble


def block(bid, page, y, text, size=12.0, flags=()):
    return {"id": bid, "page": page, "bbox": [72, y, 540, y + 40], "text": text, "lines": 3,
            "font": {"size": size, "name": "Times", "bold": False, "italic": False},
            "source": "native", "quality": 1.0, "flags": list(flags)}


@pytest.fixture
def doc_dir(tmp_path):
    ex = {
        "schema": "rsvp-extract/1", "id": "t-1",
        "source": {"file": "source.pdf", "original_name": "t.pdf", "sha256": "x", "pages": 3},
        "pages": [{"n": n, "ocr": False} for n in (1, 2, 3)],
        "blocks": [
            block("p1-b1", 1, 40, "Running head", 8, ["margin_top"]),
            block("p1-b2", 1, 100, "The Big Title", 24),
            block("p1-b3", 1, 200, "Sales grew 12% in 2020. See Exhibit 1 for details. Then more."),
            block("p1-b4", 1, 300, "This paragraph runs onto the next"),
            block("p2-b1", 2, 100, "page and finishes here."),
            block("p2-b2", 2, 200, "Section Two", 16),
            block("p2-b3", 2, 300, "Another paragraph mentions Exhibit 1 again."),
            block("p3-b1", 3, 100, "Exhibit 1: Revenue by year", 12),
        ],
        "figures": [
            {"id": "p2-f1", "page": 2, "bbox": [72, 250, 300, 290], "origin": "raster", "image": "figures/p2-f1.png"},
            {"id": "p3-f1", "page": 3, "bbox": [72, 150, 540, 500], "origin": "raster", "image": "figures/p3-f1.png"},
        ],
    }
    (tmp_path / "extract.json").write_text(json.dumps(ex), encoding="utf-8")
    rules = L.new_labels(tmp_path, "rules")
    rules["blocks"] = {
        "p1-b1": {"role": "boilerplate"}, "p1-b2": {"role": "title"}, "p1-b3": {"role": "body"},
        "p1-b4": {"role": "body"}, "p2-b1": {"role": "body"}, "p2-b2": {"role": "body"},
        "p2-b3": {"role": "body"}, "p3-b1": {"role": "caption", "figure": "p3-f1"},
    }
    rules["merge"] = [["p1-b4", "p2-b1"]]
    L.write_layer(tmp_path, "rules", rules)
    return tmp_path


def test_rules_only(doc_dir):
    doc, warnings, errors = assemble(doc_dir)
    assert not errors
    assert doc["title"] == "The Big Title"
    texts = [p["text"] for p in doc["body"]]
    assert "This paragraph runs onto the next page and finishes here." in texts
    assert "Running head" not in " ".join(texts)
    assert (doc_dir / "doc.json").exists()


def test_higher_layer_wins_per_block(doc_dir):
    claude = L.new_labels(doc_dir, "claude")
    claude["blocks"] = {"p2-b2": {"role": "heading", "level": 2}}
    L.write_layer(doc_dir, "claude", claude)
    doc, _, _ = assemble(doc_dir)
    assert [s["title"] for s in doc["sections"]] == [None, "Section Two"]
    assert doc["body"][-1]["section"] == "s1"
    assert doc["ingest"]["method"] == "rules+claude"


def test_figure_fields_merge_across_layers(doc_dir):
    claude = L.new_labels(doc_dir, "claude")
    claude["figures"] = {"p2-f1": {"kind": "chart", "label": "Figure 9"}}
    user = L.new_labels(doc_dir, "user")
    user["figures"] = {"p2-f1": {"anchor": {"block": "p1-b3"}}}
    L.write_layer(doc_dir, "claude", claude)
    L.write_layer(doc_dir, "user", user)
    doc, _, _ = assemble(doc_dir)
    f = next(f for f in doc["figures"] if f["id"] == "p2-f1")
    assert (f["kind"], f["label"], f["anchor"]["block"], f["anchor"]["method"]) == ("chart", "Figure 9", "p1-b3", "manual")


def test_exhibit_anchors_at_end_of_first_referencing_sentence(doc_dir):
    doc, _, _ = assemble(doc_dir)
    ex1 = next(f for f in doc["figures"] if f["label"] == "Exhibit 1")
    assert ex1["caption"] == "Revenue by year"
    assert ex1["anchor"]["method"] == "reference" and ex1["anchor"]["block"] == "p1-b3"
    para = next(p for p in doc["body"] if p["id"] == "p1-b3")
    assert para["text"][: ex1["anchor"]["char"]].endswith("See Exhibit 1 for details.")
    assert ex1["refs"] == [{"block": "p2-b3", "char": para and doc["body"][-1]["text"].index("Exhibit 1")}]


def test_unlabeled_photo_anchors_by_proximity(doc_dir):
    doc, _, _ = assemble(doc_dir)
    photo = next(f for f in doc["figures"] if f["id"] == "p2-f1")
    assert photo["anchor"]["method"] == "proximity"


def test_digit_changing_fix_warns_unless_explained(doc_dir):
    claude = L.new_labels(doc_dir, "claude")
    claude["text_fixes"] = {"p1-b3": [{"find": "12%", "replace": "13%"}]}
    L.write_layer(doc_dir, "claude", claude)
    _, warnings, _ = assemble(doc_dir)
    assert any("changes digits" in w for w in warnings)
    claude["text_fixes"]["p1-b3"][0]["why"] = "OCR misread 3 as 2; checked page image"
    L.write_layer(doc_dir, "claude", claude)
    doc, warnings, _ = assemble(doc_dir)
    assert not any("changes digits" in w for w in warnings)
    assert "Sales grew 13%" in doc["body"][0]["text"]


def test_invalid_labels_block_writing(doc_dir):
    (doc_dir / "doc.json").unlink(missing_ok=True)
    claude = L.new_labels(doc_dir, "claude")
    claude["blocks"] = {"p9-b9": {"role": "body"}, "p1-b3": {"role": "paragraph"}}
    L.write_layer(doc_dir, "claude", claude)
    _, _, errors = assemble(doc_dir)
    assert any("unknown block" in e for e in errors) and any("unknown role" in e for e in errors)
    assert not (doc_dir / "doc.json").exists()
