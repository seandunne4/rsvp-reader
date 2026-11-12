# RSVP Reader — Spec (agreed design, v1)

## Context
A personal RSVP reader for MBA course readings: news articles printed to PDF (WSJ, NYT, Tribune) and business cases with exhibits. The prototype (`rsvp-reader-oreo-reeses.html`) sets the reading experience to keep. The hard part is ingestion: getting clean, ordered, structured text plus figures out of messy PDFs.

The four sample PDFs fail in different ways:
- **Oreo (WSJ, 2025):** the text layer is garbage on every page because of broken font encoding, so it needs OCR. It also has a ticker chip mid-sentence ("MDLZ -0.08%"), "TAP TO UNMUTE", photo credits, a print-edition line and an author bio.
- **Cattle (WSJ, 2022):** text is clean, but charts are images that the body text wraps around. Line-break hyphens get lost ("marketready"), and one orphaned caption sentence ends up inside a body paragraph.
- **Goose Island (Tribune):** browser print chrome on every page, "ADVERTISEMENT" and "[Most read]" lines, a sticky newsletter bar, missing spaces ("gotwhatit"), a sentence split across a page with an ad in between, and pages 10–12 are all junk.
- **Google (NYT):** per-page headers and footers, photos that didn't render (captions remain), and real section headings.

## Decisions
- Fully local, **no paid API calls**.
- Messy PDFs go through a **Claude Code `/ingest` workflow**, the primary v1 path. The rules pipeline is basic in v1. Both produce the same output.
- Runs on **both Mac and Windows**. Desktop browser only, iPhone is not a goal.
- Library and reading progress live in a **OneDrive-synced folder**, shared across machines.
- A **quick review screen** to fix ingestion results.
- Figures:
  - Photos go to a thumbnail rail only.
  - Charts, tables and exhibits auto-pause at their anchor.
  - Tables are never RSVP'd.
  - Captions show with the figure, not in the word stream.
- Exhibits anchor **where the text first references them** ("see Exhibit 4").
- Section recaps and a "what was the point?" check are generated at ingest, **off by default**.
- Add **paragraph back/forward** keys.
- Golden-file testing for ingestion quality. The user will add 2–3 business case PDFs.

## Architecture
- **Python 3.12 local app, managed with `uv`.** Cross-platform, pip-only dependencies, no system installs. Windows: `winget install astral-sh.uv`. Mac: `brew install uv`.
  - `PyMuPDF`: text spans with positions and fonts, images, page rendering, vector drawings.
  - `RapidOCR` (onnxruntime): pip-only OCR on both operating systems, returns line boxes. Tesseract is an optional alternative behind the same interface.
  - `FastAPI` + `uvicorn`: local server at `localhost`.
  - Bundled English wordlist for garbage detection and hyphen repair.
- **Front end:** vanilla HTML/JS with no build step. The prototype's reader logic is ported into modules.
- **Library:** `RSVP_LIBRARY` defaults to `<project>/library/` (inside OneDrive). Each document gets its own folder:
  `library/<slug>-<hash8>/{source.pdf, extract.json, pages/p001.png, figures/*.png, labels.rules.json, labels.claude.json, labels.user.json, doc.json, progress.json}`
- **Progress** is stored as `{block_id, word_offset, updated_at}`: last write wins, anchored to block IDs so it survives re-assembly. Saved, debounced, on pause and every few seconds.

### Pipeline: one extraction, layered labels, one assembler
```
PDF → extract (deterministic) → extract.json + page PNGs + figure crops
        labels.rules.json   (auto, basic rules)
        labels.claude.json  (/ingest in Claude Code)
        labels.user.json    (review screen)
      → assemble (merge user > claude > rules) → doc.json → reader
```
Both ingestion paths produce the same output by construction: they only write labels. Claude Code **never retypes text**. It assigns roles and order and makes find/replace fixes by block ID. The assembler warns about any fix that changes digits, and the review screen shows those warnings.

### Extraction (`rsvp extract`)
- Blocks and lines come from PyMuPDF `rawdict`. Spaces are rebuilt from glyph gaps, which fixes "gotwhatit".
- Each block records page, bbox, font size and weight, and flags.
- **Garbage detection runs per block and per page.** It scores:
  - the share of letters and common punctuation;
  - private-use, control or replacement characters;
  - the share of tokens of three or more letters that appear in the wordlist (garbage < 0.5, clean > 0.85);
  - fonts that have no ToUnicode map;
  - a page that is mostly image with little text (a scan).

  A page that scores as garbage is OCR'd and the OCR lines are mapped into blocks by position. An ambiguous page is OCR'd anyway and the version with the better word-validity score wins.
- **Hyphen repair:** join a line-end hyphen to the next lowercase line if the joined word is in the wordlist, otherwise keep the hyphen.
- **Figures:**
  - Raster images are extracted.
  - Clusters of vector drawings are rendered to PNG at 2x.
  - Case exhibit pages (top line matches `^Exhibit \d+`) have the area below the title rendered as one image.
  - Tables are shown as **images** in v1, with their text kept only as alt/search text.
- `rsvp show-blocks <doc>` prints a compact per-page listing (id, bbox, font size, flags, first 80 characters) for `/ingest` to work from.

### Rules labeler, basic in v1
- Repeated header/footer lines (same normalized text and position on ≥50% of pages).
- Page numbers and browser print lines (date, URL, `n/12`).
- A small list of boilerplate patterns: ADVERTISEMENT, "Subscribers, get…", "[Most read]", the reprint notice, "PHOTO:" and credit lines.
- Captions: a small-font block directly below or beside an image.
- Headings: short lines in a larger or bold font.
- Body: the dominant font size.
- Reading order: column-aware geometric sort.

### `/ingest` (project skill at `.claude/skills/ingest/SKILL.md`)
1. `uv run rsvp extract <pdf>`
2. Read `show-blocks` and the page PNGs.
3. Write `labels.claude.json`: metadata, roles, order, merges, figure kinds, labels, captions, anchors, OCR fixes and optional recaps.
4. `rsvp assemble` then `rsvp check`. Check means schema-valid, every block labeled, no digit changes the fix doesn't explain, every figure anchored or deliberately unanchored.
5. Report a summary and warnings.

## Schemas (draft)

**labels.\*.json** (the same format from every source):
```json
{
  "schema": "rsvp-labels/1", "extract_sha": "…", "by": "claude",
  "meta": {"title": "…", "subtitle": "…", "byline": "…", "date": "…"},
  "blocks": {"b12": {"role": "body"}, "b13": {"role": "boilerplate", "reason": "ad"},
             "b20": {"role": "heading", "level": 2}, "b14": {"role": "caption", "figure": "img3"},
             "b15": {"role": "credit", "figure": "img3"}},
  "order": ["b3", "b5", "b12"],
  "merge": [["b40", "b41"]],
  "text_fixes": {"b22": [{"find": "marketready", "replace": "market-ready"}]},
  "figures": {"img3": {"kind": "chart", "label": "Exhibit 2", "anchor": {"block": "b30"}}},
  "recaps": [{"section": "s2", "summary": "…", "check": {"q": "…", "a": "…"}}]
}
```
Block roles: `title, subtitle, byline, dateline, heading, body, quote, list_item, caption, credit, endnote, figure_text, boilerplate`.
- `endnote`: case endnote sections. Never RSVP'd.
- `figure_text`: text inside a chart or table (extract flags it `in_figure`). Kept only as figure alt text.

Figure groups: one exhibit can be several images (Amazon Exhibit 12 is five maps). The labels' `figures` entry takes `"group": "p41-f1"`, and grouped figures show together under the first one's label.

Superscript note markers are stripped from block text during extraction and kept as `sups: [{at, text}]`. That way endnote numbers never become RSVP words.

**doc.json** (what the reader loads):
```json
{
  "schema": "rsvp-doc/1", "id": "oreo-a1b2c3d4",
  "title": "…", "subtitle": "…", "byline": "…", "date": "…",
  "source": {"file": "source.pdf", "sha256": "…", "pages": 8},
  "ingest": {"method": "claude-code", "ocr_pages": [1, 2, 3], "warnings": [], "created_at": "…"},
  "sections": [{"id": "s0", "title": null}, {"id": "s1", "title": "Seeking a hit", "level": 2}],
  "body": [{"id": "p7", "type": "para", "section": "s1", "text": "…",
            "src": [{"page": 3, "bbox": [56, 640, 890, 860], "ext": "b31"}]}],
  "figures": [{"id": "fig2", "kind": "photo|chart|table|exhibit|diagram", "label": "Exhibit 4",
               "caption": "…", "credit": "…", "image": "figures/fig2.png",
               "anchor": {"block": "p7", "char": 312, "method": "reference|proximity|manual"},
               "alt_text": "…"}],
  "recaps": [{"section": "s1", "summary": "…", "check": {"q": "…", "a": "…"}}]
}
```
Figures are kept out of `body`, so they never enter the word stream.

## Figure anchoring
1. **Reference:** find the first match of `\b(Exhibit|Figure|Fig\.|Table|Chart)\s+(\d+[A-Z]?)` in the body. The anchor is the **end of that sentence**, so the pause never cuts a sentence in half. Later mentions show an "Exhibit 4" badge that opens it without pausing.
2. **Proximity** (unlabeled news figures): use the body block whose vertical span contains the figure's center, or else the last body block before the figure in reading order.
3. **Manual:** set in the review screen or in labels.
4. Unreferenced exhibits appear in the rail and in a "Remaining exhibits" stop at the end.

## Reader (port of the prototype)
- **Keep:**
  - ORP focal letter
  - punctuation, long-word and number pacing
  - warm-up after a pause
  - sentence back/forward (←/→)
  - paused paragraph context with click-to-jump
  - section jump
  - ↑/↓ for WPM
  - R to restart
- **Default speed:** 300 wpm.
- **New:**
  - Shift+←/→ moves by paragraph.
  - Charts, tables and exhibits auto-pause at their anchor and show full-size with the caption until you press space (toggle in settings).
  - A thumbnail rail of all figures: clicking one opens it without losing your place.
  - Photos never interrupt.
  - Titles, headings, captions and credits never enter the word stream (headings still show as section cards, like the prototype).
  - Recaps are off by default. At a section break the hint reads "Recap available (C)". A setting shows them automatically.
- **Library page:** list of documents with title, progress %, minutes left and ingest status; upload button; Review and Read buttons.
- **Upload:** saves the PDF, runs extract and the rules labeler, and shows "Run `/ingest <id>` in Claude Code for best results".

## Review screen
- Left: the page image with block outlines colored by role.
- Right: preview of the assembled text.
- Click a block, then press a key to set its role: B body, H heading, C caption, X boilerplate, Q quote.
- Select a figure, then click a paragraph to anchor it.
- Digit-change and unlabeled-block warnings are listed at the top.
- Saves to `labels.user.json` and re-assembles straight away.

## Testing ingestion quality
- `tests/fixtures/`: the 4 articles plus the user's 2–3 cases. They stay local.
- `tests/golden/<name>.doc.json`: drafted via `/ingest`, spot-checked by the user.
- `rsvp eval` scores each fixture against its golden:
  - **Text accuracy:** word-level edit distance of the body.
  - **Recall:** how many golden sentences are present.
  - **Boilerplate leaks:** output n-grams missing from the golden, plus the known-junk list.
  - **Reading order:** Kendall tau over matched paragraphs.
  - **Headings found.**
  - **Figure anchors:** within one block of the golden.
  - **Garbage-detection confusion per page:** Oreo should flag all body pages, the others none.
- Unit tests for the garbage detector, hyphen repair, reference regex, label-precedence merge, and progress anchoring across re-assembly.
- Reader check: open each golden in the browser and step through.

## Dev setup
- Requirements: `git` and `uv`. uv installs Python 3.12 itself.
- Always run through the wrapper: `.\rsvp.ps1 <command>` on Windows, `./rsvp.sh <command>` on Mac. `test` runs pytest.
- The wrapper keeps Python, the virtualenv and the uv cache in `~/.rsvp-reader/`. That keeps them out of OneDrive (no syncing between machines; Mac and Windows each get their own `venv-*`). On Windows it also keeps them out of AppData, which the Claude desktop app's sandbox redirects.
- Sample PDFs live in `tests/fixtures/` (`oreo-wsj.pdf`, `cattle-wsj.pdf`, `goose-island-tribune.pdf`, `google-doubleclick-nyt.pdf`). Drop new samples there. Fixture PDFs and goldens are git-ignored because they're copyrighted.

## M1 findings (what extraction handles)
- **Garbage detection:** all 8 Oreo pages are flagged and OCR'd; the other 5 PDFs have none. OCR takes about 8 s per page (RapidOCR at 200 dpi, angle classifier off).
- **Spacing:** rebuilt from glyph positions. That fixes missing spaces ("gotwhatit"), spurious spaces ("US C   ar"), letter-spaced running heads ("E n t e r p r i s e") and small caps ("A MAZON").
- **Paragraphs:** browser prints that emit one block per line get merged back. OCR lines are grouped by spacing, and a short line inside a block splits it (headings).
- **Figures:**
  - Raster images and stroked vector drawings are captured.
  - Fill-only page furniture (panels, shadows, sticky bars) is ignored.
  - Exhibit pages with no picture get a title-to-"Source:" region (text tables, letter excerpts).
  - On OCR'd pages, text inside pictures ("TAP TO UNMUTE") goes to the figure's `ocr_text`, not the body.
- **Left to the labelers (by design):** running heads and watermarks (flagged `margin_*`), ads, decorative images, reading order across columns and sticky bars, paragraph merges across pages, and OCR'd ticker chips ("MondelezMDLZ -0.08%").

## Build plan (milestones)
- **M0** git init, uv project, folder layout, `.claude/launch.json`.
- **M1** Extract: blocks, spacing and hyphen repair, garbage detection, RapidOCR fallback, figure crops, page PNGs. Verified on all 4 samples.
- **M2** Label schema, assembler, `check`, basic rules labeler, `show-blocks`.
- **M3** `/ingest` skill. Produce goldens for the 4 samples; the user spot-checks them.
- **M4** Server, library page, reader port with figures, rail, auto-pause, paragraph keys and file-based progress.
- **M5** Review screen.
- **M6** `rsvp eval`, case PDFs added, recaps in `/ingest`.

**v2+**
- Smarter rules (publisher profiles learned from review corrections)
- Tables as structured cells
- .docx and URL import
- Apple Vision OCR on Mac
- Phone/PWA
- Comprehension check UX

## Risks / notes
- PyMuPDF is AGPL. That's fine for personal use.
- OneDrive can create conflict copies if both machines write `progress.json` at the same moment. This is rare, and the newer `updated_at` wins on read.
- The RapidOCR models are about 15 MB and ship with pip.
- Exhibits are shown as images, so OCR errors in exhibits never reach what you see.

## End-to-end verification
1. `uv run rsvp extract` on each sample. The Oreo pages are flagged and OCR'd, the others aren't.
2. `/ingest` on each sample → `rsvp check` passes, then `rsvp eval` against the goldens.
3. `uv run rsvp serve`, then in the browser:
   - upload a PDF;
   - review it and fix a block;
   - read it, confirming auto-pause at the Cattle charts, the rail, Shift+arrows, and that resume works after a reload;
   - repeat on the second machine through OneDrive.
