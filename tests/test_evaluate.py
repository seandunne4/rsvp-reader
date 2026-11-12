"""Scoring against a golden doc."""

import copy

from rsvp.evaluate import score


def doc(paras, sections=None, figures=None, title="T", ocr=()):
    return {
        "title": title, "ingest": {"ocr_pages": list(ocr)},
        "sections": sections or [{"id": "s0", "title": None}],
        "body": [{"id": f"b{i}", "text": t, "src": [{"ext": f"b{i}"}]} for i, t in enumerate(paras)],
        "figures": figures or [],
    }


GOLD = doc(["The first paragraph says one thing. It has two sentences.",
            "The second paragraph says another thing entirely.",
            "A third paragraph closes the piece with a final point."],
           sections=[{"id": "s0", "title": None}, {"id": "s1", "title": "Middle Part"}],
           figures=[{"id": "f1", "kind": "chart", "label": "Exhibit 1", "images": ["figures/f1.png"],
                     "anchor": {"block": "b1"}}])


def test_identical_scores_perfect():
    r = score(copy.deepcopy(GOLD), GOLD)
    assert r["text_recall"] == r["text_precision"] == 1.0
    assert not r["missing"] and not r["leaked"]
    assert r["order_tau"] == 1.0 and r["heading_recall"] == 1.0
    assert r["figure_kind"] == r["figure_anchor"] == 1.0 and r["title_match"] and r["ocr_match"]


def test_leaks_missing_order_and_figures_detected():
    out = copy.deepcopy(GOLD)
    out["body"] = [out["body"][2], out["body"][0]]                                     # dropped + reordered
    out["body"].append({"id": "x", "text": "Subscribe to our newsletter today for more.", "src": [{"ext": "x"}]})
    out["sections"] = [{"id": "s0", "title": None}]                                    # heading lost
    out["figures"][0]["kind"] = "photo"
    out["figures"][0]["anchor"] = None
    r = score(out, GOLD)
    assert r["text_recall"] < 1 and r["text_precision"] < 1
    assert any("second paragraph" in s for s in r["missing"])
    assert any("newsletter" in s for s in r["leaked"])
    assert r["order_tau"] < 1
    assert r["heading_recall"] == 0
    assert r["figure_kind"] == 0 and r["figure_anchor"] == 0


def test_punctuation_and_quote_style_ignored():
    out = copy.deepcopy(GOLD)
    out["body"][0]["text"] = "The first paragraph says one thing — it has two sentences!"
    r = score(out, GOLD)
    assert r["text_recall"] == 1.0 and r["text_precision"] == 1.0
