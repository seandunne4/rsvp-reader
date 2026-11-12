"""Score an assembled doc.json against a hand-checked golden doc.json.

Metrics (SPEC "Testing ingestion quality"):
  text      word-level recall (nothing missing) and precision (nothing extra), punctuation-blind
  sentences golden sentences missing from the output / output sentences not in the golden (leaks)
  order     Kendall tau over paragraphs found in both (1.0 = same reading order)
  headings  recall / precision of section titles
  figures   kind agreement, and anchor within one paragraph of the golden's
  ocr       pages OCR'd match the golden's
  title     exact (normalized) match
"""

from __future__ import annotations

import json
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

WORD = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")


def _words(text: str) -> list[str]:
    return WORD.findall(text.lower().replace("’", "'"))


def _norm(text: str) -> str:
    return " ".join(_words(text))


def _sentences(doc: dict) -> list[str]:
    out = []
    for p in doc["body"]:
        for s in re.split(r"(?<=[.!?…])[\"'”’)]?\s+(?=[\"“‘(A-Z0-9])", p["text"]):
            n = _norm(s)
            if len(n.split()) >= 3:
                out.append(n)
    return out


def _kendall_tau(pos: list[int]) -> float:
    n = len(pos)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for a in range(n):
        for b in range(a + 1, n):
            if pos[a] < pos[b]:
                concordant += 1
            elif pos[a] > pos[b]:
                discordant += 1
    return (concordant - discordant) / (n * (n - 1) / 2)


def score(out: dict, gold: dict) -> dict:
    # --- text --------------------------------------------------------------------------
    gw = [w for p in gold["body"] for w in _words(p["text"])]
    ow = [w for p in out["body"] for w in _words(p["text"])]
    matched = sum(b.size for b in SequenceMatcher(None, gw, ow, autojunk=False).get_matching_blocks())
    text_recall = matched / len(gw) if gw else 1.0
    text_precision = matched / len(ow) if ow else 1.0

    # --- sentences ---------------------------------------------------------------------
    gs, os_ = _sentences(gold), _sentences(out)
    gset, oset = set(gs), set(os_)
    missing = [s for s in gs if s not in oset]
    leaked = [s for s in os_ if s not in gset]

    # --- reading order -----------------------------------------------------------------
    key = lambda p: " ".join(_words(p["text"])[:12])
    opos = {}
    for i, p in enumerate(out["body"]):
        opos.setdefault(key(p), i)
    positions = [opos[key(p)] for p in gold["body"] if key(p) in opos]
    tau = _kendall_tau(positions)

    # --- headings ----------------------------------------------------------------------
    gh = {_norm(s["title"]) for s in gold["sections"] if s.get("title")}
    oh = {_norm(s["title"]) for s in out["sections"] if s.get("title")}
    head_recall = len(gh & oh) / len(gh) if gh else 1.0
    head_precision = len(gh & oh) / len(oh) if oh else 1.0

    # --- figures -----------------------------------------------------------------------
    gpara = {p["id"]: i for i, p in enumerate(gold["body"])}
    opara = {p["id"]: i for i, p in enumerate(out["body"])}
    for p in out["body"]:  # a merged paragraph answers for each of its source blocks
        for s in p.get("src", []):
            opara.setdefault(s["ext"], opara[p["id"]])
    ofig = {}
    for f in out["figures"]:
        for img in f["images"]:
            ofig[img] = f
    kinds_ok = anchors_ok = 0
    fig_problems = []
    for f in gold["figures"]:
        o = ofig.get(f["images"][0])
        name = f.get("label") or f["id"]
        if not o:
            fig_problems.append(f"{name}: missing")
            continue
        if o["kind"] == f["kind"]:
            kinds_ok += 1
        else:
            fig_problems.append(f"{name}: kind {o['kind']} (golden {f['kind']})")
        ga, oa = f.get("anchor"), o.get("anchor")
        if not ga and not oa:
            anchors_ok += 1
        elif ga and oa and ga["block"] in gpara and oa["block"] in opara \
                and abs(gpara[ga["block"]] - opara[oa["block"]]) <= 1:
            anchors_ok += 1
        else:
            fig_problems.append(f"{name}: anchored {oa['block'] if oa else 'at end'} "
                                f"(golden {ga['block'] if ga else 'at end'})")
    nf = len(gold["figures"])
    extra_figs = [f.get("label") or f["id"] for f in out["figures"]
                  if not any(img in {i for g in gold["figures"] for i in g["images"]} for img in f["images"])]

    return {
        "text_recall": round(text_recall, 4),
        "text_precision": round(text_precision, 4),
        "sentences": len(gs),
        "missing": missing,
        "leaked": leaked,
        "order_tau": round(tau, 4),
        "heading_recall": round(head_recall, 4),
        "heading_precision": round(head_precision, 4),
        "figures": nf,
        "figure_kind": round(kinds_ok / nf, 4) if nf else 1.0,
        "figure_anchor": round(anchors_ok / nf, 4) if nf else 1.0,
        "figure_problems": fig_problems,
        "extra_figures": extra_figs,
        "ocr_match": sorted(out["ingest"].get("ocr_pages", [])) == sorted(gold["ingest"].get("ocr_pages", [])),
        "title_match": _norm(out["title"]) == _norm(gold["title"]),
    }


def evaluate(golden_dir: Path, fixtures: Path, library: Path, mode: str = "library", log=print) -> dict[str, dict]:
    """mode='library': score the current library doc.json (rules + /ingest + user labels).
    mode='rules': re-extract each fixture into a temp library with rules only (the free path)."""
    from .library import doc_id_for, sha256_file

    results = {}
    tmp = Path(tempfile.mkdtemp(prefix="rsvp-eval-")) if mode == "rules" else None
    for g in sorted(golden_dir.glob("*.doc.json")):
        name = g.name[: -len(".doc.json")]
        gold = json.loads(g.read_text(encoding="utf-8"))
        pdf = fixtures / f"{name}.pdf"
        if mode == "rules":
            if not pdf.exists():
                log(f"  skip {name}: no fixture PDF")
                continue
            from .assemble import assemble
            from .extract import extract
            from .rules import write_rules

            d = extract(pdf, library=tmp, log=lambda *_: None)
            write_rules(d)
            out, _, _ = assemble(d)
        else:
            d = library / doc_id_for(pdf, sha256_file(pdf)) if pdf.exists() else None
            if not d or not (d / "doc.json").exists():
                cands = sorted(library.glob(f"{name}-*/doc.json"))
                if not cands:
                    log(f"  skip {name}: not in library")
                    continue
                d = cands[0].parent
            out = json.loads((d / "doc.json").read_text(encoding="utf-8"))
        results[name] = score(out, gold)
    return results


def report(results: dict[str, dict], verbose: bool = False) -> str:
    head = f"{'document':<26} {'text R/P':>11} {'miss':>5} {'leak':>5} {'order':>6} {'heads R/P':>10} {'fig kind/anch':>14} {'ocr':>4} {'title':>5}"
    lines = [head, "-" * len(head)]
    for name, r in results.items():
        lines.append(
            f"{name[:26]:<26} {r['text_recall']:>5.3f}/{r['text_precision']:<5.3f} {len(r['missing']):>5} {len(r['leaked']):>5} "
            f"{r['order_tau']:>6.3f} {r['heading_recall']:>4.2f}/{r['heading_precision']:<5.2f} "
            f"{r['figure_kind']:>6.2f}/{r['figure_anchor']:<6.2f} {'ok' if r['ocr_match'] else 'NO':>4} {'ok' if r['title_match'] else 'NO':>5}")
        if verbose:
            for s in r["missing"][:5]:
                lines.append(f"      missing: {s[:110]}")
            for s in r["leaked"][:5]:
                lines.append(f"      leaked:  {s[:110]}")
            for s in r["figure_problems"][:6]:
                lines.append(f"      figure:  {s}")
            if r["extra_figures"]:
                lines.append(f"      extra figures: {', '.join(r['extra_figures'][:6])}")
    return "\n".join(lines)
