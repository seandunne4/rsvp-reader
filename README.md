# RSVP Reader

A speed reader for long readings: news articles saved as PDF and business cases with exhibits. It
shows one word at a time at a fixed focal point (RSVP), at whatever speed you like. When the text
refers to a chart, table or exhibit, it pauses and shows it to you.

The hard part it solves is getting clean text out of messy PDFs. It strips ads, page headers,
watermarks and endnotes; it OCRs PDFs whose text layer is scrambled; and it keeps figures with
the paragraph that cites them.

Everything runs **on your own computer**. Your PDFs aren't uploaded anywhere, and there's no account.

## What you get

- **Reader:** one word at a time, with a highlighted focal letter.
  - Pacing slows for punctuation, long words and numbers, and eases back in after a pause.
  - You can jump back or forward by sentence or by paragraph.
  - When paused, the whole paragraph is shown and you can click any word to jump to it.
- **Figures:**
  - Charts, tables and exhibits pause the reader where the text first mentions them.
  - Photos never interrupt; they live in a thumbnail strip you can open any time.
- **Library:** all your readings, with progress and time left. You pick up exactly where you stopped.
- **Review screen:** see how each page was read, and fix anything that's wrong (a missed
  heading, an ad that slipped through, a figure in the wrong place) with one keystroke.
- **Optional, needs Claude Code:** a `/ingest` command that does a careful, page-by-page cleanup
  and writes short recaps for each section. See [Even cleaner text with Claude Code](#even-cleaner-text-with-claude-code-optional).

## Install (one time)

You need two free tools: **Git** (to download the code) and **uv** (which installs Python and
everything else for you).

The repository is private, so you also need a free [GitHub](https://github.com) account, and
an invite from the owner. Accept the invite email first. The first time you run `git clone`,
Git opens a window to sign in to GitHub.

### Windows

Open **PowerShell** (Start menu → type "PowerShell") and run:

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
```

Close PowerShell and open a new one, so it sees the new tools. Then download the reader to a
folder of your choice, for example your Documents folder:

```powershell
cd $HOME\Documents
git clone https://github.com/seandunne4/rsvp-reader.git RSVP-Reader
cd RSVP-Reader
```

### Mac

Open **Terminal**. If you don't have [Homebrew](https://brew.sh), install it first. Then:

```bash
brew install git uv
cd ~/Documents
git clone https://github.com/seandunne4/rsvp-reader.git RSVP-Reader
cd RSVP-Reader
```

> The first time you start the reader, uv downloads Python and the libraries it needs (about
> 300 MB). That takes a few minutes, once. They're kept in a `.rsvp-reader` folder in your home
> folder, not inside the project.

## Start the reader

**Double-click the launcher** in the `RSVP-Reader` folder:

- **Windows:** `Start RSVP Reader.bat`
- **Mac:** `Start RSVP Reader.command`

Your browser opens the library at **http://127.0.0.1:8765**. A small window flashes up while
the reader starts, then closes by itself. The reader keeps running quietly in the background;
there's nothing to keep open. Bookmark the page so you can come back to it anytime.

- **Stopping it:** click **Quit** at the top of the library page. Shutting down or logging out
  also stops it.
- **Start at login:** tick **Start at login** at the top of the library page, and the reader
  starts in the background whenever you log in. Then the bookmark is all you need.
- **If it isn't running,** the library page shows a red "The reader isn't running" box. Double-click
  the launcher and the page reconnects by itself. Double-clicking while it's already running just
  opens the page.

<details><summary>Prefer the command line?</summary>

From the `RSVP-Reader` folder (on Windows, use `.\rsvp.ps1` from PowerShell, preceded by
`powershell -ExecutionPolicy Bypass -File` if scripts are blocked):

| Command | What it does |
|---|---|
| `./rsvp.sh start` | start in the background and open the browser (`--no-open` to skip the browser) |
| `./rsvp.sh stop` | stop the background reader |
| `./rsvp.sh autostart on` / `off` | start (or don't) at login |
| `./rsvp.sh serve` | run in the foreground in this window instead; Ctrl+C stops it |

The background reader writes its log to `.rsvp-reader/server.log` in your home folder.
</details>

**Tip:** for one-click starts, make a shortcut to the launcher on your desktop. On Windows,
right-click → Show more options → Send to → Desktop. On a Mac, drag it to the Dock while holding
⌘ and ⌥.

## Add a reading

Any of these work:

1. **Inbox folder (most reliable):** copy a PDF into the `Inbox` folder inside `RSVP-Reader`.
   While the reader is running, it's added within a few seconds, and the original moves to
   `Inbox/Imported`. Use this if your browser won't let you upload files (common on work
   laptops).
2. **Drag and drop** a PDF anywhere on the library page.
3. Click **Choose PDF…** on the library page.

Most PDFs take a few seconds. PDFs whose text is scrambled (some news sites produce these) are
read with OCR, which takes about 8 seconds per page.

**Tips for good results:**
- Print news articles to PDF from your browser ("Save as PDF"). Reader view, if your browser has
  it, gives the cleanest result.
- Business cases work best as the original PDF you were given.

## Reading controls

| Key | Action |
|---|---|
| Space | play / pause (or click the word) |
| ← / → | back / forward one sentence |
| Shift + ← / → | back / forward one paragraph |
| ↑ / ↓ | faster / slower |
| E | open the exhibit the text just mentioned |
| C | show the recap for the section you just finished (if the reading has recaps) |
| R | restart from the beginning |
| Esc | close a figure or recap |

Use the ⚙ button to turn off auto-pausing at figures, or to show recaps automatically at each
section break. The dropdown at the top jumps to any section.

## Fixing a reading (Review screen)

Click **Review** next to a reading. On the left is the PDF page, with every block of text outlined
in a color showing how it was read (blue = body text, purple = heading, orange = caption, grey
dotted = dropped). On the right is the text exactly as the reader will play it.

- **Fixing a block:** click it on the page, then press **B** (body), **H** (heading),
  **C** (caption), **Q** (quote) or **X** (drop it). **U** returns it to the automatic label.
- **Fixing a figure:** click it to change its type (a chart pauses the reader; a photo doesn't), or
  press **A** and then click the paragraph it should come after.
- **Undo:** Ctrl+Z.

Changes save instantly.

## Even cleaner text with Claude Code (optional)

Out of the box, the reader uses built-in rules to clean up each PDF. They're very good on news
articles and decent on business cases. If you have [Claude Code](https://claude.com/claude-code),
you can do a much more careful pass:

1. Open Claude Code in the `RSVP-Reader` folder.
2. Type `/ingest` followed by the reading's name, which is shown after you add it. For example:
   `/ingest my-case-a1b2c3d4`.

Claude looks at the actual pages and fixes what the rules missed: lost headings, dropped
paragraphs or bullet lists, captions and credits, OCR mistakes, and figures in the wrong place. For
cases and longer readings, it also writes a short **recap** and a "what was the point?" question
for each section. It never rewrites the article itself, and you can still adjust everything
afterwards in the Review screen.

## Where your files are

- `library/`: your processed readings, reading progress and settings.
- `Inbox/`: drop new PDFs here.

Both stay on your computer. They're excluded from git, so they're never shared if you share or
update the code.

> **Please don't share your readings.** Course cases and news articles are usually licensed to
> you personally (cases often say so on every page). Share the app, not the PDFs: each person
> adds their own.

**Using it on two computers:** put the `RSVP-Reader` folder in OneDrive, iCloud Drive or Dropbox,
and your library, progress and Inbox follow you. Each computer still needs uv installed.

## Updating

When there's a new version, run this in the `RSVP-Reader` folder:

```bash
git pull
```

Then restart the reader. Your library isn't touched.

## Troubleshooting

- **"The reader isn't running" / "Failed to fetch":** it was stopped with Quit, or by a
  restart or log-out. Double-click the launcher again (or tick **Start at login** so this
  doesn't happen); the page reconnects by itself.
- **The launcher says the reader couldn't start:** it prints the end of the log. The full log is
  in `.rsvp-reader/server.log` in your home folder.
- **"running scripts is disabled on this system" (Windows):** use the launcher, or the full
  command above with `-ExecutionPolicy Bypass`.
- **The Mac launcher won't open ("unidentified developer"):** right-click it, choose Open, then
  confirm. You only need to do this once. If it says you don't have permission, run
  `chmod +x "Start RSVP Reader.command" rsvp.sh` in Terminal from the folder.
- **"uv is not recognized" / "command not found: uv":** open a new PowerShell or Terminal window
  after installing uv. On Mac, check that Homebrew finished without errors.
- **Port already in use:** something else is using port 8765. Start on a different port with
  `start --port 8766`.
- **Upload or drag-and-drop does nothing:** your browser may be blocking file uploads. Use the
  Inbox folder instead.
- **A reading looks wrong:** open it in Review and fix the blocks, or run `/ingest` on it if
  you have Claude Code.
- **Checking the install:** `.\rsvp.ps1 doctor` (Windows) or `./rsvp.sh doctor` (Mac) prints the
  versions of everything it uses.

## For the curious

The pipeline:
1. `extract` pulls text blocks, figures and page images out of the PDF, with OCR where needed.
2. Labelers decide what each block is. There are three layers, each able to override the one
   below it: the built-in rules, Claude Code's `/ingest`, and your Review edits.
3. `assemble` builds the document the reader plays.

The design is in [SPEC.md](SPEC.md). Command-line tools are listed by `./rsvp.sh --help`, and
`./rsvp.sh test` runs the test suite. Tests that need the author's sample PDFs skip themselves.

## License

GNU Affero General Public License v3.0 (see [LICENSE](LICENSE)). The reader uses
[PyMuPDF](https://pymupdf.readthedocs.io/), which is AGPL-licensed, so the reader is too. In
practice, you're free to use, share and modify it; if you share a modified version, share its
source code as well.
