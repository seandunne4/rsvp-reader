"""Command-line entry point. Subcommands are filled in milestone by milestone (see SPEC.md)."""

import argparse
import sys

from . import __version__

PLANNED = {
    "extract": "M1: extract text blocks, figures and page images from a PDF",
    "show-blocks": "M2: print a compact per-page block listing for /ingest",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rsvp", description="RSVP reader tools")
    parser.add_argument("--version", action="version", version=f"rsvp-reader {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check that dependencies are installed").set_defaults(func=cmd_doctor)
    for name, desc in PLANNED.items():
        p = sub.add_parser(name, help=f"(not built yet) {desc}")
        p.set_defaults(func=lambda a, d=desc: (print(f"Not implemented yet. {d}"), 1)[1])

    args, _rest = parser.parse_known_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
