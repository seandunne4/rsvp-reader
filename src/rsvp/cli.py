"""Command-line entry point. Subcommands are filled in milestone by milestone (see SPEC.md)."""

import argparse
import sys

from . import __version__

PLANNED = {
    "assemble": "M2: merge labels into doc.json",
    "check": "M2: validate labels and doc.json",
    "serve": "M4: run the local library/reader server",
    "eval": "M6: score ingestion against golden documents",
}


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
    from pathlib import Path

    from .extract import extract

    library = Path(args.library) if args.library else None
    for pdf in args.pdfs:
        print(f"{pdf}")
        doc_dir = extract(Path(pdf), library=library, ocr_mode=args.ocr)
        print(f"  -> {doc_dir}")
    return 0


def cmd_show_blocks(args: argparse.Namespace) -> int:
    from .library import resolve_doc
    from .show import show_blocks

    pages = None
    if args.pages:
        pages = set()
        for part in args.pages.split(","):
            a, _, b = part.partition("-")
            pages.update(range(int(a), int(b or a) + 1))
    text = show_blocks(resolve_doc(args.doc), pages=pages, full=args.full)
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rsvp", description="RSVP reader tools")
    parser.add_argument("--version", action="version", version=f"rsvp-reader {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check that dependencies are installed").set_defaults(func=cmd_doctor)
    p = sub.add_parser("extract", help="extract text blocks, figures and page images from PDFs")
    p.add_argument("pdfs", nargs="+")
    p.add_argument("--library", help="library folder (default: RSVP_LIBRARY or ./library)")
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto")
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("show-blocks", help="print a compact per-page block listing")
    p.add_argument("doc", help="doc id, unique id prefix, or doc folder")
    p.add_argument("--pages", help="e.g. 1-3,7")
    p.add_argument("--full", action="store_true", help="don't truncate block text")
    p.set_defaults(func=cmd_show_blocks)
    for name, desc in PLANNED.items():
        p = sub.add_parser(name, help=f"(not built yet) {desc}")
        p.set_defaults(func=lambda a, d=desc: (print(f"Not implemented yet. {d}"), 1)[1])

    args, _rest = parser.parse_known_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
