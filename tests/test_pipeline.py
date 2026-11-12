"""End-to-end: extract + rules + assemble on the sample PDFs (OCR'd Oreo excluded for speed)."""

from pathlib import Path

import pytest

from rsvp.assemble import assemble
from rsvp.extract import extract
from rsvp.rules import write_rules

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    lib = tmp_path_factory.mktemp("library")
    out = {}
    for name in ("cattle-wsj", "goose-island-tribune", "google-doubleclick-nyt", "case-amazon", "case-enterprise"):
        pdf = FIX / f"{name}.pdf"
        if not pdf.exists():
            continue
        d = extract(pdf, library=lib, ocr_mode="never", log=lambda *_: None)
        write_rules(d)
        doc, warnings, errors = assemble(d)
        assert not errors, errors
        out[name] = doc
    return out


def get(docs, name):
    if name not in docs:
        pytest.skip(f"fixture {name} not present")
    return docs[name]


def body_text(doc):
    return "\n".join(p["text"] for p in doc["body"])


def test_tribune_boilerplate_removed_and_paragraphs_joined(docs):
    doc = get(docs, "goose-island-tribune")
    t = body_text(doc)
    for junk in ("Subscribers, get", "[Most read]", "ADVERTISEMENT", "SPONSORED", "Thank you for supporting",
                 "chicagotribune.com", "Cash Back"):
        assert junk not in t, junk
    assert "Hall’s industry-rattling decision to sell Goose Island to Anheuser-Busch InBev for $38.8 million in 2011." in t
    assert doc["title"].startswith("Goose Island sale 10 years later")
    assert t.rstrip().endswith("craft beer visionary.")


def test_nyt_sections_and_header_removed(docs):
    doc = get(docs, "google-doubleclick-nyt")
    assert [s["title"] for s in doc["sections"][1:]] == ["The Prize", "The Review", "The Competition"]
    assert "nytimes.com" not in body_text(doc)
    assert doc["byline"] == "By Steve Lohr"


def test_wsj_charts_are_charts_and_captions_attached(docs):
    doc = get(docs, "cattle-wsj")
    kinds = {f["id"]: f["kind"] for f in doc["figures"]}
    assert kinds["p3-f1"] == "chart" and kinds["p4-f1"] == "chart" and kinds["p1-f1"] == "photo"
    photo = next(f for f in doc["figures"] if f["id"] == "p1-f1")
    assert photo["caption"] == "Angus cattle in Nebraska last month."
    assert doc["subtitle"].endswith("which they say underpay for livestock")
    assert "Angus cattle in Nebraska" not in body_text(doc)


def test_case_watermark_running_heads_and_endnotes_removed(docs):
    doc = get(docs, "case-amazon")
    t = body_text(doc)
    for junk in ("This document is authorized for use", "KELLOGG SCHOOL OF MANAGEMENT", "KE1289", "Feeding the Dinosaurs"):
        assert junk not in t, junk
    assert doc["title"] == "Amazon (A): Building a Retail Giant on a Stack of Books"
    assert doc["endnotes"]


def test_case_exhibits_anchor_to_references(docs):
    doc = get(docs, "case-amazon")
    by_label = {f["label"]: f for f in doc["figures"] if f["label"]}
    for n in (1, 2, 3, 5, 10, 11):
        assert by_label[f"Exhibit {n}"]["anchor"]["method"] == "reference", n
    assert len(by_label["Exhibit 12"]["images"]) == 5
    assert by_label["Exhibit 4"]["placement"] == "end"  # never cited in the text
    assert "Exhibit 15" in by_label


def test_case_headings_in_other_typeface(docs):
    doc = get(docs, "case-enterprise")
    titles = [s["title"] for s in doc["sections"]]
    assert "Acquiring Vanguard" in titles and "Origins" in titles
    assert doc["title"] == "Enterprise Rent-A-Car in the US"
    assert not doc.get("subtitle")
    assert "Kellogg School of Management" not in body_text(doc)


def test_no_paragraph_ends_mid_sentence_across_pages(docs):
    for name, doc in docs.items():
        for a, b in zip(doc["body"], doc["body"][1:]):
            if a["section"] == b["section"] and a["type"] == b["type"] == "para":
                assert a["text"].rstrip()[-1:] in '.!?:"”’)…' or len(a["text"]) < 40, (name, a["text"][-60:])
