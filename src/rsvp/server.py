"""Local web server: library page, reader, and a small JSON API over the library folder.

Everything lives in files under the library (OneDrive-synced), so the Mac and the Windows PC
see the same readings, progress and settings:
  library/settings.json            reader settings (wpm, auto-pause, recaps)
  library/<doc>/doc.json           what the reader loads
  library/<doc>/progress.json      {"block", "word", "updated_at"}; newest write wins
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
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


def create_app(library: Path | None = None) -> FastAPI:
    lib = Path(library) if library else library_dir()
    lib.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="RSVP Reader")
    jobs: dict[str, dict] = {}  # upload jobs: id -> {name, status, error, doc_id}
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
            pending = [dict(j, job=jid) for jid, j in jobs.items() if j["status"] != "done"]
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
        jid = f"job{len(jobs) + 1}"
        with lock:
            jobs[jid] = {"name": dest.name, "status": "processing", "error": None, "doc_id": None}
        threading.Thread(target=_process, args=(jid, dest, tmpdir), daemon=True).start()
        return {"job": jid}

    @app.get("/api/jobs/{jid}")
    def job_status(jid: str):
        with lock:
            if jid not in jobs:
                raise HTTPException(404)
            return dict(jobs[jid], job=jid)

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

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def _words_before(doc: dict, prog: dict) -> int:
    n = 0
    for p in doc["body"]:
        if p["id"] == prog.get("block"):
            return n + int(prog.get("word", 0))
        n += len(p["text"].split())
    return 0


def serve(host: str = "127.0.0.1", port: int = 8765, library: Path | None = None) -> None:
    import uvicorn

    print(f"RSVP Reader on http://{host}:{port}  (library: {library or library_dir()})")
    uvicorn.run(create_app(library), host=host, port=port, log_level="warning")
