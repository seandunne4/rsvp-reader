"""Library locations and document ids."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def library_dir() -> Path:
    """RSVP_LIBRARY env var, else <project>/library (inside OneDrive, so it syncs)."""
    return Path(os.environ.get("RSVP_LIBRARY") or PROJECT_ROOT / "library")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(name: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:max_len].rstrip("-") or "doc"


def doc_id_for(pdf: Path, sha: str) -> str:
    return f"{slugify(pdf.stem)}-{sha[:8]}"


def resolve_doc(ref: str) -> Path:
    """Accept a doc id, a unique id prefix, or a path to a doc folder."""
    p = Path(ref)
    if p.is_dir() and (p / "extract.json").exists():
        return p
    lib = library_dir()
    if (lib / ref).is_dir():
        return lib / ref
    matches = [d for d in lib.glob(f"*{ref}*") if d.is_dir()] if lib.exists() else []
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(f"No unique document matches {ref!r} in {lib}")
