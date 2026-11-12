"""Deterministic PDF extraction: text blocks, figures, page images -> extract.json.

No judgement calls about roles or reading order happen here; that's the labelers' job
(rules, /ingest, review screen). This step only has to be faithful and repeatable, so
block and figure ids stay stable across re-runs.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from . import textutil
from .library import doc_id_for, library_dir, sha256_file

EXTRACTOR_VERSION = "1"
PAGE_DPI = 100      # page images for review and /ingest
FIGURE_DPI = 200    # figure crops shown in the reader
MARGIN_FRAC = 0.07  # top/bottom band treated as header/footer territory
SUP_OPEN, SUP_CLOSE = "\x00", "\x01"
EXHIBIT_TITLE = re.compile(r"^\s*(Exhibit|Figure|Fig\.|Table|Chart)\s+\d+[A-Z]?\b", re.I)
SOURCE_LINE = re.compile(r"^\s*(Source|Sources|Note|Notes)\s*:", re.I)

RAWDICT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP


def _r(bbox) -> list[float]:
    return [round(float(v), 1) for v in bbox]


# --- line / block text reconstruction ----------------------------------------------------

def _line_text(line: dict) -> tuple[str, float]:
    """Rebuild one line's text from glyph positions.

    Spaces come from geometry, not from the PDF's own space glyphs, which fixes both
    missing spaces ("gotwhatit") and spurious ones ("US C   ar Rental"). Letter-spaced
    running heads ("E n t e r p r i s e") are handled by measuring gaps relative to the
    line's typical letter gap. Superscripts are wrapped in SUP markers.
    """
    glyphs = []  # (char, x0, x1, size, baseline_y, superscript_flag)
    for span in line["spans"]:
        sup_flag = bool(span["flags"] & 1)
        for ch in span["chars"]:
            glyphs.append((ch["c"], ch["bbox"][0], ch["bbox"][2], span["size"], ch["origin"][1], sup_flag))
    ink = [g for g in glyphs if not g[0].isspace()]
    if not ink:
        return "", 0.0

    sizes = Counter(round(g[3], 1) for g in ink)
    dom = sizes.most_common(1)[0][0]
    base = statistics.median(g[4] for g in ink if round(g[3], 1) == dom)

    def is_sup(g) -> bool:
        return g[5] or (g[3] < 0.85 * dom and g[4] < base - 0.15 * dom)

    # Typical gap between adjacent glyphs. Word gaps are a minority, so the median is the
    # letter gap: ~0 for normal text, larger for tracked/letter-spaced running heads (some
    # PDFs even put a space glyph between every letter of those).
    gaps, prev = [], None
    for g in ink:
        if prev is not None and not is_sup(g) and not is_sup(prev):
            gaps.append(g[1] - prev[2])
        prev = g
    letter_gap = max(0.0, statistics.median(gaps)) if gaps else 0.0

    out, prev, saw_space, in_sup = [], None, False, False
    for g in glyphs:
        if g[0].isspace():
            saw_space = True
            continue
        sup = is_sup(g)
        if prev is not None:
            gap = g[1] - prev[2]
            need = letter_gap + (0.08 if saw_space else 0.17) * dom
            if gap > need and not (sup and not saw_space):
                if in_sup:
                    out.append(SUP_CLOSE)
                    in_sup = False
                out.append(" ")
        if sup and not in_sup:
            out.append(SUP_OPEN)
            in_sup = True
        elif not sup and in_sup:
            out.append(SUP_CLOSE)
            in_sup = False
        out.append(g[0])
        prev, saw_space = g, False
    if in_sup:
        out.append(SUP_CLOSE)
    text = unicodedata.normalize("NFKC", "".join(out))  # also expands ligatures (ﬁ -> fi)
    return text, dom


def _pull_sups(text: str) -> tuple[str, list[dict]]:
    """Remove superscript markers, returning clean text and [{at, text}] note refs."""
    sups, clean, i = [], [], 0
    while i < len(text):
        if text[i] == SUP_OPEN:
            j = text.find(SUP_CLOSE, i)
            j = len(text) if j < 0 else j
            sups.append({"at": len("".join(clean)), "text": text[i + 1:j]})
            i = j + 1
        else:
            clean.append(text[i])
            i += 1
    return "".join(clean), [s for s in sups if s["text"].strip()]


def _font_info(block: dict) -> dict:
    count: Counter = Counter()
    for line in block["lines"]:
        for span in line["spans"]:
            n = sum(1 for c in span["chars"] if not c["c"].isspace())
            if n:
                count[(round(span["size"], 1), span["font"], span["flags"])] += n
    if not count:
        return {"size": 0, "name": "", "bold": False, "italic": False}
    (size, name, flags), _ = count.most_common(1)[0]
    return {
        "size": size,
        "name": name,
        "bold": bool(flags & 16) or "bold" in name.lower(),
        "italic": bool(flags & 2) or "italic" in name.lower(),
    }


def _family(font_name: str) -> str:
    """'BCDEEE+TimesNewRomanPS-BoldMT' -> 'timesnewroman'."""
    name = font_name.split("+")[-1].lower()
    name = re.split(r"[-,]", name)[0]
    return re.sub(r"(ps|mt|bold|italic|regular|oblique|light|medium)+$", "", name)


def _line_font(line: dict) -> tuple[str, bool]:
    """(dominant font name, bold) of a line, by character count."""
    count: Counter = Counter()
    for span in line["spans"]:
        n = sum(1 for c in span["chars"] if not c["c"].isspace())
        if n:
            count[(span["font"], bool(span["flags"] & 16) or "bold" in span["font"].lower())] += n
    return count.most_common(1)[0][0] if count else ("", False)


def _split_paragraphs(block: dict) -> list[dict]:
    """Split a PyMuPDF block where the vertical gap between lines is paragraph-sized, or
    where the font size jumps (authors line vs. title in a case's first-page block)."""
    lines = block["lines"]
    if len(lines) < 2:
        return [block]
    heights = [l["bbox"][3] - l["bbox"][1] for l in lines]
    h = statistics.median(heights)
    sizes = [_line_text(l)[1] for l in lines]
    fonts = [_line_font(l) for l in lines]
    parts, cur = [], [lines[0]]
    last = 0  # index of the last line with ink (empty lines carry no font info)
    for i, (prev, line) in enumerate(zip(lines, lines[1:]), start=1):
        if not sizes[i]:
            cur.append(line)
            continue
        gap = line["bbox"][1] - prev["bbox"][3]
        a, b = sizes[last], sizes[i]
        size_jump = a and b and max(a, b) / min(a, b) > 1.2
        # A line in a different typeface (and weight or size) next to body text: a title
        # or sticky bar the PDF lumped into one block ("Exhibit 15: ..." + the letter).
        (fa, ba), (fb, bb) = fonts[last], fonts[i]
        font_switch = bool(a) and _family(fa) != _family(fb) and (ba != bb or max(a, b) / min(a, b) > 1.05)
        if gap > 0.55 * h or size_jump or font_switch:
            parts.append(cur)
            cur = []
        cur.append(line)
        last = i
    parts.append(cur)
    if len(parts) == 1:
        return [block]
    out = []
    for ls in parts:
        x0 = min(l["bbox"][0] for l in ls); y0 = min(l["bbox"][1] for l in ls)
        x1 = max(l["bbox"][2] for l in ls); y1 = max(l["bbox"][3] for l in ls)
        out.append({**block, "lines": ls, "bbox": (x0, y0, x1, y1)})
    return out


def _finish(block: dict) -> dict:
    """Compute text/sups/quality from the block's raw line texts."""
    text, sups = _pull_sups(textutil.join_lines(block["_lines"]))
    block["text"], block["sups"] = text.strip(), sups
    block["quality"] = round(textutil.quality(block["text"]), 3)
    block["lines"] = len(block["_lines"])
    return block


def _same_paragraph(prev: dict, b: dict) -> bool:
    """Should block b continue paragraph prev? (Browser prints emit one block per line.)"""
    pf, bf = prev["font"], b["font"]
    if abs(pf["size"] - bf["size"]) > 0.3 or pf["name"] != bf["name"]:
        return False
    line_h = (prev["bbox"][3] - prev["bbox"][1]) / max(1, len(prev["_lines"]))
    gap = b["bbox"][1] - prev["bbox"][3]
    if not (-2 <= gap <= 0.5 * line_h):
        return False
    if abs(b["bbox"][0] - prev["bbox"][0]) >= 4:  # different left edge
        return False
    # A short last line ends a paragraph ("By Patrick Thomas" then the dateline).
    right = max(prev["bbox"][2], b["bbox"][2])
    width = right - prev["bbox"][0]
    return prev["_last_x1"] >= right - 0.15 * width


def native_blocks(page: pymupdf.Page) -> list[dict]:
    raw = page.get_text("rawdict", flags=RAWDICT_FLAGS, sort=True)
    out: list[dict] = []
    for b in raw["blocks"]:
        if b.get("type") != 0:
            continue
        for part in _split_paragraphs(b):
            line_texts = [t for t in (_line_text(l)[0] for l in part["lines"]) if t.strip()]
            if not line_texts:
                continue
            blk = {"bbox": list(part["bbox"]), "_lines": line_texts, "font": _font_info(part), "source": "native",
                   "_last_x1": part["lines"][-1]["bbox"][2]}
            if out and _same_paragraph(out[-1], blk):
                prev = out[-1]
                prev["_lines"] += line_texts
                prev["bbox"] = _union(prev["bbox"], blk["bbox"])
                prev["_last_x1"] = blk["_last_x1"]
                continue
            out.append(blk)
    result = []
    for blk in out:
        _finish(blk)
        if blk["text"]:
            blk["bbox"] = _r(blk["bbox"])
            del blk["_lines"], blk["_last_x1"]
            result.append(blk)
    return result


# --- OCR blocks ----------------------------------------------------------------------------

def _center_in(bbox, region, pad: float = 2.0) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return region[0] - pad <= cx <= region[2] + pad and region[1] - pad <= cy <= region[3] + pad


def group_ocr_lines(lines: list[dict]) -> list[dict]:
    """Group OCR lines into paragraph blocks by vertical spacing and left alignment."""
    lines = sorted(lines, key=lambda l: (round(l["bbox"][1]), l["bbox"][0]))
    blocks: list[list[dict]] = []
    for line in lines:
        h = line["bbox"][3] - line["bbox"][1]
        placed = False
        for blk in reversed(blocks[-3:]):
            last = blk[-1]
            lh = last["bbox"][3] - last["bbox"][1]
            gap = line["bbox"][1] - last["bbox"][3]
            overlap = min(line["bbox"][2], last["bbox"][2]) - max(line["bbox"][0], last["bbox"][0])
            similar = 0.7 < h / max(lh, 0.1) < 1.4
            if -0.3 * lh < gap < 0.75 * lh and overlap > 0.3 * min(
                line["bbox"][2] - line["bbox"][0], last["bbox"][2] - last["bbox"][0]
            ) and similar:
                blk.append(line)
                placed = True
                break
        if not placed:
            blocks.append([line])

    # Inside a paragraph only the last line is short. A short line followed by more lines
    # is a heading or the end of a paragraph, so split after it ("Victory dance" + body).
    split: list[list[dict]] = []
    for blk in blocks:
        widest = max(l["bbox"][2] - l["bbox"][0] for l in blk)
        cur: list[dict] = []
        for i, line in enumerate(blk):
            cur.append(line)
            if i < len(blk) - 1 and (line["bbox"][2] - line["bbox"][0]) < 0.6 * widest:
                split.append(cur)
                cur = []
        if cur:
            split.append(cur)
    blocks = split

    out = []
    for blk in blocks:
        text = textutil.join_lines([l["text"] for l in blk])
        heights = [l["bbox"][3] - l["bbox"][1] for l in blk]
        bbox = [min(l["bbox"][0] for l in blk), min(l["bbox"][1] for l in blk),
                max(l["bbox"][2] for l in blk), max(l["bbox"][3] for l in blk)]
        out.append({
            "bbox": _r(bbox),
            "text": text,
            "sups": [],
            "lines": len(blk),
            # OCR boxes hug the glyphs; font size is roughly 0.8x box height.
            "font": {"size": round(statistics.median(heights) * 0.8, 1), "name": "ocr", "bold": False, "italic": False},
            "source": "ocr",
            "quality": round(textutil.quality(text), 3),
            "ocr_score": round(statistics.mean(l["score"] for l in blk), 3),
        })
    return out


# --- figures -------------------------------------------------------------------------------

def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a, b) -> float:
    return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def _union(a, b) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _is_background(region, drawings, blocks) -> bool:
    """A drawing cluster that's page furniture (panels, shadows, bars), not a chart/table.

    Charts and tables have strokes (lines, curves, outlined boxes). Backgrounds are only
    fills. And a real chart never contains a paragraph of running prose.
    """
    inside = [d for d in drawings if _overlap(list(d["rect"]), region) > 0.5 * max(_area(list(d["rect"])), 1)]
    stroked = 0
    for d in inside:
        if d.get("type") in ("s", "fs") and (d.get("width") or 0) > 0:
            stroked += 1
        elif any(item[0] in ("l", "c") for item in d.get("items", [])) and d.get("type") != "f":
            stroked += 1
    if stroked < 2:
        return True
    for b in blocks:
        if len(b["text"]) > 100 and _overlap(b["bbox"], region) > 0.6 * max(_area(b["bbox"]), 1):
            return True
    return False


def find_figures(page: pymupdf.Page, blocks: list[dict]) -> list[dict]:
    pr = page.rect
    cands: list[dict] = []
    for info in page.get_image_info():
        b = list(pymupdf.Rect(info["bbox"]) & pr)
        if b[2] - b[0] >= 36 and b[3] - b[1] >= 36:
            cands.append({"bbox": b, "origin": "raster"})
    try:
        drawings = page.get_drawings()
        clusters = page.cluster_drawings(drawings=drawings) if drawings else []
    except Exception:
        drawings, clusters = [], []
    for r in clusters:
        b = list(r & pr)
        w, h = b[2] - b[0], b[3] - b[1]
        if w < 72 or h < 36 or (w > 0.9 * pr.width and h > 0.9 * pr.height):
            continue
        if _is_background(b, drawings, blocks):
            continue
        cands.append({"bbox": b, "origin": "vector"})

    # Merge overlapping candidates (e.g. map pins on a map, a drawn frame around a photo).
    merged: list[dict] = []
    for c in sorted(cands, key=lambda c: -_area(c["bbox"])):
        for m in merged:
            if _overlap(m["bbox"], c["bbox"]) > 0.2 * min(_area(m["bbox"]), _area(c["bbox"])):
                m["bbox"] = _union(m["bbox"], c["bbox"])
                if m["origin"] != c["origin"]:
                    m["origin"] = "mixed"
                break
        else:
            merged.append(dict(c))

    # Exhibit pages whose content isn't an image (text tables, letter excerpts): take the
    # region between the "Exhibit N:" title and the Source/Note line.
    body_blocks = [b for b in blocks if not _in_margin(b["bbox"], pr)]
    for i, b in enumerate(body_blocks):
        if not EXHIBIT_TITLE.match(b["text"]) or b["font"]["size"] < 9:
            continue
        title_bottom = b["bbox"][3]
        if any(title_bottom - 5 <= m["bbox"][1] <= title_bottom + 60 for m in merged):
            continue  # the title's own image/drawing sits right below it
        below = [x for x in body_blocks[i + 1:] if x["bbox"][1] >= b["bbox"][1]]
        stop = next((x for x in below if SOURCE_LINE.match(x["text"]) or EXHIBIT_TITLE.match(x["text"])
                     or (x["font"]["bold"] and x["font"]["size"] >= 14)), None)
        inside = [x for x in below if stop is None or x["bbox"][1] < stop["bbox"][1]]
        if not inside:
            continue
        pad = 4
        region = [min(x["bbox"][0] for x in inside) - pad, inside[0]["bbox"][1] - pad,
                  max(x["bbox"][2] for x in inside) + pad, max(x["bbox"][3] for x in inside) + pad]
        if any(_overlap(m["bbox"], region) > 0.5 * _area(region) for m in merged):
            continue  # already covered by an image or drawing
        merged.append({"bbox": region, "origin": "region"})

    merged.sort(key=lambda m: (m["bbox"][1], m["bbox"][0]))
    return merged


def _in_margin(bbox, pr) -> bool:
    return bbox[3] <= pr.y0 + MARGIN_FRAC * pr.height or bbox[1] >= pr.y1 - MARGIN_FRAC * pr.height


# --- page assembly -------------------------------------------------------------------------

def page_quality(blocks: list[dict]) -> tuple[float, float, int]:
    """(char-weighted quality, share of chars in garbage blocks, total chars)."""
    total = sum(len(b["text"]) for b in blocks)
    if not total:
        return 1.0, 0.0, 0
    q = sum(b["quality"] * len(b["text"]) for b in blocks) / total
    bad = sum(len(b["text"]) for b in blocks if b["quality"] < textutil.GARBAGE_THRESHOLD)
    return q, bad / total, total


def decide_ocr(blocks: list[dict], figures: list[dict], pr: pymupdf.Rect, mode: str) -> tuple[bool, str]:
    if mode == "never":
        return False, "disabled"
    if mode == "always":
        return True, "forced"
    q, bad, total = page_quality(blocks)
    img_area = sum(_area(f["bbox"]) for f in figures if f["origin"] != "region")
    if total < 80 and img_area > 0.5 * pr.width * pr.height:
        return True, "scanned"
    if bad > 0.25:
        return True, f"garbage text layer ({bad:.0%} of chars)"
    return False, "clean"


def extract(pdf: Path, library: Path | None = None, ocr_mode: str = "auto", log=print,
            render_images: bool = True) -> Path:
    pdf = Path(pdf)
    sha = sha256_file(pdf)
    doc_dir = (library or library_dir()) / doc_id_for(pdf, sha)
    for sub in ("pages", "figures"):  # regenerated every run; labels files are left alone
        shutil.rmtree(doc_dir / sub, ignore_errors=True)
        (doc_dir / sub).mkdir(parents=True, exist_ok=True)
    if not (doc_dir / "source.pdf").exists():
        shutil.copy2(pdf, doc_dir / "source.pdf")

    doc = pymupdf.open(pdf)
    pages_out, blocks_out, figures_out = [], [], []
    for pno, page in enumerate(doc, start=1):
        pr = page.rect
        blocks = native_blocks(page)
        figures = find_figures(page, blocks)
        do_ocr, reason = decide_ocr(blocks, figures, pr, ocr_mode)
        fig_ocr: dict[int, list[str]] = {}
        if do_ocr:
            from .ocr import ocr_page

            lines = ocr_page(page)
            keep = [b for b in blocks if textutil.is_trustworthy(b["text"])]
            rest = []
            for line in lines:
                if any(_center_in(line["bbox"], b["bbox"]) for b in keep):
                    continue  # already have this text natively and it's clean
                fi = next((i for i, f in enumerate(figures)
                           if f["origin"] != "region" and _center_in(line["bbox"], f["bbox"], pad=0)), None)
                if fi is not None:
                    fig_ocr.setdefault(fi, []).append(line["text"])  # text inside a picture
                    continue
                rest.append(line)
            blocks = keep + group_ocr_lines(rest)
            blocks.sort(key=lambda b: (round(b["bbox"][1] / 4), b["bbox"][0]))
            figures = find_figures(page, blocks) if not figures else figures

        q, bad, _ = page_quality(blocks)
        img_name = f"pages/p{pno:03d}.png"
        if render_images:
            page.get_pixmap(dpi=PAGE_DPI).save(doc_dir / img_name)

        fig_ids = []
        for fi, f in enumerate(figures, start=1):
            fid = f"p{pno}-f{fi}"
            fname = f"figures/{fid}.png"
            if render_images:
                page.get_pixmap(dpi=FIGURE_DPI, clip=pymupdf.Rect(f["bbox"])).save(doc_dir / fname)
            entry = {"id": fid, "page": pno, "bbox": _r(f["bbox"]), "origin": f["origin"], "image": fname}
            if fig_ocr.get(fi - 1):
                entry["ocr_text"] = " ".join(fig_ocr[fi - 1])
            figures_out.append(entry)
            fig_ids.append((fid, f))

        for bi, b in enumerate(blocks, start=1):
            flags = []
            if b["bbox"][3] <= pr.y0 + MARGIN_FRAC * pr.height:
                flags.append("margin_top")
            if b["bbox"][1] >= pr.y1 - MARGIN_FRAC * pr.height:
                flags.append("margin_bottom")
            if b["quality"] < textutil.GARBAGE_THRESHOLD:
                flags.append("garbage")
            entry = {"id": f"p{pno}-b{bi}", "page": pno, **b, "flags": flags}
            for fid, f in fig_ids:
                if f["origin"] != "raster" and _overlap(b["bbox"], f["bbox"]) > 0.6 * max(_area(b["bbox"]), 1):
                    entry["in_figure"] = fid
                    break
            if not entry["sups"]:
                del entry["sups"]
            blocks_out.append(entry)

        pages_out.append({
            "n": pno, "width": round(pr.width, 1), "height": round(pr.height, 1), "image": img_name,
            "ocr": do_ocr, "ocr_reason": reason, "quality": round(q, 3), "garbage_frac": round(bad, 3),
        })
        log(f"  p{pno:>2}: {len(blocks):>3} blocks, {len(figures)} figures, "
            f"quality {q:.2f}{'  OCR: ' + reason if do_ocr else ''}")

    extract_doc = {
        "schema": "rsvp-extract/1",
        "extractor_version": EXTRACTOR_VERSION,
        "id": doc_dir.name,
        "source": {"file": "source.pdf", "original_name": pdf.name, "sha256": sha, "pages": doc.page_count},
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages_out,
        "blocks": blocks_out,
        "figures": figures_out,
    }
    (doc_dir / "extract.json").write_text(json.dumps(extract_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc_dir
