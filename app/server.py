# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "python-multipart>=0.0.9",
#                 "httpx>=0.27", "pillow>=10"]
# ///
"""Demo server for 60-Second Fire Check.

    set -a; . ~/.secrets/credentials.env; set +a
    uv run app/server.py                 # http://127.0.0.1:8000

Endpoints
    POST /api/jobs            multipart: `file`, or `sample` (a file in data/). `mode=reuse`
                              returns an already finished run of that sample instead of
                              re-running it (handy while recording the demo).
    GET  /api/jobs/<id>       the run's job.json, exactly as pipeline/run_all.py writes it
    GET  /api/samples         the clips in data/, with `previous` = a finished job id
    GET  /api/health          key/public/upload flags and how many runs are left today
    GET  /runs/<id>/...       run artifacts (report.json, overlay.mp4, crops, agent frames)
    GET  /                    the three-screen UI

The Meta API key lives only in this process's environment. Nothing under /api
returns it, and the browser never talks to api.meta.ai — only this server does.
Jobs run one at a time in a daemon thread, so two demo clicks cannot interleave
SAM requests.

Public mode (`FIRECHECK_PUBLIC=1`, for the hackathon review link)
    Anyone can open the runs named in `FIRECHECK_SHOWCASE` (no model calls). A new
    upload needs the shared `UPLOAD_PASSCODE`, is refused while another video is
    processing, and is capped per day (`MAX_RUNS_PER_DAY`, Asia/Seoul) and overall
    (`MAX_RUNS_TOTAL`) so a public URL cannot spend the model budget. `/runs` stops
    being a directory listing of everything: only the files the UI actually reads are
    served, and only for showcase/uploaded runs. Without the flag nothing changes.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime, timedelta, timezone
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
QUOTA_FILE = RUNS / "_quota.json"

# what the browser may read out of runs/<id>/ in public mode — app.js asks for
# report.json and overlay.mp4, and points <img> tags at the crop and agent frames
# the job log and report trace name. input.mp4 (the unblurred original), the SSE
# streams, muse_raw/, agent_raw/ and door_state_raw/ stay on the server.
PUBLIC_FILES = {"report.json", "job.json", "overlay.mp4"}
PUBLIC_DIRS = {"crops": {".jpg", ".jpeg", ".png"},
               "agent_frames": {".jpg", ".jpeg", ".png"}}


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


PUBLIC = env_flag("FIRECHECK_PUBLIC")
SHOWCASE = [r.strip() for r in os.environ.get("FIRECHECK_SHOWCASE", "").split(",") if r.strip()]
UPLOAD_PASSCODE = os.environ.get("UPLOAD_PASSCODE", "").strip()
MAX_RUNS_PER_DAY = int(os.environ.get("MAX_RUNS_PER_DAY") or 4)
MAX_RUNS_TOTAL = int(os.environ.get("MAX_RUNS_TOTAL") or 15)

RUNS.mkdir(exist_ok=True)
app = FastAPI(title="60-Second Fire Check", docs_url=None, redoc_url=None,
              # no 307 "add a slash" dance on /runs/<id>/<path>: an unknown path is a 404
              redirect_slashes=False)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
if not PUBLIC:
    app.mount("/runs", StaticFiles(directory=RUNS), name="runs")

_queue = threading.Lock()
_pending = threading.Lock()  # held from "upload accepted" until that job finishes


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


def load_job(job_id: str) -> dict | None:
    if not JOB_ID_RE.match(job_id):
        return None
    try:
        return json.loads((RUNS / job_id / "job.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def new_run_dir(name: str) -> Path:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.") or "clip"
    base = f"{datetime.now():%Y%m%d-%H%M%S}-{stem}"
    run_dir, n = RUNS / base, 2
    while run_dir.exists():
        run_dir, n = RUNS / f"{base}-{n}", n + 1
    run_dir.mkdir(parents=True)
    return run_dir


# ---------------------------------------------------------------- quota (public mode)

def seoul_day() -> str:
    """Today's date in Asia/Seoul; a fixed +09:00 offset if tzdata is missing."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    except (ImportError, KeyError, OSError):  # no tzdata in a slim image, no zoneinfo
        return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


_quota_lock = threading.Lock()


def read_quota() -> dict:
    """`runs/_quota.json` — uploads today, uploads ever, and the run ids they made.

    Kept on disk so a container restart does not reset the budget.
    """
    try:
        data = json.loads(QUOTA_FILE.read_text())
        if not isinstance(data, dict):
            raise TypeError
    except (OSError, ValueError, TypeError):
        data = {}
    today = seoul_day()
    if data.get("day") != today:
        data["day"] = today
        data["day_count"] = 0
    data.setdefault("day_count", 0)
    data.setdefault("total", 0)
    data.setdefault("uploads", [])
    return data


def write_quota(data: dict) -> None:
    tmp = QUOTA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(QUOTA_FILE)


def runs_left_today() -> int:
    data = read_quota()
    return max(0, min(MAX_RUNS_PER_DAY - int(data.get("day_count") or 0),
                      MAX_RUNS_TOTAL - int(data.get("total") or 0)))


def take_quota_slot(run_id: str) -> str | None:
    """Count one upload; returns None on success, else the reason it was refused."""
    with _quota_lock:
        data = read_quota()
        if int(data.get("day_count") or 0) >= MAX_RUNS_PER_DAY:
            return (f"Today's limit of {MAX_RUNS_PER_DAY} "
                    f"upload{'' if MAX_RUNS_PER_DAY == 1 else 's'} is used up — "
                    f"try again tomorrow (Asia/Seoul). The sample results still open.")
        if int(data.get("total") or 0) >= MAX_RUNS_TOTAL:
            return ("This demo has used its whole run budget — the sample results "
                    "still open, but new uploads are off.")
        data["day_count"] = int(data.get("day_count") or 0) + 1
        data["total"] = int(data.get("total") or 0) + 1
        data["uploads"] = [*data.get("uploads", []), run_id]
        write_quota(data)
    return None


def uploads_enabled() -> bool:
    return (not PUBLIC) or bool(UPLOAD_PASSCODE)


def visible_run(job_id: str) -> bool:
    """Public mode serves the showcase runs and the runs uploads created, nothing else."""
    if not PUBLIC:
        return True
    return job_id in SHOWCASE or job_id in read_quota().get("uploads", [])


# ---------------------------------------------------------------- public-mode redaction

# any absolute path that ends in ".../runs/" — station paths were baked in by the
# machine that produced the run, which is not this container's REPO
RUNS_PREFIX = re.compile(r'(?<!:)(?:/[^/"\s]+)+/runs/')


def redact(value):
    """Strip this server's absolute paths from anything public mode serves."""
    if isinstance(value, str):
        return RUNS_PREFIX.sub("runs/", value).replace(f"{REPO}/", "")
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def public_job(job: dict) -> dict:
    """job.json for the browser: same shape, minus server paths and unserved artifacts."""
    out = redact(job)
    out["source"] = Path(str(job.get("source") or "")).name
    artifacts = job.get("artifacts") or {}
    out["artifacts"] = {k: redact(v) for k, v in artifacts.items()
                        if k in {"overlay", "report"} and v}
    return out


def showcase_samples() -> list[dict]:
    out = []
    for run_id in SHOWCASE:
        job = load_job(run_id)
        if not job:
            continue
        frames = (job.get("cost") or {}).get("frames")
        fps = job.get("fps")
        seconds = round(frames / fps, 1) if frames and fps else None
        item = {"name": run_id,
                "label": pretty(Path(str(job.get("sample") or run_id)).stem),
                "previous": run_id if job.get("status") == "done" else None,
                "previous_at": job.get("created_at")}
        if seconds:
            item["seconds"] = seconds
        if job.get("report_summary"):
            # the card needs the result, not the method: keep the merged-counts clause
            # ("9 ok · 2 issue · …") and drop the two-pass preamble above it
            text = " ".join(str(job["report_summary"]).split())
            if "Merged result:" in text:
                text = text.split("Merged result:", 1)[1].strip().split(". ", 1)[0]
            item["summary"] = text[:160].rstrip(" ·")
        out.append(item)
    return out


# ---------------------------------------------------------------- jobs

def _run(job, release_busy: bool = False) -> None:
    try:
        with _queue:  # one job at a time: SAM quota and the API key are shared
            run_all.run_job(job)
    finally:
        if release_busy:
            _pending.release()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "key_configured": key_configured(), "public": PUBLIC,
            "uploads_enabled": uploads_enabled(), "runs_left_today": runs_left_today()}


@app.get("/api/samples")
def samples():
    if PUBLIC:
        return {"samples": showcase_samples()}
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
               mode: str = Form("run"), passcode: str | None = Form(None)):
    if mode == "reuse":
        if not sample:
            raise HTTPException(400, "mode=reuse needs a sample name")
        if PUBLIC:
            run_id = Path(sample).name
            job = load_job(run_id) if run_id in SHOWCASE else None
            if not job or job.get("status") != "done":
                raise HTTPException(404, "no finished run for that sample")
            return {"job_id": run_id, "reused": True}
        job = previous_jobs().get(Path(sample).name)
        if not job:
            raise HTTPException(404, f"no finished run for {sample} yet — run it once first")
        return {"job_id": job["id"], "reused": True}

    if PUBLIC:
        if not uploads_enabled():
            raise HTTPException(403, "Uploads are off on this public demo — "
                                     "open one of the sample results instead.")
        if not passcode or not hmac.compare_digest(passcode, UPLOAD_PASSCODE):
            raise HTTPException(401, "That passcode didn't match. "
                                     "Use the passcode from the submission notes.")
        if sample:
            raise HTTPException(403, "Re-running a sample is off in the public demo — "
                                     "open the finished result instead.")

    if not key_configured():
        if PUBLIC:
            raise HTTPException(400, "This demo server has no model key configured, "
                                     "so it cannot run a new video.")
        raise HTTPException(400, "META_MODEL_API_KEY is not set for this server — "
                                 "export it and restart the server")

    if PUBLIC and _pending.locked():
        raise HTTPException(429, "Another video is being processed right now — "
                                 "try again in about 5 minutes.")

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

    run_id = run_dir.name
    release_busy = False
    if PUBLIC:
        # Counted only once this upload is otherwise good: a wrong passcode, a
        # missing key or a 413 must not eat one of the day's runs.
        if not _pending.acquire(blocking=False):
            shutil.rmtree(run_dir, ignore_errors=True)
            raise HTTPException(429, "Another video is being processed right now — "
                                     "try again in about 5 minutes.")
        release_busy = True
        refused = take_quota_slot(run_id)
        if refused:
            _pending.release()
            shutil.rmtree(run_dir, ignore_errors=True)
            raise HTTPException(429, refused)

    try:
        job = run_all.create_job(run_dir, source_name, stored,
                                 fps=run_all.DEFAULT_FPS, budget=run_all.DEFAULT_MAX_COST,
                                 concepts=run_all.DEFAULT_CONCEPTS)
        threading.Thread(target=_run, args=(job, release_busy), daemon=True).start()
    except Exception:
        if release_busy:
            _pending.release()
        raise
    return {"job_id": job.data["id"], "reused": False}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(400, "bad job id")
    path = RUNS / job_id / "job.json"
    if not path.exists() or not visible_run(job_id):
        raise HTTPException(404, "no such job")
    data = json.loads(path.read_text())
    return JSONResponse(public_job(data) if PUBLIC else data)


@app.get("/runs/{job_id}/{file_path:path}")
def public_run_file(job_id: str, file_path: str):
    """Allowlisted run artifacts (public mode only; local mode uses the static mount)."""
    if not PUBLIC:
        raise HTTPException(404, "not found")
    if not JOB_ID_RE.match(job_id) or not visible_run(job_id):
        raise HTTPException(404, "not found")
    run_dir = RUNS / job_id
    if not run_dir.is_dir():
        raise HTTPException(404, "not found")
    base = run_dir.resolve()
    try:
        target = (base / file_path).resolve()
        parts = target.relative_to(base).parts  # rejects .., absolute paths, symlink escapes
    except (ValueError, OSError):
        raise HTTPException(404, "not found")
    if not target.is_file():
        raise HTTPException(404, "not found")
    if len(parts) == 1:
        if parts[0] not in PUBLIC_FILES:
            raise HTTPException(404, "not found")
    elif len(parts) == 2:
        suffixes = PUBLIC_DIRS.get(parts[0])
        if not suffixes or target.suffix.lower() not in suffixes:
            raise HTTPException(404, "not found")
    else:
        raise HTTPException(404, "not found")
    if target.name == "job.json":
        return JSONResponse(public_job(json.loads(target.read_text())))
    if target.name == "report.json":
        return JSONResponse(redact(json.loads(target.read_text())))
    return FileResponse(target)


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
    if not sample_files() and not PUBLIC:
        print(f"  ! no clips in {DATA} — upload a video or drop an mp4 there")
    if PUBLIC:
        print(f"  public mode: {len(showcase_samples())} showcase run(s), "
              f"uploads {'on' if uploads_enabled() else 'off (no UPLOAD_PASSCODE)'}, "
              f"{runs_left_today()} run(s) left today")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()