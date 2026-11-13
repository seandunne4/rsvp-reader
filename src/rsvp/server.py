"""Local web server: library page, reader, and a small JSON API over the library folder.

Everything lives in files under the library (OneDrive-synced), so the Mac and the Windows PC
see the same readings, progress and settings:
  library/settings.json            reader settings (wpm, auto-pause, recaps)
  library/<doc>/doc.json           what the reader loads
  library/<doc>/progress.json      {"block", "word", "updated_at"}; newest write wins
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .library import PROJECT_ROOT, library_dir

WEB = PROJECT_ROOT / "web"
DEFAULT_SETTINGS = {"wpm": 300, "autopause": True, "recaps_auto": False}
SERVABLE = ("figures/", "pages/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, data) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)  # atomic on one filesystem; avoids half-written files syncing


class Progress(BaseModel):
    block: str
    word: int = 0
    updated_at: str | None = None


class Settings(BaseModel):
    wpm: int | None = None
    autopause: bool | None = None
    recaps_auto: bool | None = None


def default_inbox() -> Path:
    """RSVP_INBOX env var, else <project>/Inbox (inside OneDrive, so either computer can fill it)."""
    return Path(os.environ.get("RSVP_INBOX") or PROJECT_ROOT / "Inbox")


def create_app(library: Path | None = None, inbox: Path | None = None, watch: bool = False) -> FastAPI:
    lib = Path(library) if library else library_dir()
    lib.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="RSVP Reader")

    @app.middleware("http")
    async def no_stale_pages(request, call_next):
        # Always revalidate, so an updated page or script is picked up on the next load
        # instead of the browser silently showing a cached old version.
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    jobs: dict[str, dict] = {}  # import jobs: id -> {name, status, error, doc_id, source, started}
    job_counter = itertools.count(1)
    lock = threading.Lock()

    def doc_dir(doc_id: str) -> Path:
        d = (lib / doc_id).resolve()
        if d.parent != lib.resolve() or not (d / "doc.json").exists():
            raise HTTPException(404, f"no document {doc_id!r}")
        return d

    def settings() -> dict:
        return {**DEFAULT_SETTINGS, **(_read_json(lib / "settings.json", {}) or {})}

    # --- pages -------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def library_page():
        return FileResponse(WEB / "library.html")

    @app.get("/read/{doc_id}", include_in_schema=False)
    def reader_page(doc_id: str):
        doc_dir(doc_id)
        return FileResponse(WEB / "reader.html")

    @app.get("/review/{doc_id}", include_in_schema=False)
    def review_page(doc_id: str):
        doc_dir(doc_id)
        return FileResponse(WEB / "review.html")

    # --- review: page view + label editing (writes labels.user.json) -------------------
    def review_state(d: Path) -> dict:
        from . import labels as L

        ex = L.load_extract(d)
        layers = L.load_layers(d)
        merged = L.merge_layers(layers)
        roles = {}
        for b in ex["blocks"]:
            by = next((n for n in reversed(L.LAYERS) if b["id"] in (layers.get(n, {}).get("blocks") or {})), None)
            e = merged["blocks"].get(b["id"], {})
            roles[b["id"]] = {"role": e.get("role"), "by": by, "figure": e.get("figure"), "level": e.get("level")}
        figures = {}
        for f in ex["figures"]:
            by = next((n for n in reversed(L.LAYERS) if f["id"] in (layers.get(n, {}).get("figures") or {})), None)
            figures[f["id"]] = {**merged["figures"].get(f["id"], {}), "by": by}
        user = layers.get("user") or L.new_labels(d, "user")
        return {
            "id": d.name,
            "pages": ex["pages"],
            "blocks": [{k: b[k] for k in ("id", "page", "bbox", "text", "font", "flags", "source") if k in b}
                       | ({"in_figure": b["in_figure"]} if b.get("in_figure") else {}) for b in ex["blocks"]],
            "figures": ex["figures"],
            "roles": roles,
            "figure_labels": figures,
            "user": user,
            "doc": _read_json(d / "doc.json"),
        }

    @app.get("/api/docs/{doc_id}/review")
    def get_review(doc_id: str):
        return review_state(doc_dir(doc_id))

    @app.put("/api/docs/{doc_id}/labels/user")
    def put_user_labels(doc_id: str, labels: dict):
        from . import labels as L
        from .assemble import assemble

        d = doc_dir(doc_id)
        ex = L.load_extract(d)
        labels = {**labels, "schema": L.SCHEMA, "by": "user", "extract_sha": L.extract_sha(d)}
        errors = L.validate(labels, ex, "labels.user")
        if errors:
            raise HTTPException(400, {"errors": errors})
        with lock:
            if L.has_content(labels):
                L.write_layer(d, "user", labels)
            else:
                L.layer_path(d, "user").unlink(missing_ok=True)  # everything undone: no user layer
            _, _, errors = assemble(d)
        state = review_state(d)
        state["errors"] = errors
        return state

    # --- API ---------------------------------------------------------------------------
    @app.get("/api/docs")
    def list_docs():
        wpm = settings()["wpm"]
        out = []
        for d in sorted(lib.iterdir()) if lib.exists() else []:
            doc = _read_json(d / "doc.json") if d.is_dir() else None
            if not doc:
                continue
            words = doc.get("stats", {}).get("words", 0)
            prog = _read_json(d / "progress.json") or {}
            done = _words_before(doc, prog) if prog else 0
            out.append({
                "id": doc["id"], "title": doc["title"], "byline": doc.get("byline"),
                "source": doc["source"].get("original_name"), "words": words,
                "progress": round(done / words, 4) if words else 0,
                "minutes_left": round((words - done) / max(wpm, 1), 1),
                "method": doc["ingest"]["method"], "warnings": len(doc["ingest"]["warnings"]),
                "figures": len(doc["figures"]), "pages": doc["source"].get("pages"),
                "created_at": doc["ingest"].get("created_at"), "last_read": prog.get("updated_at"),
            })
        out.sort(key=lambda x: x["last_read"] or x["created_at"] or "", reverse=True)
        with lock:
            # unfinished jobs, plus ones finished in the last 10 minutes (so the page can say "Added …")
            pending = [dict(j, job=jid) for jid, j in jobs.items()
                       if j["status"] != "done" or time.time() - j.get("started", 0) < 600]
        return {"docs": out, "jobs": pending}

    @app.get("/api/docs/{doc_id}")
    def get_doc(doc_id: str):
        return FileResponse(doc_dir(doc_id) / "doc.json", media_type="application/json")

    @app.get("/api/docs/{doc_id}/files/{path:path}")
    def get_file(doc_id: str, path: str):
        d = doc_dir(doc_id)
        f = (d / path).resolve()
        if not path.startswith(SERVABLE) or d not in f.parents or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f)

    @app.get("/api/docs/{doc_id}/progress")
    def get_progress(doc_id: str):
        return JSONResponse(_read_json(doc_dir(doc_id) / "progress.json") or {})

    @app.put("/api/docs/{doc_id}/progress")
    def put_progress(doc_id: str, p: Progress):
        d = doc_dir(doc_id)
        new = {"block": p.block, "word": p.word, "updated_at": p.updated_at or _now()}
        with lock:
            old = _read_json(d / "progress.json") or {}
            if old.get("updated_at") and old["updated_at"] > new["updated_at"]:
                return {"saved": False, "progress": old}  # a newer save (other machine) wins
            _write_json(d / "progress.json", new)
        return {"saved": True, "progress": new}

    @app.get("/api/settings")
    def get_settings():
        return settings()

    @app.put("/api/settings")
    def put_settings(s: Settings):
        with lock:
            cur = settings()
            cur.update({k: v for k, v in s.model_dump().items() if v is not None})
            cur["wpm"] = max(100, min(1000, int(cur["wpm"])))
            _write_json(lib / "settings.json", cur)
        return cur

    @app.post("/api/upload")
    async def upload(file: UploadFile):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Only PDF files for now.")
        tmpdir = Path(tempfile.mkdtemp(prefix="rsvp-upload-"))
        dest = tmpdir / Path(file.filename).name
        with open(dest, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        return {"job": _start_job(dest.name, dest, tmpdir, source="upload")}

    @app.get("/api/jobs/{jid}")
    def job_status(jid: str):
        with lock:
            if jid not in jobs:
                raise HTTPException(404)
            return dict(jobs[jid], job=jid)

    # --- Inbox folder: an upload path that doesn't depend on the browser -----------------
    # Managed browsers can block file uploads and drag-and-drop. Copying PDFs into the Inbox
    # folder (File Explorer / Finder) always works; the server imports them and moves the
    # originals to Inbox/Imported.
    inbox_dir = Path(inbox) if inbox else default_inbox()
    seen_sizes: dict[str, tuple[int, float]] = {}

    def _stable(p: Path, wait: float) -> bool:
        """True once a file has stopped changing (still being copied or synced otherwise)."""
        try:
            st = p.stat()
        except OSError:
            return False
        sig = (st.st_size, st.st_mtime)
        if wait:
            time.sleep(wait)
            try:
                st2 = p.stat()
            except OSError:
                return False
            return st.st_size > 0 and sig == (st2.st_size, st2.st_mtime)
        prev = seen_sizes.get(str(p))
        seen_sizes[str(p)] = sig
        return st.st_size > 0 and prev == sig

    def scan_inbox(wait: float = 0.0) -> list[str]:
        inbox_dir.mkdir(parents=True, exist_ok=True)
        done_dir = inbox_dir / "Imported"
        started = []
        for p in sorted(x for x in inbox_dir.iterdir() if x.is_file() and x.suffix.lower() == ".pdf"):
            if not _stable(p, wait):
                continue
            tmpdir = Path(tempfile.mkdtemp(prefix="rsvp-inbox-"))
            work = tmpdir / p.name
            try:
                shutil.copy2(p, work)
                done_dir.mkdir(exist_ok=True)
                dest = done_dir / p.name
                n = 2
                while dest.exists():
                    dest = done_dir / f"{p.stem} ({n}){p.suffix}"
                    n += 1
                shutil.move(str(p), dest)
            except OSError:
                shutil.rmtree(tmpdir, ignore_errors=True)
                continue  # locked (e.g. still open elsewhere); try again next scan
            seen_sizes.pop(str(p), None)
            started.append(_start_job(p.name, work, tmpdir, source="inbox"))
        return started

    def _start_job(name: str, pdf: Path, tmpdir: Path, source: str) -> str:
        with lock:
            jid = f"job{next(job_counter)}"
            jobs[jid] = {"name": name, "status": "processing", "error": None, "doc_id": None,
                         "source": source, "started": time.time()}
        threading.Thread(target=_process, args=(jid, pdf, tmpdir), daemon=True).start()
        return jid

    @app.get("/api/inbox")
    def inbox_info():
        inbox_dir.mkdir(parents=True, exist_ok=True)
        pending = [x.name for x in inbox_dir.iterdir() if x.is_file() and x.suffix.lower() == ".pdf"]
        return {"path": str(inbox_dir), "pending": pending}

    @app.post("/api/inbox/scan")
    def inbox_scan():
        return {"jobs": scan_inbox(wait=0.5)}

    def _process(jid: str, pdf: Path, tmpdir: Path) -> None:
        from .assemble import assemble
        from .extract import extract
        from .rules import write_rules

        try:
            d = extract(pdf, library=lib, log=lambda *_: None)
            write_rules(d)
            _, _, errors = assemble(d)
            status = "error" if errors else "done"
            with lock:
                jobs[jid].update(status=status, doc_id=d.name, error="; ".join(errors) or None)
        except Exception as e:  # report, don't crash the server
            traceback.print_exc()
            with lock:
                jobs[jid].update(status="error", error=f"{type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if watch:  # started last, once everything it calls is defined
        def watcher():
            while True:
                try:
                    scan_inbox()
                except Exception:
                    traceback.print_exc()
                time.sleep(4)
        threading.Thread(target=watcher, daemon=True).start()

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def _words_before(doc: dict, prog: dict) -> int:
    n = 0
    for p in doc["body"]:
        if p["id"] == prog.get("block"):
            return n + int(prog.get("word", 0))
        n += len(p["text"].split())
    return 0


def _already_running(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url + "/api/settings", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def serve(host: str = "127.0.0.1", port: int = 8765, library: Path | None = None, open_browser: bool = False) -> None:
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}"
    if _already_running(url):
        # Double-clicking the launcher twice shouldn't fail with "port in use": just show the page.
        print(f"RSVP Reader is already running at {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return

    print(f"RSVP Reader on {url}")
    print(f"  library: {library or library_dir()}")
    print(f"  inbox:   {default_inbox()}  (copy PDFs here to add them)")
    print("  Keep this window open while you read. Close it (or press Ctrl+C) to stop the reader.", flush=True)
    if open_browser:
        def open_when_ready():
            for _ in range(60):
                if _already_running(url):
                    webbrowser.open(url)
                    return
                time.sleep(0.5)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(create_app(library, watch=True), host=host, port=port, log_level="warning")
