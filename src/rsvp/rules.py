"""Basic rules labeler -> labels.rules.json.

Deliberately simple (SPEC: "basic in v1"). It handles the generic cases: running heads and
footers, page numbers, browser print chrome, common boilerplate phrases, headings, captions
next to figures, exhibit titles and sources, endnote sections, and paragraphs split across
page breaks. Publisher-specific junk is /ingest's job.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from . import labels as L

BOILERPLATE_PATTERNS = [re.compile(p, re.I) for p in (
    r"^\d{1,3}$",                                   # page number
    r"^(page\s+)?\d+\s*(/|of)\s*\d+$",              # 3/12, page 3 of 12
    r"^https?://\S+(\s+\d+/\d+)?$",                 # browser print URL footer
    r"^\d{1,2}/\d{1,2}/\d{2,4}\s+\S",               # browser print date header
    r"^(advertisement|advertising|sponsored|sponsored content)$",
    r"\|\s*sponsored$",
    r"^\[most read\]",
    r"^this copy is for your personal, non-commercial use",
    r"^this document is authorized for use",
    r"^any unauthorized use or reproduction",
    r"^(©|copyright ©|\(c\) )",
    r"^subscribers, get",
    r"^sign up for",
    r"^(play now|sections|share|tap to unmute|enter your email address)$",
    r"^appeared in the .{0,40}print edition",
    r"^continue reading",
    r"^(eat\. watch\. do\.|twice-weekly)",
    r"^thank you for supporting our journalism",
    # newsletter sign-ups and "latest news" widgets
    r"get it sent to your inbox|sent by email each|sign up for (our|the) .{0,40}newsletter",
    r"^(daily business (updates|briefing)|latest updates ›?|is this helpful\??|updated \d+ (minutes|hours) ago)$",
)]
END_MARKERS = [re.compile(p, re.I) for p in (
    r"^[\w.+-]+@[\w-]+\.[\w.]+$",                   # reporter's email sign-off
    r"^you may like",
    r"sponsored links by",
    r"^(recommended|related coverage|most popular|more from)\b",
)]
ENDNOTES_HEADING = re.compile(r"^(endnotes|notes|references)$", re.I)
EXHIBIT_TITLE = re.compile(r"^\s*(Exhibit|Figure|Fig\.|Table|Chart)\s+\d+[A-Z]?\b", re.I)
SOURCE_LINE = re.compile(r"^\s*(Sources?|Notes?)\s*:", re.I)
DATELINE = re.compile(
    r"^(published|updated)\b|^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}",
    re.I)
SENT_END = re.compile(r"[.!?:…][\"'”’)\]]*$")
TICKER = re.compile(r"(?<=[a-z])\s?([A-Z]{1,5}\s?[-+−]\d+\.\d+%\s?[▼▲]?)")
ALPHA3 = re.compile(r"[A-Za-z]{3,}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _body_size(blocks: list[dict]) -> float:
    sizes: Counter = Counter()
    for b in blocks:
        if not b["flags"] and len(b["text"]) > 150 and not b.get("in_figure"):
            sizes[b["font"]["size"]] += len(b["text"])
    if not sizes:
        return statistics.median([b["font"]["size"] for b in blocks]) if blocks else 10.0
    return sizes.most_common(1)[0][0]


def _below(fig_bbox, b_bbox, max_gap: float) -> bool:
    gap = b_bbox[1] - fig_bbox[3]
    overlap = min(fig_bbox[2], b_bbox[2]) - max(fig_bbox[0], b_bbox[0])
    return -15 <= gap <= max_gap and overlap > 0  # image boxes often overlap their caption a bit


def _family(name: str) -> str:
    name = re.split(r"[-,]", name.split("+")[-1].lower())[0]
    return re.sub(r"(ps|mt|bold|italic|regular|oblique|light|medium)+$", "", name)


def _body_family(blocks: list[dict], body_size: float) -> str:
    fam: Counter = Counter()
    for b in blocks:
        if abs(b["font"]["size"] - body_size) < 0.3 and len(b["text"]) > 150:
            fam[_family(b["font"]["name"])] += len(b["text"])
    return fam.most_common(1)[0][0] if fam else ""


def _is_text_widget(fig: dict, blocks: list[dict]) -> bool:
    """A drawn box whose text is sentences/headlines rather than numbers and short labels.

    Charts and tables are mostly numbers, axis ticks and short labels. A "Latest Updates" or
    newsletter box drawn with a border is several sentence-like lines with almost no numbers.
    """
    inner = [b for b in blocks if b.get("in_figure") == fig["id"]]
    words = [w for b in inner for w in b["text"].split()]
    if len(words) < 12:
        return False
    numeric = sum(1 for w in words if re.search(r"\d", w)) / len(words)
    sentence_lines = sum(1 for b in inner if len(b["text"].split()) >= 6)
    junk = sum(1 for b in inner if any(p.search(b["text"].strip()) for p in BOILERPLATE_PATTERNS))
    return numeric < 0.15 and (sentence_lines >= 2 or junk >= 2)


def _looks_like_chart(png: Path) -> bool:
    """Raster charts are mostly white with few distinct colors; photos are neither."""
    try:
        import numpy as np
        from PIL import Image

        im = Image.open(png).convert("RGB").resize((120, 120))
        a = np.asarray(im)
    except Exception:
        return False
    white = (a.min(axis=2) > 230).mean()
    distinct = len(np.unique((a // 32).reshape(-1, 3), axis=0))
    return white > 0.45 and distinct < 60


def label_rules(doc_dir: Path) -> dict:
    ex = L.load_extract(doc_dir)
    blocks = ex["blocks"]
    figs = ex["figures"]
    n_pages = len(ex["pages"])
    body_size = _body_size(blocks)
    lab = L.new_labels(doc_dir, "rules")
    role: dict[str, str] = {}
    reason: dict[str, str] = {}

    def set_role(bid, r, why=None, **extra):
        if bid in role:
            return
        role[bid] = r
        lab["blocks"][bid] = {"role": r, **extra}
        if why:
            lab["blocks"][bid]["reason"] = why
            reason[bid] = why

    # 1. Repeated elements: same normalized text on many pages (running heads, sticky bars).
    pages_with: dict[str, set] = defaultdict(set)
    for b in blocks:
        if len(b["text"]) < 200:
            pages_with[_norm(b["text"])].add(b["page"])
    repeat_min = max(2, round(0.4 * n_pages)) if n_pages > 2 else 99
    sizes_of: dict[str, list] = defaultdict(list)
    for b in blocks:
        sizes_of[_norm(b["text"])].append(b["font"]["size"])
    for b in blocks:
        key = _norm(b["text"])
        if len(b["text"]) < 200 and len(pages_with[key]) >= repeat_min:
            # A case's title also appears in every running head, but in a much bigger font.
            if b["font"]["size"] > 1.5 * statistics.median(sizes_of[key]):
                continue
            set_role(b["id"], "boilerplate", "repeated on many pages")

    # 2. Known boilerplate phrases, then everything after an end-of-article marker.
    ended = False
    for b in blocks:
        t = b["text"].strip()
        if ended:
            set_role(b["id"], "boilerplate", "after end of article")
            continue
        if any(p.search(t) for p in BOILERPLATE_PATTERNS):
            set_role(b["id"], "boilerplate", "boilerplate phrase")
        elif any(p.search(t) for p in END_MARKERS) and b["page"] > 1:
            set_role(b["id"], "boilerplate", "end of article")
            ended = True

    # 3. Text inside charts/tables.
    for b in blocks:
        if b.get("in_figure"):
            set_role(b["id"], "figure_text", figure=b["in_figure"])

    # 4. Endnotes section: the heading and everything after it.
    in_notes = False
    for b in blocks:
        if not in_notes and ENDNOTES_HEADING.match(b["text"].strip()) and b["font"]["size"] >= body_size:
            in_notes = True
        if in_notes and b["id"] not in role and not any(f.startswith("margin") for f in b["flags"]):
            set_role(b["id"], "endnote")

    # 5. Exhibit/figure titles, their sources, and grouping of multi-image exhibits.
    figs_by_page: dict[int, list] = defaultdict(list)
    for f in figs:
        figs_by_page[f["page"]].append(f)
    for page, pfigs in figs_by_page.items():
        pblocks = [b for b in blocks if b["page"] == page and b["id"] not in role]
        titles = [b for b in pblocks if EXHIBIT_TITLE.match(b["text"]) and len(b["text"]) < 200]
        owner: dict[str, dict] = {}  # figure id -> nearest exhibit title above it
        for f in sorted(pfigs, key=lambda f: f["bbox"][1]):
            above = [t for t in titles if t["bbox"][1] < f["bbox"][1]]
            if above:
                owner[f["id"]] = max(above, key=lambda t: t["bbox"][1])
        heads: dict[str, str] = {}
        for f in sorted(pfigs, key=lambda f: f["bbox"][1]):
            t = owner.get(f["id"])
            if not t:
                continue
            if t["id"] in heads:
                lab["figures"].setdefault(f["id"], {})["group"] = heads[t["id"]]
            else:
                heads[t["id"]] = f["id"]
                set_role(t["id"], "caption", figure=f["id"])
                lab["figures"].setdefault(f["id"], {})["kind"] = "exhibit"
        for b in pblocks:
            if SOURCE_LINE.match(b["text"]) and b["id"] not in role:
                above_titles = [t for t in titles if t["id"] in heads and t["bbox"][1] <= b["bbox"][1]]
                if above_titles:
                    t = max(above_titles, key=lambda t: t["bbox"][1])
                    set_role(b["id"], "credit", figure=heads[t["id"]])

    # 6. Figure kinds, side-by-side photo groups, and captions directly under a figure.
    page_area = {p["n"]: p["width"] * p["height"] for p in ex["pages"]}
    for f in figs:
        e = lab["figures"].setdefault(f["id"], {})
        w, h = f["bbox"][2] - f["bbox"][0], f["bbox"][3] - f["bbox"][1]
        near_ad = any(re.match(r"^advertis", b["text"], re.I) and b["page"] == f["page"]
                      and -30 <= f["bbox"][1] - b["bbox"][3] <= 30 for b in blocks)
        if "kind" not in e:
            if near_ad:
                e["kind"] = "ad"
            elif (w * h < 0.03 * page_area[f["page"]] and h < 70) or \
                    (f["origin"] == "raster" and (w < 120 or h < 60 or w / max(h, 1) > 5)):
                e["kind"] = "decorative"  # logos, icons, banner strips, shadows
            elif f["origin"] == "vector" and _is_text_widget(f, blocks):
                e["kind"] = "ad"  # an outlined box of headlines/links ("Latest Updates"), not a chart
            elif f["origin"] == "vector":
                e["kind"] = "chart"
            elif f["origin"] == "region":
                e["kind"] = "table"
            elif re.search(r"unmute|play video|watch", f.get("ocr_text", ""), re.I):
                e["kind"] = "photo"  # a video still; often mostly blank, so not a chart
            elif _looks_like_chart(doc_dir / f["image"]):
                e["kind"] = "chart"
            else:
                e["kind"] = "photo"

    # Photos side by side (similar vertical span) usually share one caption: group them.
    for page, pfigs in figs_by_page.items():
        photos = sorted([f for f in pfigs if lab["figures"][f["id"]]["kind"] == "photo"
                         and "group" not in lab["figures"][f["id"]]], key=lambda f: f["bbox"][0])
        for i, a in enumerate(photos):
            if "group" in lab["figures"][a["id"]]:
                continue
            for b in photos[i + 1:]:
                ov = min(a["bbox"][3], b["bbox"][3]) - max(a["bbox"][1], b["bbox"][1])
                if ov > 0.7 * min(a["bbox"][3] - a["bbox"][1], b["bbox"][3] - b["bbox"][1]):
                    lab["figures"][b["id"]]["group"] = a["id"]

    for f in figs:
        e = lab["figures"][f["id"]]
        if e["kind"] in L.HIDDEN_FIGURE_KINDS:
            continue
        head = e.get("group", f["id"])
        for b in blocks:
            if b["page"] != f["page"] or b["id"] in role or len(b["text"]) >= 500:
                continue
            small = b["font"]["size"] < 0.92 * body_size and _below(f["bbox"], b["bbox"], 40)
            # Right under the picture and left-aligned with it, even in body-size type.
            tight = len(b["text"]) < 250 and abs(b["bbox"][0] - f["bbox"][0]) < 15 and _below(f["bbox"], b["bbox"], 12)
            if small or tight:
                set_role(b["id"], "caption", figure=head)

    # 7. First-page metadata: title (largest font), subtitle, byline, dateline, kickers.
    p1 = [b for b in blocks if b["page"] <= 2 and b["id"] not in role and ALPHA3.search(b["text"])]
    title = max(p1, key=lambda b: b["font"]["size"], default=None) if p1 else None
    if title and title["font"]["size"] >= 1.3 * body_size:
        set_role(title["id"], "title")
        lab["meta"]["title"] = title["text"]
        after = [b for b in p1 if b["page"] == title["page"] and b["bbox"][1] > title["bbox"][1]]
        if after:
            a, f = after[0], after[0]["font"]
            styled = f["bold"] or f["italic"] or f["size"] >= 1.05 * body_size
            short_ocr = a["source"] == "ocr" and len(a["text"]) < 150
            if len(a["text"]) < 300 and (styled or short_ocr) and not a["text"].lower().startswith("by ") \
                    and a["bbox"][1] - title["bbox"][3] < 40:
                set_role(a["id"], "subtitle")
                for nxt in after[1:]:  # a centred subtitle can wrap into several blocks
                    if nxt["font"] == f and nxt["bbox"][1] - a["bbox"][3] < 6:
                        set_role(nxt["id"], "subtitle")
                        a = nxt
                    else:
                        break
        for b in p1:
            if b["page"] == title["page"] and b["bbox"][3] < title["bbox"][1] and len(b["text"]) < 80 \
                    and b["text"].upper() == b["text"]:
                set_role(b["id"], "boilerplate", "kicker")
    for b in p1:
        t = b["text"].strip()
        if b["id"] in role or len(t) > 200:
            continue
        if re.match(r"^by\s+[A-Z]", t, re.I):
            set_role(b["id"], "byline")
        elif DATELINE.search(t) and len(t) < 80:
            set_role(b["id"], "dateline")

    # 8. Headings, quotes, orphan captions, body. Small text in the page margins is
    #    boilerplate; body-size text there is just a paragraph that runs low on the page.
    body_family = _body_family(blocks, body_size)
    for b in blocks:
        if b["id"] in role:
            continue
        t, f = b["text"].strip(), b["font"]
        # Headings: short, start with a capital (or digit), no sentence break inside or at the end.
        headingish = (len(t) < 90 and ALPHA3.search(t) and not SENT_END.search(t)
                      and not t[:1].islower() and not re.search(r"[.!?]\s+[A-Z]", t))
        other_face = _family(f["name"]) != body_family and f["name"] != "ocr"
        ocr_heading = (b["source"] == "ocr" and b["lines"] == 1 and len(t) < 50 and t[:1].isupper()
                       and abs(f["size"] - body_size) < 0.15 * body_size)
        if any(fl.startswith("margin") for fl in b["flags"]) and f["size"] < 0.92 * body_size:
            set_role(b["id"], "boilerplate", "page margin")
        elif headingish and (f["size"] >= 1.12 * body_size or (f["bold"] and f["size"] >= 0.95 * body_size)):
            set_role(b["id"], "heading", level=2)
        elif headingish and (ocr_heading or (other_face and f["size"] >= 0.95 * body_size)):
            set_role(b["id"], "heading", level=3 if not ocr_heading else 2)
        elif f["size"] < 0.92 * body_size:
            if f["italic"] and len(t) > 100:
                set_role(b["id"], "quote")
            else:
                set_role(b["id"], "caption", "small text, likely an orphan caption")
        else:
            set_role(b["id"], "body")

    # 9. Paragraphs split across page breaks or around ads/sticky bars.
    reading = [b for b in blocks if role.get(b["id"]) in L.READING_ROLES]
    merges, cur = [], []
    for prev, nxt in zip(reading, reading[1:]):
        # A paragraph that doesn't end a sentence continues in the next reading block
        # (next page, other side of a caption, after an ad or sticky bar).
        continues = not SENT_END.search(prev["text"].rstrip())
        if continues and role[prev["id"]] == role[nxt["id"]]:
            if not cur:
                cur = [prev["id"]]
            cur.append(nxt["id"])
        elif cur:
            merges.append(cur)
            cur = []
    if cur:
        merges.append(cur)
    if merges:
        lab["merge"] = merges

    # 10. OCR'd stock-ticker chips glued into the text ("MondelezMDLZ -0.08%").
    for b in blocks:
        if role.get(b["id"]) in L.READING_ROLES:
            for m in TICKER.finditer(b["text"]):
                lab["text_fixes"].setdefault(b["id"], []).append(
                    {"find": m.group(0), "replace": " " if m.group(0).endswith(" ") else "",
                     "why": "stock ticker chip"})

    return lab


def write_rules(doc_dir: Path) -> Path:
    return L.write_layer(doc_dir, "rules", label_rules(doc_dir))
