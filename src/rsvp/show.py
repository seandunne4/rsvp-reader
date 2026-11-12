"""Compact, greppable listing of an extract.json, for /ingest and debugging."""

from __future__ import annotations

import json
from pathlib import Path


def show_doc(doc_dir: Path, width: int = 140) -> str:
    """Plain-text rendering of doc.json: what the reader will present, in order."""
    doc = json.loads((doc_dir / "doc.json").read_text(encoding="utf-8"))
    cut = (lambda s: s if not width or len(s) <= width else s[:width] + "…")
    out = [f"TITLE    {doc['title']}"]
    for k in ("subtitle", "byline", "dateline"):
        if doc.get(k):
            out.append(f"{k.upper():<8} {cut(doc[k])}")
    out.append(f"INGEST   {doc['ingest']['method']}, {doc['stats']['words']} words, "
               f"{len(doc['ingest']['warnings'])} warnings")
    figs_at: dict[str, list] = {}
    for f in doc["figures"]:
        if f["anchor"]:
            figs_at.setdefault(f["anchor"]["block"], []).append(f)
    sec_title = {s["id"]: s["title"] for s in doc["sections"]}
    cur = "s0"
    for p in doc["body"]:
        if p["section"] != cur:
            cur = p["section"]
            out.append(f"\n## {sec_title[cur]}")
        tag = {"para": "", "quote": "> ", "list_item": "- "}[p["type"]]
        out.append(f"[{p['id']}] {tag}{cut(p['text'])}")
        for f in figs_at.get(p["id"], []):
            out.append(f"    ▣ {f['kind'].upper()} {f['label'] or f['id']} ({f['anchor']['method']}, "
                       f"{len(f['images'])} img): {cut(f['caption'] or '')}")
    end = [f for f in doc["figures"] if not f["anchor"]]
    if end:
        out.append("\n## Remaining exhibits")
        for f in end:
            out.append(f"    ▣ {f['kind'].upper()} {f['label'] or f['id']} ({len(f['images'])} img): {cut(f['caption'] or '')}")
    if doc.get("endnotes"):
        out.append(f"\n({len(doc['endnotes'])} endnote blocks not shown)")
    return "\n".join(out)


def show_blocks(doc_dir: Path, pages: set[int] | None = None, width: int = 100, full: bool = False) -> str:
    ex = json.loads((doc_dir / "extract.json").read_text(encoding="utf-8"))
    out = [f"# {ex['id']}  ({ex['source']['original_name']}, {ex['source']['pages']} pages)"]
    figs_by_page: dict[int, list] = {}
    for f in ex["figures"]:
        figs_by_page.setdefault(f["page"], []).append(f)
    for pg in ex["pages"]:
        n = pg["n"]
        if pages and n not in pages:
            continue
        ocr = f"  OCR({pg['ocr_reason']})" if pg["ocr"] else ""
        out.append(f"\n## page {n}  {pg['width']:.0f}x{pg['height']:.0f}  q={pg['quality']:.2f}{ocr}  image={pg['image']}")
        for f in figs_by_page.get(n, []):
            b = f["bbox"]
            extra = f"  ocr_text={f['ocr_text'][:60]!r}" if f.get("ocr_text") else ""
            out.append(f"  [{f['id']}] FIGURE {f['origin']} bbox=({b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f}){extra}")
        for b in ex["blocks"]:
            if b["page"] != n:
                continue
            x0, y0, x1, y1 = b["bbox"]
            f = b["font"]
            style = ("B" if f["bold"] else "") + ("I" if f["italic"] else "")
            flags = list(b["flags"]) + ([f"in:{b['in_figure']}"] if b.get("in_figure") else [])
            if b["source"] == "ocr":
                flags.append("ocr")
            if b.get("sups"):
                flags.append("sup:" + ",".join(s["text"] for s in b["sups"]))
            text = b["text"] if full else (b["text"][:width] + ("…" if len(b["text"]) > width else ""))
            out.append(
                f"  {b['id']:<8} ({x0:3.0f},{y0:3.0f},{x1:3.0f},{y1:3.0f}) {f['size']:>4}{style:<2} "
                f"L{b['lines']:<2} q{b['quality']:.2f} {' '.join(flags)}\n           {text}"
            )
    return "\n".join(out)
