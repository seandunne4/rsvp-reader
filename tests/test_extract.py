"""Extraction checks against the sample PDFs (skipped if a fixture isn't present)."""

from pathlib import Path

import pymupdf
import pytest

from rsvp.extract import decide_ocr, find_figures, native_blocks

FIX = Path(__file__).parent / "fixtures"


def open_fixture(name):
    path = FIX / name
    if not path.exists():
        pytest.skip(f"fixture {name} not present")
    return pymupdf.open(path)


def page_data(name, pno):
    page = open_fixture(name)[pno - 1]
    blocks = native_blocks(page)
    return page, blocks, find_figures(page, blocks)


def texts(blocks):
    return "\n".join(b["text"] for b in blocks)


@pytest.mark.parametrize("name,expect_ocr", [
    ("oreo-wsj.pdf", True),
    ("cattle-wsj.pdf", False),
    ("goose-island-tribune.pdf", False),
    ("google-doubleclick-nyt.pdf", False),
    ("case-amazon.pdf", False),
    ("case-enterprise.pdf", False),
])
def test_garbage_detection_per_page(name, expect_ocr):
    doc = open_fixture(name)
    decisions = []
    for page in doc:
        blocks = native_blocks(page)
        decisions.append(decide_ocr(blocks, find_figures(page, blocks), page.rect, "auto")[0])
    assert all(d == expect_ocr for d in decisions), decisions


def test_missing_spaces_rebuilt():
    _, blocks, _ = page_data("goose-island-tribune.pdf", 1)
    assert "Anheuser-Busch got what it needed from the deal" in texts(blocks)


def test_spurious_and_letter_spaced_text_fixed():
    _, blocks, _ = page_data("case-enterprise.pdf", 3)
    t = texts(blocks)
    assert "US Car Rental Revenue by Company" in t
    assert "Kellogg School of Management" in t


def test_small_caps_running_head():
    _, blocks, _ = page_data("case-amazon.pdf", 3)
    assert "AMAZON (A): BUILDING A RETAIL GIANT" in texts(blocks)


def test_superscript_note_refs_removed_from_text():
    _, blocks, _ = page_data("case-amazon.pdf", 1)
    para = next(b for b in blocks if b["text"].startswith("Historically"))
    assert [s["text"] for s in para["sups"]] == ["1"]


def test_browser_print_lines_merged_into_paragraphs():
    _, blocks, _ = page_data("goose-island-tribune.pdf", 2)
    assert any(b["text"].startswith("Goose Island Beer Co. has grown wildly") and b["text"].endswith("in 1988.")
               for b in blocks)


def test_charts_found_and_backgrounds_ignored():
    for pno in (3, 4):
        _, _, figs = page_data("cattle-wsj.pdf", pno)
        assert len(figs) == 1
    _, _, figs = page_data("goose-island-tribune.pdf", 2)
    assert all(f["origin"] == "raster" for f in figs)  # no fake vector "figures" from page furniture


def test_inline_figure_does_not_spawn_region():
    _, _, figs = page_data("case-enterprise.pdf", 6)
    assert [f["origin"] for f in figs] == ["raster"]


def test_exhibit_page_figures():
    _, _, figs = page_data("case-amazon.pdf", 31)
    assert [f["origin"] for f in figs] == ["vector", "region"]  # drawn chart + text table


def test_ocr_reads_garbage_page():
    from rsvp.ocr import ocr_page

    page = open_fixture("oreo-wsj.pdf")[1]
    text = " ".join(l["text"] for l in ocr_page(page))
    assert "Hershey makes Reese" in text
