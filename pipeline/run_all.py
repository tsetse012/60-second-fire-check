# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "pillow>=10"]
# ///
"""Stage 4: one video in, one inspected run out.

Wraps the three existing stages behind a single call:

    preprocess (ffmpeg -> H.264, long side 1920, 10 fps, max 3 min)
      -> SAM 3.1 tracking      (gate/sam_video_gate.py, concepts in parallel)
      -> track labelling       (pipeline/classify_tracks.py, `person` removed)
      -> inspection agent      (pipeline/inspect_agent.py)
      -> overlay.mp4           (boxes coloured by label, people blurred)

The preprocessed file is the one common timeline: SAM frame numbers, the crops
the agent re-cuts and the overlay all index into `input.mp4`, so a report
timestamp maps to the same second in the video the demo plays.

    uv run pipeline/run_all.py data/hospital-corridor-static.mp4
    uv run pipeline/run_all.py clip.mov --fps 10 --max-cost 3 --lang en

`runs/<id>/job.json` carries per-stage status, timings, the cost estimate and a
running log of what happened — that file is what `app/server.py` streams to the
browser, so the UI never has to parse stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "gate"))
sys.path.insert(0, str(REPO / "pipeline"))
import classify_tracks as stage2
import inspect_agent as stage3
import sam_video_gate as stage1

# Generic concepts only — gate/RESULTS.md showed fire-specific nouns miss on this
# footage, so stage 2 decides what each track actually is.
DEFAULT_CONCEPTS = [
    "sign", "red box", "fire extinguisher", "door", "wheelchair",
    "bicycle", "cardboard boxes", "cart", "person",
]
EXCLUDE_FROM_CLASSIFY = frozenset({"person"})  # privacy: never sent to the labelling model
DEFAULT_FPS = 10.0
MAX_SECONDS = 180.0
TARGET_LONG_SIDE = 1920  # 1280 shrank a far EXIT sign to 24x14 px (needs_human_check); SAM bills per frame, not pixel
DEFAULT_MAX_COST = 3.0
SAM_WORKERS = 4
PRICE_PER_1K_FRAMES = stage1.PRICE_PER_1K_FRAMES

# overlay colours: fire equipment green, violations red, check-by-hand amber, rest grey
COLOR_FIRE = (26, 140, 78)
COLOR_ISSUE = (200, 46, 38)
COLOR_CHECK = (206, 130, 22)
COLOR_OTHER = (118, 124, 132)
FIRE_CATEGORIES = {
    "fire_extinguisher", "fire_hose_cabinet", "exit_sign", "emergency_light",
    "fire_door", "smoke_detector", "sprinkler_head", "fire_alarm_device",
}
SHORT = {
    "fire_extinguisher": "extinguisher",
    "fire_hose_cabinet": "hose cabinet",
    "exit_sign": "exit sign",
    "emergency_light": "emergency light",
    "fire_door": "fire door",
    "smoke_detector": "smoke detector",
    "sprinkler_head": "sprinkler",
    "fire_alarm_device": "alarm device",
    "corridor_obstruction": "obstruction",
    "not_fire_equipment": "not fire equipment",
    "unclear": "unclear",
}


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path | str) -> str:
    """Path relative to the repo root (what the web server serves), else as-is."""
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except (ValueError, OSError):
        return str(path)


def api_key() -> str:
    key = os.environ.get("META_MODEL_API_KEY")
    if not key:
        raise RuntimeError("META_MODEL_API_KEY is not set (set -a; . ~/.secrets/credentials.env; set +a)")
    return key


# ---------------------------------------------------------------- job state

class Job:
    """`runs/<id>/job.json` — the single source of truth for the demo server."""

    STAGES = ("preprocess", "sam", "classify", "inspect", "overlay")

    def __init__(self, run_dir: Path, sample: str, source: Path, fps: float, budget: float,
                 concepts: list[str], status: str = "queued"):
        self.dir = Path(run_dir).resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "job.json"
        self.source = Path(source)
        self.lock = threading.RLock()
        self._stage_t0: dict[str, float] = {}
        self.data = {
            "id": self.dir.name,
            "created_at": iso_now(),
            "status": status,
            "sample": sample,
            "source": rel(source),
            "fps": fps,
            "concepts": list(concepts),
            "reused": False,
            "stages": {k: {"status": "pending", "started_at": None, "ended_at": None,
                           "seconds": None, "detail": ""} for k in self.STAGES},
            "cost": {"budget_usd": budget, "sam_estimate_usd": None, "sam_actual_usd": None,
                     "frames": None},
            "counts": {},
            "report_summary": "",
            "log": [],
            "error": None,
            "artifacts": {},
        }
        self.save()

    def save(self) -> None:
        with self.lock:
            payload = json.dumps(self.data, indent=2, ensure_ascii=False)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(payload)
        tmp.replace(self.path)

    def stage(self, name: str, status: str | None = None, detail: str | None = None) -> None:
        with self.lock:
            st = self.data["stages"][name]
            if status and status != st["status"]:
                if status == "running":
                    self._stage_t0[name] = time.monotonic()
                    st["started_at"] = iso_now()
                if status in {"done", "failed", "skipped"}:
                    st["ended_at"] = iso_now()
                    t0 = self._stage_t0.pop(name, None)
                    if t0:
                        st["seconds"] = round(time.monotonic() - t0, 1)
                st["status"] = status
            if detail is not None:
                st["detail"] = detail
        self.save()

    def log(self, stage: str, kind: str, text: str, image: str | None = None, **extra) -> None:
        entry = {"at": now(), "stage": stage, "kind": kind, "text": text}
        if image:
            entry["image"] = image
        entry.update(extra)
        with self.lock:
            self.data["log"].append(entry)
        self.save()


def create_job(run_dir: Path, sample: str, source: Path, *, fps: float = DEFAULT_FPS,
               budget: float = DEFAULT_MAX_COST, concepts: list[str] | None = None,
               status: str = "queued") -> Job:
    return Job(run_dir, sample, source, fps, budget, concepts or DEFAULT_CONCEPTS, status=status)


# ---------------------------------------------------------------- preprocess

def preprocess(src: Path, dst: Path, fps: float) -> dict:
    """Re-encode to H.264 at 1920 px long side and `fps`; returns the new probe.

    Everything downstream indexes this file, so the frame numbers the SAM model
    reports are the frame numbers the overlay draws on and the report cites.
    """
    try:
        meta = stage1.probe(src)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"{src.name} could not be read as a video — ffprobe said: "
                         f"{(exc.stderr or '').strip().splitlines()[-1][:120]}") from exc
    if meta["duration_s"] > MAX_SECONDS + 0.5:
        raise ValueError(f"{src.name} is {meta['duration_s']:.0f} s — the walk must fit in "
                         f"{MAX_SECONDS / 60:.0f} minutes; trim it first")
    if meta["codec"] not in {"h264", "hevc", "mpeg4"}:
        raise ValueError(f"codec {meta['codec']} not accepted (H.264/H.265/MPEG-4 only)")
    # Size the long side after ffmpeg applies the rotation flag (phone videos are
    # stored landscape with a 90-degree flag); probe width/height are pre-rotation.
    long_side = min(TARGET_LONG_SIDE, max(meta["width"], meta["height"]))
    scale = (f"scale='if(gte(iw,ih),{long_side},-2)':'if(gte(iw,ih),-2,{long_side})'")
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-vf", f"fps={fps:g},{scale}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "veryfast",
         "-movflags", "+faststart", "-an", str(dst)],
        check=True, capture_output=True, text=True,
    )
    return stage1.probe(dst)


def estimate_cost(frames: int, concepts: int) -> float:
    return round(frames / 1000 * PRICE_PER_1K_FRAMES * concepts, 3)


# ---------------------------------------------------------------- SAM

def run_sam(job: Job, video: Path, run_dir: Path, concepts: list[str], workers: int) -> dict:
    headers = {"Authorization": f"Bearer {api_key()}"}
    with httpx.Client(base_url=stage1.BASE_URL, headers=headers) as client:
        file_id = stage1.upload(client, video)
    job.log("sam", "info", f"uploaded {video.name} — {len(concepts)} concepts, {workers} in parallel")

    def one(concept: str) -> tuple[str, dict]:
        slug = concept.replace(" ", "_")
        try:
            with httpx.Client(base_url=stage1.BASE_URL, headers=headers) as client:
                call = stage1.segment(client, file_id, concept, run_dir / f"{slug}.sse.jsonl")
        except Exception as exc:  # one broken concept must not sink the run
            call = {"http_status": None, "error": f"{type(exc).__name__}: {exc}",
                    "seconds_total": None, "seconds_to_first_frame": None}
        call["file_id"] = file_id
        return concept, call

    calls: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(concepts)))) as pool:
        futures = [pool.submit(one, c) for c in concepts]
        for done, fut in enumerate(as_completed(futures), 1):
            concept, call = fut.result()
            calls[concept] = call
            if call.get("http_status") == 200:
                job.log("sam", "concept", f"{concept} — {call.get('seconds_total')} s")
            else:
                job.log("sam", "error", f"{concept} — HTTP {call.get('http_status')}: "
                                        f"{str(call.get('error'))[:160]}")
            job.stage("sam", "running", f"{done}/{len(concepts)} concepts")
            (run_dir / "calls.json").write_text(json.dumps(calls, indent=2))
    return calls


# ---------------------------------------------------------------- overlay

def track_concept(track_id: str) -> str:
    return track_id.rsplit("_", 1)[0]


def style_of(category: str, condition: str) -> tuple[tuple[int, int, int], str]:
    name = SHORT.get(category, category.replace("_", " "))
    if category == "corridor_obstruction" or condition in {"blocked", "damaged_or_missing_part"}:
        return COLOR_ISSUE, name
    if condition == "needs_human_check":
        return COLOR_CHECK, name
    if category in FIRE_CATEGORIES:
        return COLOR_FIRE, name
    return COLOR_OTHER, name


def load_styles(run_dir: Path) -> tuple[dict[str, tuple], dict[str, str]]:
    styles: dict[str, tuple] = {}
    merged: dict[str, str] = {}
    path = run_dir / "classified.json"
    if not path.exists():
        return styles, merged
    for row in json.loads(path.read_text())["tracks"]:
        if row.get("merged_into"):
            merged[row["track"]] = row["merged_into"]
            continue
        label = row.get("label") or {}
        styles[row["track"]] = style_of(label.get("category") or "unclear",
                                        label.get("condition") or "")
    return styles, merged


def overlay_font(size: int = 26) -> ImageFont.ImageFont:
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def scaled_box(rec: dict, im: Image.Image) -> tuple[int, int, int, int]:
    sx, sy = im.width / rec["wh"][0], im.height / rec["wh"][1]
    x1, y1, x2, y2 = rec["box"]
    return int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy)


def blur_region(im: Image.Image, rec: dict) -> Image.Image:
    """Gaussian-blur one tracked box in place (people never leave this machine)."""
    x1, y1, x2, y2 = scaled_box(rec, im)
    pad_x, pad_y = (x2 - x1) * 0.06, (y2 - y1) * 0.06
    x1, y1 = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y))
    x2, y2 = min(im.width, int(x2 + pad_x)), min(im.height, int(y2 + pad_y))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return im
    radius = max(6.0, min(x2 - x1, y2 - y1) / 7)
    im.paste(im.crop((x1, y1, x2, y2)).filter(ImageFilter.GaussianBlur(radius)), (x1, y1))
    return im


def label_rect(x1: float, ty: float, tw: float, th: int) -> tuple[int, int, int, int]:
    return int(x1), int(ty), int(x1 + tw + 10), int(ty + th)


def overlaps(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def draw_track(draw: ImageDraw.ImageDraw, im: Image.Image, rec: dict, text: str,
               color: tuple[int, int, int], font, placed: list | None = None) -> None:
    x1, y1, x2, y2 = scaled_box(rec, im)
    draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
    tw = draw.textlength(text, font=font)
    th = font.size + 6
    ty = y1 - th - 2 if y1 - th - 2 >= 0 else y1 + 2
    if placed is not None:  # two tracks sitting next to each other would stack their pills
        for _ in range(6):
            if not any(overlaps(label_rect(x1, ty, tw, th), r) for r in placed):
                break
            if ty + 2 * th + 2 > im.height - th:
                break
            ty += th + 2
        placed.append(label_rect(x1, ty, tw, th))
    draw.rectangle([x1, ty, x1 + tw + 10, ty + th], fill=color)
    draw.text((x1 + 5, ty + 3), text, fill=(255, 255, 255), font=font)


def render_overlay(job: Job, run_dir: Path, video: Path, fps: float) -> dict:
    """Draw every track on every frame; people get a blur instead of a box."""
    idx = stage3.frame_index(run_dir)  # track id -> {frame: {box, wh}}, read from the SAM text
    styles, merged = load_styles(run_dir)
    out = run_dir / "overlay.mp4"
    with tempfile.TemporaryDirectory(prefix="overlay-") as tmp_name:
        tmp = Path(tmp_name)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
                        str(tmp / "f%05d.png")], check=True)
        pngs = sorted(tmp.glob("f*.png"))
        font = overlay_font()
        for n, png in enumerate(pngs, 1):
            frame = int(png.stem[1:]) - 1
            im = Image.open(png).convert("RGB")
            for tid, frames in idx.items():
                if track_concept(tid) != "person":
                    continue
                rec = frames.get(frame)
                if rec:
                    im = blur_region(im, rec)
            draw = ImageDraw.Draw(im)
            visible = []
            for tid, frames in idx.items():
                if track_concept(tid) == "person":
                    continue
                rec = frames.get(frame)
                if rec:
                    visible.append((tid, rec))
            # big boxes first so the small ones' pills get nudged, not the other way round
            visible.sort(key=lambda it: (it[1]["box"][2] - it[1]["box"][0])
                         * (it[1]["box"][3] - it[1]["box"][1]), reverse=True)
            placed: list = []
            for tid, rec in visible:
                color, name = styles.get(tid) or styles.get(merged.get(tid)) or (COLOR_OTHER, "unclassified")
                draw_track(draw, im, rec, name, color, font, placed)
            im.save(png)
            if n % 25 == 0:
                job.stage("overlay", "running", f"{n}/{len(pngs)} frames")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-framerate", f"{fps:g}", "-i", str(tmp / "f%05d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "veryfast",
             "-movflags", "+faststart", str(out)],
            check=True)
    return {"frames": len(pngs), "seconds": round(len(pngs) / fps, 1),
            "mb": out.stat().st_size / 1e6}


# ---------------------------------------------------------------- job

def describe_tool(step: dict) -> str:
    name = step.get("tool") or "?"
    args = step.get("args") or {}
    if name == "view_track":
        extra = f", frame {args['frame']}" if args.get("frame") is not None else ""
        return f"view_track({args.get('track_id', '?')}{extra})"
    if name == "list_tracks":
        return f"list_tracks({args.get('category') or ''})"
    if name == "get_rule":
        return f"get_rule({args.get('rule_id', '?')})"
    if name == "submit_report":
        if step.get("rejected"):
            return "submit_report — rejected, retrying"
        return "submit_report — checklist filed"
    return name


def run_job(job: Job, *, ruleset: Path | None = None, lang: str = "en",
            max_steps: int | None = None, max_tracks: int = 60, overlay: bool = True,
            sam_workers: int = SAM_WORKERS) -> dict:
    """Run every stage for one job. Never raises: failures land in job.json."""
    ruleset = Path(ruleset or REPO / "rulesets" / "kr-fire.json")
    max_steps = max_steps or stage3.DEFAULT_MAX_STEPS
    run_dir, source = job.dir, job.source
    try:
        api_key()  # fail before the first byte of work if the key is missing
        job.data["status"] = "running"
        job.save()

        # 1. preprocess ------------------------------------------------------
        job.stage("preprocess", "running", f"encoding H.264, {job.data['fps']:g} fps")
        work = run_dir / "input.mp4"
        meta = preprocess(source, work, job.data["fps"])
        frames = meta["frames"]
        est = estimate_cost(frames, len(job.data["concepts"]))
        job.data["cost"]["frames"] = frames
        job.data["cost"]["sam_estimate_usd"] = est
        if est > job.data["cost"]["budget_usd"]:
            raise RuntimeError(f"estimated SAM cost ${est:.2f} is over the "
                               f"${job.data['cost']['budget_usd']:.2f} budget (--max-cost)")
        job.stage("preprocess", "done",
                  f"{meta['width']}x{meta['height']} · {frames} frames · {frames / job.data['fps']:.1f} s")
        job.log("preprocess", "info",
                f"{source.name} → {meta['width']}x{meta['height']} H.264 at {job.data['fps']:g} fps, "
                f"{frames} frames")
        job.log("preprocess", "info",
                f"{len(job.data['concepts'])} concepts x {frames} frames ≈ ${est:.2f} of SAM")

        # 2. SAM tracking ----------------------------------------------------
        job.stage("sam", "running", f"0/{len(job.data['concepts'])} concepts")
        calls = run_sam(job, work, run_dir, job.data["concepts"], sam_workers)
        if all(c.get("http_status") != 200 for c in calls.values()):
            raise RuntimeError(f"every SAM request failed: {calls}")
        stage1.summarize(run_dir, work, meta, calls)
        summary = json.loads((run_dir / "summary.json").read_text())
        billed = sum(c.get("frames_returned") or 0 for c in summary["concepts"].values())
        job.data["cost"]["sam_actual_usd"] = round(billed / 1000 * PRICE_PER_1K_FRAMES, 3)
        tracks_total = sum(c.get("unique_ids") or 0 for c in summary["concepts"].values())
        failed = [concept for concept, c in calls.items() if c.get("http_status") != 200]
        job.stage("sam", "done",
                  f"{len(calls)} concepts · {tracks_total} tracks · "
                  f"${job.data['cost']['sam_actual_usd']:.2f} of SAM"
                  + (f" · {len(failed)} concept(s) failed" if failed else ""))
        job.log("sam", "info",
                f"{tracks_total} track IDs across {len({k for k in calls})} concepts, "
                f"${job.data['cost']['sam_actual_usd']:.2f} of frames billed")

        # 3. labelling -------------------------------------------------------
        job.stage("classify", "running", "labelling tracks")

        def on_track(key, label, error) -> None:
            slug, oid = key
            crop = run_dir / "crops" / f"{slug}_{oid}.jpg"
            image = rel(crop) if crop.exists() else None
            if error:
                job.log("classify", "track", f"{slug}_{oid} — {error[:120]}", image=image)
            else:
                job.log("classify", "track",
                        f"{slug}_{oid} → {label['category']} · {label['condition']} "
                        f"({label['confidence']:.2f})", image=image)

        counts = stage2.classify(run_dir, work, max_tracks=max_tracks,
                                 exclude_concepts=EXCLUDE_FROM_CLASSIFY, on_track=on_track)
        job.data["counts"] = counts
        job.stage("classify", "done",
                  f"{counts['labelled']} labelled · {counts['merged']} merged · {counts['errors']} errors")

        # 4. inspection agent ------------------------------------------------
        job.stage("inspect", "running", "agent starting")

        def on_step(step: int, executed: list[dict]) -> None:
            for entry in executed:
                images = entry.get("images") or []
                job.log("inspect", "tool", describe_tool(entry),
                        image=rel(images[0]) if images else None)
            job.stage("inspect", "running", f"step {step}/{max_steps}")

        rep = stage3.inspect(run_dir, work, ruleset=ruleset, lang=lang,
                             max_steps=max_steps, on_step=on_step)
        job.data["counts"] = rep["counts"]
        job.data["report_summary"] = rep.get("summary") or ""
        items = sum(rep["counts"].values())
        job.stage("inspect", "done",
                  f"{items} items · " + " · ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items())))
        if rep["partial"]:
            raise RuntimeError(f"the agent stopped before submit_report ({rep['stop_reason']})")

        # 5. overlay video ---------------------------------------------------
        if overlay:
            job.stage("overlay", "running", "drawing boxes")
            info = render_overlay(job, run_dir, work, job.data["fps"])
            job.stage("overlay", "done",
                      f"{info['frames']} frames · {info['seconds']} s · {info['mb']:.1f} MB")
        else:
            job.stage("overlay", "skipped", "disabled")

        job.data["artifacts"] = {
            "video": rel(work),
            "overlay": rel(run_dir / "overlay.mp4") if overlay else None,
            "report": rel(run_dir / "report.json"),
            "email": rel(run_dir / "email.md"),
            "classified": rel(run_dir / "classified.json"),
        }
        job.data["status"] = "done"
        job.save()
        return job.data

    except (Exception, SystemExit) as exc:
        message = f"{type(exc).__name__}: {exc}"
        job.data["status"] = "failed"
        job.data["error"] = message
        for name, st in job.data["stages"].items():
            if st["status"] == "running":
                st["status"] = "failed"
                st["ended_at"] = iso_now()
                t0 = job._stage_t0.pop(name, None)
                if t0:
                    st["seconds"] = round(time.monotonic() - t0, 1)
        job.log("job", "error", message)
        return job.data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--run", type=Path, help="run dir (default runs/<timestamp>-<stem>)")
    ap.add_argument("--fps", type=float, default=DEFAULT_FPS)
    ap.add_argument("--concepts", default=",".join(DEFAULT_CONCEPTS),
                    help="comma-separated; one SAM request per concept")
    ap.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST,
                    help="stop before SAM if the estimate is over this many dollars")
    ap.add_argument("--max-tracks", type=int, default=60)
    ap.add_argument("--max-steps", type=int, default=stage3.DEFAULT_MAX_STEPS)
    ap.add_argument("--ruleset", type=Path, default=REPO / "rulesets" / "kr-fire.json")
    ap.add_argument("--lang", choices=["en", "ko"], default="en")
    ap.add_argument("--no-overlay", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="preprocess + cost estimate only, no API call")
    args = ap.parse_args()

    src = args.video.expanduser().resolve()
    if not src.exists():
        sys.exit(f"no such video: {src}")
    concepts = [c.strip() for c in args.concepts.split(",") if c.strip()]

    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="firecheck-") as tmp:
            meta = preprocess(src, Path(tmp) / "input.mp4", args.fps)
        print(f"{src.name}: {meta['width']}x{meta['height']} {meta['codec']} · "
              f"{meta['frames']} frames at {args.fps:g} fps · {meta['frames'] / args.fps:.1f} s")
        print(f"{len(concepts)} concepts x {meta['frames']} frames -> "
              f"est. ${estimate_cost(meta['frames'], len(concepts)):.2f} "
              f"(limit ${args.max_cost:.2f})")
        return

    run_dir = args.run or Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}-{src.stem}"
    job = create_job(run_dir, src.name, src, fps=args.fps, budget=args.max_cost,
                     concepts=concepts)
    data = run_job(job, ruleset=args.ruleset, lang=args.lang, max_steps=args.max_steps,
                   max_tracks=args.max_tracks, overlay=not args.no_overlay)
    print(f"-> {job.dir} · {data['status']}"
          + (f" · {data['error']}" if data["error"] else ""))
    if data["status"] != "done":
        sys.exit(1)


if __name__ == "__main__":
    main()