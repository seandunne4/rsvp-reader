---
name: ingest
description: Turn a course-reading PDF into a clean, structured document for the RSVP reader. Use when the user runs /ingest with a PDF path or a library doc id, or asks to ingest, clean up, or fix a reading in the library.
---

# /ingest: label a reading for the RSVP reader

You are the high-quality labeler in this project's pipeline (see `SPEC.md`). Deterministic code
has already extracted the PDF into text blocks and figures, and a basic rules labeler has
made a first pass. Your job is to **look at the actual pages** and write
`labels.claude.json`: corrections that make the assembled document match what a careful
human reader would want to RSVP through.

**The one hard rule: never retype the article.** You only assign roles, order, merges and figure
info, by block id. Text changes go through small `text_fixes` find/replace entries. The only free
text you write is metadata, figure titles, captions, credits and alt text, plus recaps if asked.

## Commands

Use `.\rsvp.ps1 <command>` on Windows and `./rsvp.sh <command>` on macOS. In PowerShell, quote page
lists: `--pages "1-3,7"`. A doc can be referred to by any unique part of its id (e.g. `cattle`).

| Command | What it does |
|---|---|
| `add <pdf>` | extract + rules + assemble (OCR'd PDFs take ~8 s/page) |
| `show-doc <doc> --full` | the assembled document, as the reader will present it |
| `show-blocks <doc> --pages "N"` | every block on a page: id, bbox, font size/style, flags, text |
| `check <doc>` | validate labels and list warnings (doesn't write) |
| `assemble <doc>` | write `doc.json` |
| `golden <doc>` | save the reviewed `doc.json` to `tests/golden/` (only when asked) |

Files live in `library/<doc-id>/`:
- `extract.json`: the extraction.
- `pages/pNNN.png`: page images, which you should Read.
- `figures/*.png`: figure crops.
- `labels.rules.json`, `labels.claude.json` (yours) and `labels.user.json`: the label layers.

## Steps

1. **Get the doc.** If given a PDF path, run `add` on it. If it's already in the library, use
   the existing extraction; don't re-extract unless asked, because re-extraction can shift ids.
2. **Read the current result:** `show-doc --full` and `check`. Read `labels.rules.json` too, and
   `labels.claude.json` if one already exists (then you're revising it, not starting over).
3. **Look at the pages.** Read page 1, every page with a figure, every OCR'd page, and every
   page where the show-doc output looks suspicious. Compare each against `show-blocks` for that
   page. For long cases you needn't view every page of plain narrative: check a sample, plus
   any page whose blocks look odd (unusual sizes, flags, short fragments, roles that don't fit).
4. **Write `labels.claude.json`** with only what needs to change (format below). Go through the
   checklist.
5. **Run `check`, then `assemble`.** Fix every error. For each warning, either fix it or be
   ready to explain why it's fine.
6. **Skim `show-doc --full` once more** from top to bottom, as the reader would.
7. **Report** to the user, briefly:
   - what you changed, grouped by kind;
   - anything you weren't sure about, with page numbers;
   - any remaining warnings.

## Checklist

- **Metadata:** title, subtitle, byline and dateline are right. Set them in `meta` if the rules
  got them wrong or fused them (e.g. byline and date in one block).
- **Nothing that isn't article text is in the body:**
  - ads, newsletter or subscription prompts, "[Most read]"/related links
  - photo credits, captions, chart labels
  - author bios, "Appeared in print" lines, reprint notices, running heads, page numbers
  - a caption sentence that leaked into a paragraph: remove it with a text fix whose
    replacement is `""` (include the leading space so no double space remains)
- **Nothing real is missing:** scan blocks labeled boilerplate, caption or figure_text in
  `show-blocks`. A real paragraph wrongly dropped is worse than a stray line kept.
- **Headings:** every section heading is `heading` (level 2, or 3 for sub-sections), and no
  paragraph is mislabeled as one.
- **Paragraph boundaries:** the reading flow never breaks mid-sentence (use `merge`), and two
  paragraphs aren't fused. Quotes set off as block quotes are `quote`.
- **Reading order:** for multi-column or wrap-around layouts, make sure the order is right. If
  it isn't, give a complete `order`.
- **Figures:**
  - `kind`:
    - `chart`, `table`, `exhibit` or `diagram`: these auto-pause in the reader.
    - `photo`: rail only.
    - `decorative` or `ad`: hidden.
  - `label`: "Exhibit 4", "Figure 2" and so on, when the document numbers them.
  - Caption vs credit: the caption block stays `caption`. If a credit ("PHOTO: …",
    "Getty Images", "(Name / Paper)") is fused into it, strip it from the caption block with a
    text fix and put it in the figure's `credit`, copied verbatim.
  - For charts and tables, set `title` if it's only visible in the image ("Value of beef
    products"), and a one-sentence `alt_text` saying what the chart shows.
  - Images that belong together (side-by-side photos, multi-panel exhibits) share a `group`.
  - Anchors: leave them automatic unless they're wrong. If one is wrong, set
    `"anchor": {"block": "<block id of the paragraph it should follow>"}`, or `{"end": true}` for
    exhibits that belong in the end-of-document stop.
- **OCR'd pages** (marked `OCR(...)` in show-blocks): read the page image next to the text and
  fix misreads with text fixes, especially names, numbers, quote marks and dropped words.
  **Any fix that changes a digit must include a `why`** that says you checked the page image.
- **Stock tickers, "TAP TO UNMUTE" and similar web debris** inside paragraphs: remove with text
  fixes.

## labels.claude.json format

```json
{
  "schema": "rsvp-labels/1",
  "extract_sha": "<copy from labels.rules.json>",
  "by": "claude",
  "meta": {"byline": "By Patrick Thomas", "dateline": "March 21, 2022"},
  "blocks": {
    "p6-b4": {"role": "body"},
    "p5-b7": {"role": "caption", "figure": "p5-f1"},
    "p3-b2": {"role": "heading", "level": 2},
    "p9-b3": {"role": "boilerplate", "reason": "author bio"}
  },
  "text_fixes": {
    "p6-b4": [{"find": " Trey Wasserburger, a North Platte rancher, has invested in Sustainable Beef.", "replace": "",
               "why": "photo caption that leaked into the paragraph"}]
  },
  "figures": {
    "p3-f1": {"kind": "chart", "title": "Value of beef products", "alt_text": "Retail beef value rose sharply after 2020 while live steer prices stayed flat."},
    "p5-f1": {"kind": "photo", "credit": "PHOTO: TANNEN MAURY/EPA/SHUTTERSTOCK"}
  },
  "merge": [["p2-b9", "p2-b11", "p3-b2"]]
}
```

How layers combine (user > claude > rules):
- **`blocks` entries:** yours replace the rules' entry for that block entirely, so include
  `figure` and `level` where relevant.
- **`figures`:** merge per field, so you only list the fields you're changing.
- **`merge`, `order` and `recaps`:** **replaced wholesale.** If you write `merge`, start from
  the full list in `labels.rules.json` and edit it, or you'll undo the rules' merges.
- **Roles:** `title, subtitle, byline, dateline, heading, body, quote, list_item, caption,
  credit, endnote, figure_text, boilerplate`.
- **`text_fixes`:** a `find` must occur in the block's extracted text (after earlier fixes in
  the same list). Use `"all": true` to replace every occurrence.

Recaps (only when the user asks for them): `"recaps": [{"heading": "<heading block id or null for the
opening section>", "summary": "2-3 sentences", "check": {"q": "...", "a": "..."}}]`. Write
them from the assembled text, so they're faithful and don't editorialize.
