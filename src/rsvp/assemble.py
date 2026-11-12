"""Assemble extract.json + merged label layers -> doc.json (what the reader loads).

Deterministic: the same extraction and labels always give the same document. Block text
only changes through explicit text_fixes, and any fix that changes digits must say why.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import labels as L
from .textutil import join_lines

SCHEMA = "rsvp-doc/1"
REF = re.compile(r"\b(Exhibits?|Figures?|Fig\.|Tables?|Charts?)\s+(\d+[A-Z]?)\b", re.I)
SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s|$)")
TYPE_FOR_ROLE = {"body": "para", "quote": "quote", "list_item": "list_item"}
DIGITS = re.compile(r"\d")


def _norm_label(kind: str, num: str) -> str:
    k = kind.lower().rstrip("s").rstrip(".")
    k = {"fig": "figure"}.get(k, k)
    return f"{k} {num.upper()}"


def parse_label(text: str) -> tuple[str | None, str]:
    """'Exhibit 4: Store counts' -> ('Exhibit 4', 'Store counts')."""
    m = re.match(r"\s*((?:Exhibit|Figure|Fig\.|Table|Chart)\s+\d+[A-Z]?)\s*[:.\-–—]?\s*(.*)", text, re.I | re.S)
    if not m:
        return None, text
    return m.group(1), m.group(2).strip()


def _apply_fixes(bid: str, text: str, fixes: list[dict], warnings: list[str]) -> str:
    for fx in fixes:
        find, rep = fx["find"], fx["replace"]
        if find not in text:
            warnings.append(f"text fix not applied, {find!r} not found in {bid}")
            continue
        if sorted(DIGITS.findall(find)) != sorted(DIGITS.findall(rep)) and not fx.get("why"):
            warnings.append(f"text fix in {bid} changes digits without a 'why': {find!r} -> {rep!r}")
        text = text.replace(find, rep) if fx.get("all") else text.replace(find, rep, 1)
    return text


def _center_y(bbox) -> float:
    return (bbox[1] + bbox[3]) / 2


def assemble(doc_dir: Path, write: bool = True) -> tuple[dict, list[str], list[str]]:
    """Returns (doc, warnings, errors). doc.json is only written when there are no errors."""
    ex = L.load_extract(doc_dir)
    layers = L.load_layers(doc_dir)
    errors: list[str] = []
    warnings: list[str] = []
    sha = L.extract_sha(doc_dir)
    for name, lab in layers.items():
        errors += L.validate(lab, ex, f"labels.{name}")
        if lab.get("extract_sha") and lab["extract_sha"] != sha:
            warnings.append(f"labels.{name} was made for a different extraction (ids may have shifted)")
    lab = L.merge_layers(layers)

    blocks = {b["id"]: b for b in ex["blocks"]}
    ext_order = [b["id"] for b in ex["blocks"]]
    ext_index = {bid: i for i, bid in enumerate(ext_order)}
    figs = {f["id"]: f for f in ex["figures"]}

    # --- roles ---------------------------------------------------------------------------
    roles: dict[str, str] = {}
    unlabeled = []
    for bid in ext_order:
        entry = lab["blocks"].get(bid)
        if entry and entry.get("role") in L.ROLES:
            roles[bid] = entry["role"]
        else:
            b = blocks[bid]
            margin = any(f.startswith("margin") for f in b["flags"])
            roles[bid] = "boilerplate" if margin or b.get("in_figure") else "body"
            unlabeled.append(bid)
    if unlabeled:
        more = f" (+{len(unlabeled) - 8} more)" if len(unlabeled) > 8 else ""
        warnings.append(f"{len(unlabeled)} unlabeled blocks defaulted by position: {', '.join(unlabeled[:8])}{more}")

    # --- text with fixes -----------------------------------------------------------------
    text = {bid: _apply_fixes(bid, blocks[bid]["text"], lab["text_fixes"].get(bid, []), warnings)
            if bid in lab["text_fixes"] else blocks[bid]["text"] for bid in ext_order}

    # --- merges (paragraphs split across pages, columns, or by a sticky bar) -------------
    head_of: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for group in lab.get("merge") or []:
        group = [g for g in group if g in blocks]
        if len(group) < 2:
            continue
        members[group[0]] = group
        for g in group[1:]:
            head_of[g] = group[0]

    # --- reading sequence ----------------------------------------------------------------
    if lab.get("order"):
        seq = [b for b in lab["order"] if b in blocks]
        listed = set(seq) | set(head_of)
        for bid in ext_order:  # reading/heading blocks the order forgot: place by position
            if bid in listed or roles[bid] not in L.READING_ROLES | {"heading"}:
                continue
            warnings.append(f"{bid} ({roles[bid]}) is not in 'order'; placed by page position")
            pos = next((i for i, s in enumerate(seq) if ext_index[s] > ext_index[bid]), len(seq))
            seq.insert(pos, bid)
    else:
        seq = list(ext_order)

    sections = [{"id": "s0", "title": None, "level": 1, "heading_block": None}]
    body: list[dict] = []
    meta_found: dict[str, str] = {}
    endnotes: list[str] = []
    ext_to_para: dict[str, str] = {}
    sec_of_heading: dict[str, str] = {}
    for bid in seq:
        if bid in head_of:
            continue  # merged into its head
        role = roles[bid]
        group = members.get(bid, [bid])
        if role == "heading":
            sid = f"s{len(sections)}"
            level = (lab["blocks"].get(bid) or {}).get("level", 2)
            sections.append({"id": sid, "title": join_lines([text[g] for g in group]), "level": level,
                             "heading_block": bid})
            sec_of_heading[bid] = sid
        elif role in L.READING_ROLES:
            para = {
                "id": bid,
                "type": TYPE_FOR_ROLE[role],
                "section": sections[-1]["id"],
                "text": join_lines([text[g] for g in group]),
                "src": [{"page": blocks[g]["page"], "bbox": blocks[g]["bbox"], "ext": g} for g in group],
            }
            body.append(para)
            for g in group:
                ext_to_para[g] = bid
        elif role in ("title", "subtitle", "byline", "dateline"):
            piece = join_lines([text[g] for g in group])
            meta_found[role] = f"{meta_found[role]} {piece}" if role in meta_found else piece
        elif role == "endnote":
            endnotes.append(text[bid])

    # --- figures -------------------------------------------------------------------------
    fig_entries = {fid: dict(lab["figures"].get(fid, {})) for fid in figs}
    default_kind = {"raster": "photo", "vector": "chart", "region": "table", "mixed": "photo"}
    for fid, e in fig_entries.items():
        e.setdefault("kind", default_kind.get(figs[fid]["origin"], "photo"))
    visible = [fid for fid in figs if fig_entries[fid]["kind"] not in L.HIDDEN_FIGURE_KINDS]
    group_head = {}
    for fid in visible:
        g = fig_entries[fid].get("group")
        group_head[fid] = g if g and g in visible and g != fid else fid
    caption_parts: dict[str, list[str]] = {}
    credit_parts: dict[str, list[str]] = {}
    for bid in ext_order:
        entry = lab["blocks"].get(bid) or {}
        fid = entry.get("figure")
        if not fid or fid not in group_head:
            continue
        target = caption_parts if roles[bid] == "caption" else credit_parts if roles[bid] == "credit" else None
        if target is not None:
            target.setdefault(group_head[fid], []).append(text[bid])
    fig_text: dict[str, list[str]] = {}
    for b in ex["blocks"]:
        if b.get("in_figure") in group_head and roles[b["id"]] == "figure_text":
            fig_text.setdefault(group_head[b["in_figure"]], []).append(text[b["id"]])

    para_pos = {p["id"]: i for i, p in enumerate(body)}
    last_body_page = max((s["page"] for p in body for s in p["src"]), default=0)
    figures_out = []
    for fid in visible:
        if group_head[fid] != fid:
            continue
        e = fig_entries[fid]
        f = figs[fid]
        images = [figs[g]["image"] for g in visible if group_head[g] == fid]
        caption = " ".join(caption_parts.get(fid, [])) or None
        label = e.get("label")
        if caption:
            parsed, rest = parse_label(caption)
            if parsed:
                label = label or parsed
                caption = rest or None
        if e.get("caption"):
            caption = e["caption"]
        credit = e.get("credit") or " ".join(credit_parts.get(fid, [])) or None
        anchor, placement, refs = _anchor(fid, f, e, label, body, para_pos, ext_to_para, last_body_page, warnings)
        figures_out.append({
            "id": fid,
            "kind": e["kind"],
            "label": label,
            "title": e.get("title"),
            "caption": caption,
            "credit": credit,
            "images": images,
            "page": f["page"],
            "anchor": anchor,
            "placement": placement,
            "refs": refs,
            "alt_text": e.get("alt_text") or " ".join(fig_text.get(fid, [])) or f.get("ocr_text") or None,
        })
    figures_out.sort(key=lambda fo: (para_pos.get((fo["anchor"] or {}).get("block"), 10**9), fo["page"]))

    # --- recaps --------------------------------------------------------------------------
    recaps = []
    for r in lab.get("recaps") or []:
        h = r.get("heading")
        sid = "s0" if h is None else sec_of_heading.get(h)
        if sid is None:
            warnings.append(f"recap refers to {h!r}, which isn't a heading in the document")
            continue
        recaps.append({"section": sid, "summary": r.get("summary"), "check": r.get("check")})

    # --- sanity warnings -----------------------------------------------------------------
    counts = Counter(p["text"].strip().lower() for p in body if len(p["text"]) < 200)
    for t, n in counts.items():
        if n > 1:
            warnings.append(f"repeated paragraph x{n} (leaked running head/ad?): {t[:60]!r}")
    for p in body:
        for s in p["src"]:
            flags = blocks[s["ext"]]["flags"]
            if "garbage" in flags:
                warnings.append(f"{s['ext']} is body text but looks like garbage")
            # Long or merged paragraphs often just start/end at the page edge; a short
            # stand-alone fragment there is more likely a leaked running head.
            if any(fl.startswith("margin") for fl in flags) and len(p["src"]) == 1 and len(p["text"]) < 80:
                warnings.append(f"{s['ext']} is a short body fragment in the page margin (running head?)")

    meta = {k: lab["meta"].get(k) or meta_found.get(k) for k in ("title", "subtitle", "byline", "dateline")}
    meta["title"] = meta["title"] or ex["source"]["original_name"].rsplit(".", 1)[0]
    doc = {
        "schema": SCHEMA,
        "id": ex["id"],
        **meta,
        "source": {k: ex["source"][k] for k in ("file", "original_name", "sha256", "pages")},
        "ingest": {
            "method": "+".join(n for n in L.LAYERS if n in layers and L.has_content(layers[n])) or "none",
            "ocr_pages": [p["n"] for p in ex["pages"] if p["ocr"]],
            "warnings": warnings,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "extract_sha": sha,
        },
        "sections": sections,
        "body": body,
        "figures": figures_out,
        "endnotes": endnotes,
        "recaps": recaps,
        "stats": {"words": sum(len(p["text"].split()) for p in body), "paragraphs": len(body)},
    }
    if write and not errors:
        (doc_dir / "doc.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc, warnings, errors


def _anchor(fid, f, e, label, body, para_pos, ext_to_para, last_body_page, warnings):
    """Returns (anchor | None, placement, refs). See SPEC 'Figure anchoring'."""
    refs = []
    want = None
    if label:
        m = REF.match(label)
        want = _norm_label(m.group(1), m.group(2)) if m else None
    first = None
    if want:
        for p in body:
            for m in REF.finditer(p["text"]):
                if _norm_label(m.group(1), m.group(2)) == want:
                    if first is None:
                        first = (p, m)
                    else:
                        refs.append({"block": p["id"], "char": m.start()})
    a = e.get("anchor") or {}
    if a.get("end"):
        return None, "end", refs
    if a.get("block"):
        pid = ext_to_para.get(a["block"])
        if pid:
            ptext = next(p["text"] for p in body if p["id"] == pid)
            return {"block": pid, "char": a.get("char", len(ptext)), "method": "manual"}, "inline", refs
        warnings.append(f"figure {fid}: anchor block {a['block']} isn't body text; anchoring automatically")
    if first:
        p, m = first
        end = SENTENCE_END.search(p["text"], m.end())
        return {"block": p["id"], "char": end.end() if end else len(p["text"]), "method": "reference"}, "inline", refs
    if label and f["page"] > last_body_page:
        return None, "end", refs  # exhibit at the back that the text never cites
    # Proximity: the paragraph beside the figure, else the last one before it.
    same_page = [p for p in body if any(s["page"] == f["page"] for s in p["src"])]
    cy = _center_y(f["bbox"])
    beside = [p for p in same_page if any(s["page"] == f["page"] and s["bbox"][1] <= cy <= s["bbox"][3] for s in p["src"])]
    if beside:
        p = beside[0]
    else:
        before = [p for p in body if any(s["page"] < f["page"] or (s["page"] == f["page"] and s["bbox"][3] <= f["bbox"][1])
                                         for s in p["src"])]
        p = before[-1] if before else (body[0] if body else None)
    if p is None:
        return None, "end", refs
    return {"block": p["id"], "char": len(p["text"]), "method": "proximity"}, "inline", refs
