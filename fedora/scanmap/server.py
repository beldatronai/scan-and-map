"""HTTP receiver for phone uploads, plus the web viewer.

  POST /api/sessions?name=<id>&process=1     body: session zip (application/zip)
  GET  /api/sessions                         list sessions with status
  GET  /api/sessions/{id}                    meta, status, alignment, available outputs
  POST /api/sessions/{id}/process?steps=colmap,align,splat,signal&mode=photogrammetry
  GET  /data/{id}/...                        session files (samples.csv, splat/splat.ply, signal/...)
  GET  /                                     viewer (viewer/dist, built with `npm run build`)
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline
from .session import Session

DATA_DIR = Path(os.environ.get("SCANMAP_DATA", Path.home() / "scanmap" / "sessions")).expanduser()
VIEWER_DIR = Path(os.environ.get("SCANMAP_VIEWER", Path(__file__).resolve().parents[2] / "viewer" / "dist"))
MAX_UPLOAD = int(os.environ.get("SCANMAP_MAX_UPLOAD_MB", "4096")) * 1024 * 1024

app = FastAPI(title="ScanMap receiver")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_jobs = ThreadPoolExecutor(max_workers=1)  # one GPU, one job at a time
_lock = threading.Lock()

SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


def _session_dir(sid: str) -> Path:
    if not SAFE.match(sid):
        raise HTTPException(400, "bad session id")
    d = DATA_DIR / sid
    if not d.is_dir():
        raise HTTPException(404, "no such session")
    return d


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract, refusing absolute paths and '..' (zip-slip). Strips one common top folder."""
    names = [n for n in zf.namelist() if not n.endswith("/")]
    tops = {n.split("/", 1)[0] for n in names}
    strip = len(tops) == 1 and all("/" in n for n in names)
    dest = dest.resolve()
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.split("/", 1)[1] if strip else info.filename
        target = (dest / name).resolve()
        if dest not in target.parents:
            raise HTTPException(400, f"unsafe path in zip: {info.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)


def _summary(d: Path) -> dict:
    s = Session(d)
    meta = s.meta
    outputs = {
        "samples": (d / "samples.csv").exists(),
        "depth_points": (d / "depth_points.ply").exists(),
        "splat": (d / "splat" / "splat.ply").exists(),
        "mesh": (d / "mesh" / "mesh.ply").exists(),
        "signal": (d / "signal" / "index.json").exists(),
    }
    align = json.loads((d / "alignment.json").read_text()) if (d / "alignment.json").exists() else None
    return {"id": d.name, "device": meta.get("device"), "duration_s": meta.get("duration_s"),
            "counts": meta.get("counts"), "status": s.read_status(), "outputs": outputs,
            "alignment": align and {k: align.get(k) for k in ("median_error_m", "quality", "inliers", "matched")}}


def _queue(d: Path, steps: list[str] | None, mode: str, matcher: str, iterations: int) -> None:
    Session(d).write_status(state="queued", error=None)
    _jobs.submit(pipeline.process, d, steps, mode, matcher, iterations)


@app.post("/api/sessions")
async def upload(request: Request, name: str = "session", process: int = 1, steps: str = "",
                 mode: str = "photogrammetry"):
    if not SAFE.match(name):
        raise HTTPException(400, "bad name")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD:
            raise HTTPException(413, "upload too large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(bytes(body)))
    except zipfile.BadZipFile:
        raise HTTPException(400, "body is not a zip")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        sid, n = name, 1
        while (DATA_DIR / sid).exists():
            n += 1
            sid = f"{name}-{n}"
        dest = DATA_DIR / sid
        dest.mkdir()
    safe_extract(zf, dest)
    try:
        Session(dest)
    except FileNotFoundError as e:
        shutil.rmtree(dest)
        raise HTTPException(400, str(e))
    if process:
        _queue(dest, [s for s in steps.split(",") if s] or None, mode, "sequential", 30000)
    return {"id": sid, "queued": bool(process), "viewer": f"/?session={sid}"}


@app.get("/api/sessions")
def list_sessions():
    if not DATA_DIR.exists():
        return []
    out = []
    for d in sorted(DATA_DIR.iterdir(), reverse=True):
        if (d / "samples.csv").exists():
            out.append(_summary(d))
    return out


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    return _summary(_session_dir(sid))


@app.post("/api/sessions/{sid}/process")
def reprocess(sid: str, steps: str = "", mode: str = "photogrammetry", matcher: str = "sequential",
              iterations: int = 30000):
    d = _session_dir(sid)
    if Session(d).read_status().get("state") in ("queued", "running"):
        raise HTTPException(409, "already processing")
    _queue(d, [s for s in steps.split(",") if s] or None, mode, matcher, iterations)
    return {"id": sid, "queued": True}


@app.get("/api/sessions/{sid}/log", response_class=PlainTextResponse)
def log(sid: str):
    p = _session_dir(sid) / "process.log"
    return p.read_text()[-50_000:] if p.exists() else ""


@app.get("/api/health")
def health():
    return {"ok": True, "data_dir": str(DATA_DIR), "runtime": shutil.which(pipeline.RUNTIME) is not None,
            "viewer": VIEWER_DIR.exists()}


DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
if VIEWER_DIR.exists():
    app.mount("/", StaticFiles(directory=VIEWER_DIR, html=True), name="viewer")
else:
    @app.get("/")
    def no_viewer():
        return JSONResponse({"message": "viewer not built: cd viewer && npm install && npm run build"})
