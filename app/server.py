# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "python-multipart>=0.0.9",
#                 "httpx>=0.27", "pillow>=10"]
# ///
"""Local demo server for 60-Second Fire Check.

    set -a; . ~/.secrets/credentials.env; set +a
    uv run app/server.py                 # http://127.0.0.1:8000

Endpoints
    POST /api/jobs            multipart: `file`, or `sample` (a file in data/). `mode=reuse`
                              returns an already finished run of that sample instead of
                              re-running it (handy while recording the demo).
    GET  /api/jobs/<id>       the run's job.json, exactly as pipeline/run_all.py writes it
    GET  /api/samples         the clips in data/, with `previous` = a finished job id
    GET  /runs/<id>/...       run artifacts (report.json, overlay.mp4, crops, agent frames)
    GET  /                    the three-screen UI

The Meta API key lives only in this process's environment. Nothing under /api
returns it, and the browser never talks to api.meta.ai — only this server does.
Jobs run one at a time in a daemon thread, so two demo clicks cannot interleave
SAM requests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))
import run_all
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

RUNS = REPO / "runs"
DATA = REPO / "data"
STATIC = Path(__file__).resolve().parent / "static"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
MAX_UPLOAD_MB = 400
JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

RUNS.mkdir(exist_ok=True)
app = FastAPI(title="60-Second Fire Check", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/runs", StaticFiles(directory=RUNS), name="runs")

_queue = threading.Lock()


def key_configured() -> bool:
    return bool(os.environ.get("META_MODEL_API_KEY"))


def sample_files() -> list[Path]:
    return sorted(p for p in DATA.glob("*") if p.suffix.lower() in VIDEO_SUFFIXES)


def pretty(stem: str) -> str:
    return re.sub(r"[-_]+", " ", stem).strip().capitalize()


def previous_jobs() -> dict[str, dict]:
    """sample name -> newest finished job that still has an overlay and a report."""
    best: dict[str, dict] = {}
    for path in RUNS.glob("*/job.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        overlay = (data.get("artifacts") or {}).get("overlay")
        if data.get("status") != "done" or not overlay:
            continue
        if not (path.parent / "report.json").exists() or not (REPO / overlay).exists():
            continue
        key = data.get("sample") or ""
        if key not in best or (data.get("created_at") or "") > (best[key].get("created_at") or ""):
            best[key] = data
    return best


def new_run_dir(name: str) -> Path:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.") or "clip"
    base = f"{datetime.now():%Y%m%d-%H%M%S}-{stem}"
    run_dir, n = RUNS / base, 2
    while run_dir.exists():
        run_dir, n = RUNS / f"{base}-{n}", n + 1
    run_dir.mkdir(parents=True)
    return run_dir


def _run(job) -> None:
    with _queue:  # one job at a time: SAM quota and the API key are shared
        run_all.run_job(job)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "key_configured": key_configured()}


@app.get("/api/samples")
def samples():
    previous = previous_jobs()
    out = []
    for path in sample_files():
        try:
            meta = run_all.stage1.probe(path)
            info = {"seconds": round(meta["duration_s"], 1),
                    "width": meta["width"], "height": meta["height"]}
        except Exception:  # a broken clip should not take the list down
            info = {}
        job = previous.get(path.name)
        out.append({"name": path.name, "label": pretty(path.stem), **info,
                    "previous": job["id"] if job else None,
                    "previous_at": job.get("created_at") if job else None})
    return {"samples": out}


@app.post("/api/jobs")
def create_job(file: UploadFile | None = File(None), sample: str | None = Form(None),
               mode: str = Form("run")):
    if mode == "reuse":
        if not sample:
            raise HTTPException(400, "mode=reuse needs a sample name")
        job = previous_jobs().get(Path(sample).name)
        if not job:
            raise HTTPException(404, f"no finished run for {sample} yet — run it once first")
        return {"job_id": job["id"], "reused": True}

    if not key_configured():
        raise HTTPException(400, "META_MODEL_API_KEY is not set for this server — "
                                 "export it and restart the server")

    if file is not None and file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            raise HTTPException(400, "upload an MP4 or MOV file")
        source_name = Path(file.filename).name
        run_dir = new_run_dir(source_name)
        stored = run_dir / f"source{suffix}"
        written = 0
        with stored.open("wb") as out:
            while chunk := file.file.read(1 << 20):
                written += len(chunk)
                if written > MAX_UPLOAD_MB * 1_000_000:
                    out.close()
                    shutil.rmtree(run_dir, ignore_errors=True)
                    raise HTTPException(413, f"file is over {MAX_UPLOAD_MB} MB")
                out.write(chunk)
        if written == 0:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise HTTPException(400, "empty upload")
    elif sample:
        allowed = {p.name: p for p in sample_files()}
        if Path(sample).name not in allowed:
            raise HTTPException(404, f"unknown sample {sample!r}")
        stored = allowed[Path(sample).name]
        source_name = stored.name
        run_dir = new_run_dir(source_name)
    else:
        raise HTTPException(400, "send a video file or a sample name")

    job = run_all.create_job(run_dir, source_name, stored,
                             fps=run_all.DEFAULT_FPS, budget=run_all.DEFAULT_MAX_COST,
                             concepts=run_all.DEFAULT_CONCEPTS)
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return {"job_id": job.data["id"], "reused": False}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(400, "bad job id")
    path = RUNS / job_id / "job.json"
    if not path.exists():
        raise HTTPException(404, "no such job")
    return JSONResponse(json.loads(path.read_text()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = ap.parse_args()

    import uvicorn

    print(f"60-Second Fire Check → http://{args.host}:{args.port}")
    if not key_configured():
        print("  ! META_MODEL_API_KEY is not set — the UI loads, but jobs will be refused.")
    if not sample_files():
        print(f"  ! no clips in {DATA} — upload a video or drop an mp4 there")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()