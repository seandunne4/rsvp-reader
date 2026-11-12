"""Command-line entry point. Subcommands are filled in milestone by milestone (see SPEC.md)."""

import argparse
import sys
from pathlib import Path

from . import __version__

PLANNED = {
    "eval": "M6: score ingestion against golden documents",
}


def out(text: str) -> None:
    """Write UTF-8 regardless of the console code page (Windows)."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Confirm the core dependencies import and report versions."""
    import pymupdf
    import onnxruntime
    import rapidocr  # noqa: F401
    import fastapi

    print(f"rsvp-reader {__version__}")
    print(f"python      {sys.version.split()[0]}")
    print(f"pymupdf     {pymupdf.__version__}")
    print(f"onnxruntime {onnxruntime.__version__}")
    print(f"fastapi     {fastapi.__version__}")
    print("rapidocr    ok")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from .extract import extract

    library = Path(args.library) if args.library else None
    for pdf in args.pdfs:
        print(f"{pdf}")
        doc_dir = extract(Path(pdf), library=library, ocr_mode=args.ocr)
        print(f"  -> {doc_dir}")
    return 0


def _report(doc_dir: Path, doc: dict, warnings: list[str], errors: list[str], quiet: bool = False,
            dry_run: bool = False) -> int:
    for e in errors:
        out(f"  ERROR   {e}")
    for w in ([] if quiet else warnings):
        out(f"  warning {w}")
    shown = sum(1 for f in doc["figures"])
    status = "NOT WRITTEN (errors)" if errors else "ok (dry run, nothing written)" if dry_run else "doc.json written"
    out(f"  {doc_dir.name}: {doc['stats']['paragraphs']} paragraphs, {doc['stats']['words']} words, "
        f"{len(doc['sections']) - 1} sections, {shown} figures, {len(warnings)} warnings -> {status}")
    return 1 if errors else 0


def cmd_label_rules(args: argparse.Namespace) -> int:
    from .library import resolve_doc
    from .rules import write_rules

    for ref in args.docs:
        p = write_rules(resolve_doc(ref))
        out(f"  wrote {p}")
    return 0


def cmd_assemble(args: argparse.Namespace) -> int:
    from .assemble import assemble
    from .library import resolve_doc

    rc = 0
    for ref in args.docs:
        doc_dir = resolve_doc(ref)
        doc, warnings, errors = assemble(doc_dir, write=not args.dry_run)
        rc |= _report(doc_dir, doc, warnings, errors, quiet=args.quiet, dry_run=args.dry_run)
    return rc


def cmd_add(args: argparse.Namespace) -> int:
    """extract + rules + assemble: what the upload button will do."""
    from .assemble import assemble
    from .extract import extract
    from .rules import write_rules

    rc = 0
    library = Path(args.library) if args.library else None
    for pdf in args.pdfs:
        out(f"{pdf}")
        doc_dir = extract(Path(pdf), library=library, ocr_mode=args.ocr, log=lambda *_: None)
        write_rules(doc_dir)
        doc, warnings, errors = assemble(doc_dir)
        rc |= _report(doc_dir, doc, warnings, errors, quiet=True)
    return rc


def cmd_show_blocks(args: argparse.Namespace) -> int:
    from .library import resolve_doc
    from .show import show_blocks

    pages = None
    if args.pages:
        pages = set()
        for part in args.pages.split(","):
            a, _, b = part.partition("-")
            pages.update(range(int(a), int(b or a) + 1))
    out(show_blocks(resolve_doc(args.doc), pages=pages, full=args.full))
    return 0


def cmd_show_doc(args: argparse.Namespace) -> int:
    from .library import resolve_doc
    from .show import show_doc

    out(show_doc(resolve_doc(args.doc), width=0 if args.full else 140))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(host=args.host, port=args.port, library=Path(args.library) if args.library else None)
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    """Save a reviewed doc.json as the golden reference for a fixture."""
    import shutil

    from .library import PROJECT_ROOT, resolve_doc

    doc_dir = resolve_doc(args.doc)
    if not (doc_dir / "doc.json").exists():
        out("No doc.json yet; run assemble first.")
        return 1
    name = args.name or doc_dir.name.rsplit("-", 1)[0]
    dest = PROJECT_ROOT / "tests" / "golden" / f"{name}.doc.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(doc_dir / "doc.json", dest)
    out(f"  saved {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rsvp", description="RSVP reader tools")
    parser.add_argument("--version", action="version", version=f"rsvp-reader {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check that dependencies are installed").set_defaults(func=cmd_doctor)

    p = sub.add_parser("add", help="extract + rules + assemble a PDF into the library")
    p.add_argument("pdfs", nargs="+")
    p.add_argument("--library", help="library folder (default: RSVP_LIBRARY or ./library)")
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("extract", help="extract text blocks, figures and page images from PDFs")
    p.add_argument("pdfs", nargs="+")
    p.add_argument("--library", help="library folder (default: RSVP_LIBRARY or ./library)")
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("label-rules", help="write labels.rules.json with the basic rules labeler")
    p.add_argument("docs", nargs="+", help="doc ids (or unique parts of ids)")
    p.set_defaults(func=cmd_label_rules)

    p = sub.add_parser("assemble", help="merge label layers into doc.json")
    p.add_argument("docs", nargs="+")
    p.add_argument("--dry-run", action="store_true", help="don't write doc.json")
    p.add_argument("--quiet", action="store_true", help="hide warnings")
    p.set_defaults(func=cmd_assemble)

    p = sub.add_parser("check", help="validate labels and report warnings (doesn't write doc.json)")
    p.add_argument("docs", nargs="+")
    p.set_defaults(func=cmd_assemble, dry_run=True, quiet=False)

    p = sub.add_parser("show-blocks", help="print a compact per-page block listing")
    p.add_argument("doc", help="doc id, unique part of an id, or doc folder")
    p.add_argument("--pages", help='e.g. "1-3,7" (quote it in PowerShell)')
    p.add_argument("--full", action="store_true", help="don't truncate block text")
    p.set_defaults(func=cmd_show_blocks)

    p = sub.add_parser("show-doc", help="print the assembled document as plain text")
    p.add_argument("doc")
    p.add_argument("--full", action="store_true", help="don't truncate paragraphs")
    p.set_defaults(func=cmd_show_doc)

    p = sub.add_parser("serve", help="run the local library + reader at http://127.0.0.1:8765")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--library", help="library folder (default: RSVP_LIBRARY or ./library)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("golden", help="save a reviewed doc.json to tests/golden/ as the reference")
    p.add_argument("doc")
    p.add_argument("--name", help="golden name (default: the doc id without its hash)")
    p.set_defaults(func=cmd_golden)

    for name, desc in PLANNED.items():
        p = sub.add_parser(name, help=f"(not built yet) {desc}")
        p.set_defaults(func=lambda a, d=desc: (print(f"Not implemented yet. {d}"), 1)[1])

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
