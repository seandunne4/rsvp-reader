"""Label layers: load, validate, and merge (rules < claude < user).

Every labeler (rules, /ingest in Claude Code, the review screen) writes the same format,
keyed by extract block/figure ids. Labels never contain retyped text, only roles, order,
merges, figure info, and small find/replace fixes.

Merge semantics:
  - dict sections (meta, blocks, text_fixes) merge per key; the higher layer's entry wins.
  - figures merge per figure *and per field*, so the review screen can change just an
    anchor without losing /ingest's kind/label.
  - list sections (order, merge, recaps) are replaced wholesale by the highest layer that
    has them, since partial lists don't compose meaningfully.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "rsvp-labels/1"
LAYERS = ("rules", "claude", "user")  # lowest to highest precedence

ROLES = {
    "title", "subtitle", "byline", "dateline", "heading", "body", "quote", "list_item",
    "caption", "credit", "endnote", "figure_text", "boilerplate",
}
READING_ROLES = {"body", "quote", "list_item"}  # these become RSVP'd paragraphs
FIGURE_KINDS = {"photo", "chart", "table", "exhibit", "diagram", "decorative", "ad"}
HIDDEN_FIGURE_KINDS = {"decorative", "ad"}


def layer_path(doc_dir: Path, layer: str) -> Path:
    return doc_dir / f"labels.{layer}.json"


def extract_sha(doc_dir: Path) -> str:
    return hashlib.sha256((doc_dir / "extract.json").read_bytes()).hexdigest()[:16]


def load_extract(doc_dir: Path) -> dict:
    return json.loads((doc_dir / "extract.json").read_text(encoding="utf-8"))


def load_layers(doc_dir: Path) -> dict[str, dict]:
    layers = {}
    for name in LAYERS:
        p = layer_path(doc_dir, name)
        if p.exists():
            # utf-8-sig: tolerate a BOM (Windows PowerShell 5 adds one when saving UTF-8).
            layers[name] = json.loads(p.read_text(encoding="utf-8-sig"))
    return layers


def merge_layers(layers: dict[str, dict]) -> dict:
    merged: dict = {"meta": {}, "blocks": {}, "text_fixes": {}, "figures": {}}
    for name in LAYERS:
        lab = layers.get(name)
        if not lab:
            continue
        for key in ("meta", "blocks", "text_fixes"):
            merged[key].update(lab.get(key) or {})
        for fid, entry in (lab.get("figures") or {}).items():
            merged["figures"].setdefault(fid, {}).update(entry)
        for key in ("order", "merge", "recaps"):
            if key in lab:
                merged[key] = lab[key]
    return merged


def validate(labels: dict, extract: dict, name: str = "labels") -> list[str]:
    """Return a list of errors (empty if valid)."""
    errors = []
    block_ids = {b["id"] for b in extract["blocks"]}
    fig_ids = {f["id"] for f in extract["figures"]}
    if labels.get("schema", SCHEMA) != SCHEMA:
        errors.append(f"{name}: schema must be {SCHEMA!r}")
    for bid, entry in (labels.get("blocks") or {}).items():
        if bid not in block_ids:
            errors.append(f"{name}: unknown block {bid!r}")
        role = entry.get("role")
        if role not in ROLES:
            errors.append(f"{name}: block {bid}: unknown role {role!r}")
        if entry.get("figure") and entry["figure"] not in fig_ids:
            errors.append(f"{name}: block {bid}: unknown figure {entry['figure']!r}")
    for fid, entry in (labels.get("figures") or {}).items():
        if fid not in fig_ids:
            errors.append(f"{name}: unknown figure {fid!r}")
        if "kind" in entry and entry["kind"] not in FIGURE_KINDS:
            errors.append(f"{name}: figure {fid}: unknown kind {entry['kind']!r}")
        anchor = entry.get("anchor")
        if anchor and anchor.get("block") and anchor["block"] not in block_ids:
            errors.append(f"{name}: figure {fid}: anchor to unknown block {anchor['block']!r}")
        if entry.get("group") and entry["group"] not in fig_ids:
            errors.append(f"{name}: figure {fid}: unknown group {entry['group']!r}")
    order = labels.get("order") or []
    for bid in order:
        if bid not in block_ids:
            errors.append(f"{name}: order lists unknown block {bid!r}")
    if len(order) != len(set(order)):
        errors.append(f"{name}: order has duplicates")
    seen: set[str] = set()
    for group in labels.get("merge") or []:
        for bid in group:
            if bid not in block_ids:
                errors.append(f"{name}: merge lists unknown block {bid!r}")
            if bid in seen:
                errors.append(f"{name}: block {bid!r} is in more than one merge group")
            seen.add(bid)
    for bid, fixes in (labels.get("text_fixes") or {}).items():
        if bid not in block_ids:
            errors.append(f"{name}: text_fixes for unknown block {bid!r}")
        for fx in fixes:
            if "find" not in fx or "replace" not in fx:
                errors.append(f"{name}: text_fixes {bid}: each fix needs 'find' and 'replace'")
    return errors


def new_labels(doc_dir: Path, by: str) -> dict:
    return {"schema": SCHEMA, "extract_sha": extract_sha(doc_dir), "by": by,
            "meta": {}, "blocks": {}, "text_fixes": {}, "figures": {}}


def write_layer(doc_dir: Path, layer: str, labels: dict) -> Path:
    p = layer_path(doc_dir, layer)
    p.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
