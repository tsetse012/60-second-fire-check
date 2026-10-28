# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "pillow>=10"]
# ///
"""Stage 2: label every SAM track with Muse Spark.

Day-1 gate (gate/RESULTS.md) showed SAM 3.1 keeps stable track IDs but does not
understand fire-specific nouns. So SAM runs with generic concepts (`red box`,
`sign`, `door`) and this stage asks Muse Spark, one track at a time, what the
tracked object actually is.

    uv run pipeline/classify_tracks.py --run runs/<run> --video data/<clip>.mp4

Per track: pick the frame where the box is largest, cut a crop (box + 30 %
margin) and a context image (whole frame with the box drawn), send both to
`muse-spark-1.3`, save the raw response JSON, then write `classified.json` /
`classified.md`. `--reparse` rebuilds both from the saved raw responses without
calling the API.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "gate"))
from sam_video_gate import parse_tracks, probe  # same parsers as the gate (import after sys.path)

BASE_URL = "https://api.meta.ai/v1"
MODEL = "muse-spark-1.3"
CROP_MARGIN = 0.30
CROP_LONG_SIDE = 768
CONTEXT_LONG_SIDE = 1024
MERGE_IOU = 0.5
MERGE_FRAME_SHARE = 0.5
CLASSIFY_WORKERS = 4  # one crop takes ~6 s of model time; 4 in flight keeps a 40-track run near 60 s
BOX_COLOR = (255, 0, 255)

CATEGORIES = [
    "fire_extinguisher", "fire_hose_cabinet", "exit_sign", "emergency_light", "fire_door",
    "smoke_detector", "sprinkler_head", "fire_alarm_device", "corridor_obstruction",
    "not_fire_equipment", "unclear",
]
CONDITIONS = ["ok", "blocked", "damaged_or_missing_part", "not_applicable", "needs_human_check"]

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "condition": {"type": "string", "enum": CONDITIONS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "string"},
    },
    "required": ["category", "condition", "confidence", "evidence"],
    "additionalProperties": False,
}

# One question on its own, after the category is known: the 17-item inspection
# loop asked one crop to be a door OR a sign OR a cabinet and flipped "closed" on
# an open leaf, but the same frame asked this single question gave open 10/10
# (gate/RESULTS.md). Two independent calls; they must agree, else unclear.
DOOR_STATE_DIR = "door_state_raw"
DOOR_STATE_CHECKS = 2
DOOR_QUESTION = ("Look at the door inside the magenta box. Is that door leaf standing open or shut "
                 "in its frame? Answer from what you see.")
DOOR_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "door_state": {"type": "string", "enum": ["open", "closed", "unclear"]},
        "evidence": {"type": "string"},
    },
    "required": ["door_state", "evidence"],
    "additionalProperties": False,
}

INSTRUCTIONS = """\
You label objects that a video segmentation model (SAM 3.1) tracked in a building
corridor video. SAM labelled this object as "{concept}". Do not trust that name:
decide only from the images.

Image 1 is a tight crop of the object (SAM's box plus 30% margin).
Image 2 is the whole frame for context, with the tracked box drawn in magenta.

Pick exactly one category:
- fire_extinguisher: portable extinguisher, or a wall cabinet whose front is plainly meant for one
- fire_hose_cabinet: cabinet / box holding a fire hose and hydrant valve (Korean 소화전함)
- exit_sign: illuminated or painted EXIT / 비상구 / running-man evacuation sign
- emergency_light: emergency / exit light fixture on the wall or ceiling
- fire_door: fire-rated door with self-closer, panic bar, or fire-door label
- smoke_detector: smoke or heat detector head on the ceiling
- sprinkler_head: sprinkler head / drop
- fire_alarm_device: manual call point, fire alarm bell, strobe, or alarm panel
- corridor_obstruction: something parked or stored in the corridor that blocks the way
  (boxes, bicycle, wheelchair, cart, pallet)
- not_fire_equipment: none of the above (air diffuser, ordinary shop door, poster, ...)
- unclear: the object cannot be told apart at this resolution

Pick exactly one condition:
- ok: the item is present, whole, and unobstructed
- blocked: the item or its access is obstructed
- damaged_or_missing_part: visibly broken, dented, or missing a part
- not_applicable: category is not_fire_equipment or unclear
- needs_human_check: present but this image cannot settle its condition

Guidance: a round perforated grille on the ceiling is an air diffuser, not a smoke
detector. A glass shop door is not a fire door. A red pillar-mounted box in a
Korean building is usually a fire hose cabinet. If the object is too small or
ambiguous, return unclear / needs_human_check rather than guessing.
`evidence` is one English sentence about what you actually see in the image.
"""


def api_key() -> str:
    key = os.environ.get("META_MODEL_API_KEY")
    if not key:
        sys.exit("META_MODEL_API_KEY is not set (set -a; . ~/.secrets/credentials.env; set +a)")
    return key


# ---------------------------------------------------------------- tracks

def load_tracks(run: Path) -> dict[tuple[str, int], dict]:
    """(slug, id) -> {frames: {frame: rec}} from the saved SAM text per concept."""
    tracks: dict[tuple[str, int], dict] = {}
    for txt in sorted(run.glob("*.txt")):
        slug = txt.stem
        frames = parse_tracks(txt.read_text())
        for f, objs in frames.items():
            for oid, rec in objs.items():
                tracks.setdefault((slug, oid), {"frames": {}})["frames"][f] = rec
    for key, tr in tracks.items():
        fs = sorted(tr["frames"])
        tr["first"], tr["last"] = fs[0], fs[-1]
        tr["rep_frame"], tr["rep_box"], tr["wh"] = max(
            ((f, tr["frames"][f]["box"], tr["frames"][f]["wh"]) for f in fs),
            key=lambda t: (t[1][2] - t[1][0]) * (t[1][3] - t[1][1]))
        tr["max_area"] = max(
            (r["box"][2] - r["box"][0]) * (r["box"][3] - r["box"][1])
            for r in tr["frames"].values())
        tr["key"] = key
    return tracks


def iou(a: list[int], b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def merge_duplicates(tracks: dict[tuple[str, int], dict]) -> dict[tuple[str, int], tuple[str, int] | None]:
    """Union tracks that sit on the same pixels, within one concept or across two.

    SAM sometimes splits one object into two IDs of the *same* concept (e.g. two
    `sign` boxes on the same panel), so the test is not concept identity but
    overlap. Two tracks are the same object when IoU > MERGE_IOU in at least
    half of the frames they share (and they share at least 3 frames, which keeps
    one-frame coincidences out). Rep of a merged group = largest max box.
    """
    keys = list(tracks)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            fa, fb = tracks[a]["frames"], tracks[b]["frames"]
            shared = fa.keys() & fb.keys()
            if len(shared) < 3:
                continue
            hits = sum(1 for f in shared if iou(fa[f]["box"], fb[f]["box"]) > MERGE_IOU)
            if hits / len(shared) >= MERGE_FRAME_SHARE:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

    groups: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)

    rep_of: dict[tuple[str, int], tuple[str, int] | None] = {}
    for members in groups.values():
        rep = max(members, key=lambda k: (tracks[k]["max_area"], k[1], k[0]))
        for m in members:
            rep_of[m] = None if m == rep else rep
    return rep_of


# ---------------------------------------------------------------- images

def extract_frames(video: Path, frames: list[int], outdir: Path) -> dict[int, Path]:
    """Pull the given frame numbers out of the video in one ffmpeg pass."""
    outdir.mkdir(parents=True, exist_ok=True)
    if not frames:
        return {}
    expr = "+".join(f"eq(n\\,{f})" for f in frames)
    pattern = outdir / "f%05d.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video),
         "-vf", f"select='{expr}'", "-fps_mode", "passthrough", "-compression_level", "1",
         str(pattern)],
        check=True, capture_output=True, text=True,
    )
    produced = sorted(outdir.glob("f*.png"))
    if len(produced) != len(frames):
        sys.exit(f"ffmpeg produced {len(produced)} frames, expected {len(frames)}")
    return dict(zip(sorted(frames), produced))


def scale_box(box: list[int], wh: list[int], im: Image.Image) -> list[int]:
    sx, sy = im.width / wh[0], im.height / wh[1]
    return [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]


def fit_long_side(im: Image.Image, long: int, max_upscale: float = 16.0) -> Image.Image:
    """Scale to `long` on the long side, up or down.

    Small tracks (a sign 57 px wide at frame 19) are unreadable to the vision
    model if they are sent as-is, so they get enlarged; the 16x ceiling keeps a
    few-pixel box from turning into pure blur.
    """
    w, h = im.size
    s = min(long / max(w, h), max_upscale)
    if s == 1:
        return im
    return im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def save_crop(im: Image.Image, box: list[float], wh: list[int], out: Path) -> None:
    x1, y1, x2, y2 = scale_box(box, wh, im)
    w, h = x2 - x1, y2 - y1
    crop = im.crop((max(0, int(x1 - w * CROP_MARGIN)), max(0, int(y1 - h * CROP_MARGIN)),
                    min(im.width, int(x2 + w * CROP_MARGIN)), min(im.height, int(y2 + h * CROP_MARGIN))))
    fit_long_side(crop, CROP_LONG_SIDE).convert("RGB").save(out, "JPEG", quality=88)


def save_context(im: Image.Image, box: list[float], wh: list[int], out: Path, label: str) -> None:
    x1, y1, x2, y2 = scale_box(box, wh, im)
    width = max(3, round(im.width / 300))
    d = ImageDraw.Draw(im)
    d.rectangle([x1, y1, x2, y2], outline=BOX_COLOR, width=width)
    d.text((x1 + width + 2, max(0, y1 - 12)), label, fill=BOX_COLOR)
    im.thumbnail((CONTEXT_LONG_SIDE, CONTEXT_LONG_SIDE), Image.LANCZOS)
    im.convert("RGB").save(out, "JPEG", quality=88)


# ---------------------------------------------------------------- muse

def data_uri(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def ask(client: httpx.Client, concept: str, crop: Path, context: Path) -> tuple[dict, dict]:
    """One classification request. Returns (raw response JSON, usage)."""
    body = {
        "model": MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": INSTRUCTIONS.format(concept=concept)},
            {"type": "input_image", "image_url": data_uri(crop)},
            {"type": "input_image", "image_url": data_uri(context)},
        ]}],
        "text": {"format": {"type": "json_schema", "name": "track_label",
                            "schema": SCHEMA, "strict": True}},
    }
    r = client.post("/responses", json=body, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
    raw = r.json()
    return raw, raw.get("usage") or {}


def ask_door_state(client: httpx.Client, context: Path) -> tuple[dict, dict]:
    """The single-question door check on the boxed full frame (no crop)."""
    body = {
        "model": MODEL,
        "temperature": 0.2,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": DOOR_QUESTION},
            {"type": "input_image", "image_url": data_uri(context)},
        ]}],
        "text": {"format": {"type": "json_schema", "name": "door_state",
                            "schema": DOOR_STATE_SCHEMA, "strict": True}},
    }
    r = client.post("/responses", json=body, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
    raw = r.json()
    return raw, raw.get("usage") or {}


def output_text(raw: dict) -> str:
    for item in raw.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part["text"]
    raise RuntimeError("no output_text in response")


def parse_label(raw: dict) -> dict:
    label = json.loads(output_text(raw))
    if label.get("category") not in CATEGORIES:
        raise RuntimeError(f"category out of enum: {label.get('category')!r}")
    if label.get("condition") not in CONDITIONS:
        raise RuntimeError(f"condition out of enum: {label.get('condition')!r}")
    return label


def parse_door_state(raw: dict) -> dict:
    stamp = json.loads(output_text(raw))
    if stamp.get("door_state") not in {"open", "closed", "unclear"}:
        raise RuntimeError(f"door_state out of enum: {stamp.get('door_state')!r}")
    return stamp


def door_state_for(key: tuple[str, int], context: Path, raw_dir: Path,
                   client: httpx.Client | None) -> dict:
    """DOOR_STATE_CHECKS independent single-question checks on one fire door.

    Every response is written to `door_state_raw/<slug>_<oid>_<k>.json` before it
    is parsed, so a re-run rebuilds the value with no calls (`client=None`). Two
    matching answers keep the answer; anything else (disagreement, a failed call)
    is `unclear`, which the inspection agent turns into needs_human_check.
    """
    slug, oid = key
    votes: list[str] = []
    evidence: list[str] = []
    errors: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    calls = 0
    for k in range(1, DOOR_STATE_CHECKS + 1):
        path = raw_dir / f"{slug}_{oid}_{k}.json"
        raw = None
        if path.exists():
            raw = json.loads(path.read_text())
        elif client is not None:
            try:
                raw, call_usage = ask_door_state(client, context)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            else:
                path.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
                calls += 1
                for kk in usage:
                    usage[kk] += call_usage.get(kk) or 0
        else:
            errors.append(f"no saved {path.name}")
        if raw is None:
            continue
        try:
            stamp = parse_door_state(raw)
        except Exception as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        votes.append(stamp["door_state"])
        evidence.append(stamp.get("evidence") or "")
    state = votes[0] if len(votes) == DOOR_STATE_CHECKS and len(set(votes)) == 1 else (
        "unclear" if votes else None)
    return {"door_state": state, "votes": votes, "evidence": evidence,
            "errors": errors, "calls": calls, "usage": usage}


# ---------------------------------------------------------------- report

def write_outputs(run: Path, video: Path, tracks: dict, rep_of: dict, labels: dict,
                  filled: dict, calls: int, usage: dict, door_states: dict | None = None,
                  door_calls: int = 0, door_usage: dict | None = None) -> dict:
    """Write classified.json/md. Returns the counts block of classified.json."""
    door_states = door_states or {}
    door_usage = door_usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    keys = sorted(tracks, key=lambda k: (tracks[k]["first"], k[0], k[1]))
    rows = []
    for k in keys:
        tr, slug, oid = tracks[k], k[0], k[1]
        merged_into = rep_of[k]
        label = labels.get(k)
        door = door_states.get(k) or {}
        rows.append({
            "track": f"{slug}_{oid}",
            "concept": slug.replace("_", " "),
            "id": oid,
            "first_frame": tr["first"],
            "last_frame": tr["last"],
            "rep_frame": tr["rep_frame"],
            "merged_into": f"{merged_into[0]}_{merged_into[1]}" if merged_into else None,
            "crop": filled.get(k, {}).get("crop"),
            "context": filled.get(k, {}).get("context"),
            "label": label,
            "door_state": door.get("door_state"),
            "door_state_votes": door.get("votes"),
            "door_state_evidence": door.get("evidence"),
            "door_state_errors": door.get("errors"),
            "error": filled.get(k, {}).get("error"),
        })

    out = {
        "video": str(video),
        "run": str(run),
        "model": MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calls_this_pass": calls,
        "usage_totals": usage,
        "door_state_calls_this_pass": door_calls,
        "door_state_usage_totals": door_usage,
        "counts": {"tracks": len(tracks),
                   "labelled": sum(1 for r in rows if r["label"]),
                   "merged": sum(1 for r in rows if r["merged_into"]),
                   "errors": sum(1 for r in rows if r["error"]),
                   "door_state_tracks": len(door_states)},
        "tracks": rows,
    }
    (run / "classified.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))

    lines = [f"# Track classification — {video.name}", "",
             f"{len(tracks)} tracks · {out['counts']['labelled']} labelled by `{MODEL}` · "
             f"{out['counts']['merged']} merged duplicates · {out['counts']['errors']} errors"
             + (f" · {len(door_states)} fire-door state check(s)" if door_states else ""), "",
             "| track | concept | frames | category | condition | conf | door state | evidence |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        span = f"{r['first_frame']}–{r['last_frame']}"
        door = ""
        if r["door_state"]:
            votes = r["door_state_votes"] or []
            door = f"{r['door_state']} ({len(votes)}/{DOOR_STATE_CHECKS})"
        if r["merged_into"]:
            lines.append(f"| {r['track']} | {r['concept']} | {span} | _merged_ | {r['merged_into']} | | | |")
            continue
        if r["error"]:
            lines.append(f"| {r['track']} | {r['concept']} | {span} | **error** | "
                         f"{r['error'][:120].replace('|', '/')} | | {door} | |")
            continue
        lb = r["label"] or {}
        lines.append(f"| {r['track']} | {r['concept']} | {span} | {lb.get('category', '')} | "
                     f"{lb.get('condition', '')} | {lb.get('confidence', '')} | {door} | "
                     f"{lb.get('evidence', '').replace('|', '/')} |")
    (run / "classified.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return out["counts"]


def classify(run: Path, video: Path, max_tracks: int = 60, *, reparse: bool = False,
             exclude_concepts: set[str] | frozenset[str] = frozenset(),
             dry_run: bool = False, on_track=None, door_state_calls: bool = False) -> dict:
    """Stage 2 over an existing run dir (the `<slug>.txt` SAM files must be there).

    `exclude_concepts` drops whole SAM concepts before anything is labelled — the
    orchestrator removes `person` so faces never reach the labelling model.
    `on_track(key, label, error)` fires once per selected track, in the same order
    as the printed lines, so a caller can stream progress. Returns the counts block
    of classified.json.

    After the labels are known, every `fire_door` track gets DOOR_STATE_CHECKS
    single-question checks on its boxed full frame (`door_state` in
    classified.json). Saved `door_state_raw/*.json` is always reused, so copying a
    run re-measures nothing; a fresh classification passes `client` and calls the
    API, `--reparse` alone never does.
    """
    tracks = load_tracks(run)
    if not tracks:
        raise FileNotFoundError(f"no <slug>.txt tracks in {run}")
    if exclude_concepts:
        tracks = {k: v for k, v in tracks.items() if k[0] not in exclude_concepts}
        if not tracks:
            raise FileNotFoundError(f"no tracks left after excluding {sorted(exclude_concepts)}")
    rep_of = merge_duplicates(tracks)
    reps = sorted([k for k in tracks if rep_of[k] is None],
                  key=lambda k: (tracks[k]["first"], k[0], k[1]))
    selected, skipped = reps[:max_tracks], reps[max_tracks:]

    print(f"{run.name}: {len(tracks)} tracks in {len({k[0] for k in tracks})} concepts · "
          f"{len(tracks) - len(reps)} merged · {len(selected)} to classify"
          + (f" · {len(skipped)} over --max-tracks={max_tracks}" if skipped else ""))
    if dry_run:
        for k in selected:
            t = tracks[k]
            print(f"  {k[0]}#{k[1]}  frames {t['first']}–{t['last']}  rep {t['rep_frame']}")
        print(f"would call the API {len(selected)} times")
        return {"tracks": len(tracks), "selected": len(selected), "would_call": len(selected)}

    meta = probe(video)
    raw_dir = run / "muse_raw"
    raw_dir.mkdir(exist_ok=True)
    labels: dict = {}
    filled: dict = {}
    usage_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    calls = 0

    if not reparse:
        crops = run / "crops"
        crops.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            frames = extract_frames(video, sorted({tracks[k]["rep_frame"] for k in selected}),
                                    Path(tmp))
            headers = {"Authorization": f"Bearer {api_key()}"}
            for k in selected:  # crops are local and fast; cut them in order first
                slug, oid, tr = k[0], k[1], tracks[k]
                crop, ctx = crops / f"{slug}_{oid}.jpg", crops / f"{slug}_{oid}_ctx.jpg"
                im = Image.open(frames[tr["rep_frame"]]).convert("RGB")
                save_crop(im, tr["rep_box"], tr["wh"], crop)
                save_context(im.copy(), tr["rep_box"], tr["wh"], ctx,
                             f"{slug} #{oid} frame {tr['rep_frame']}")
                filled[k] = {"crop": str(crop), "context": str(ctx), "error": None}

            def work(key: tuple[str, int]) -> tuple[tuple[str, int], dict | None, dict, str | None]:
                slug, oid = key
                try:
                    with httpx.Client(base_url=BASE_URL, headers=headers) as client:
                        raw, usage = ask(client, slug.replace("_", " "),
                                         crops / f"{slug}_{oid}.jpg",
                                         crops / f"{slug}_{oid}_ctx.jpg")
                except Exception as exc:
                    return key, None, {}, f"{type(exc).__name__}: {exc}"
                return key, raw, usage, None

            # Four requests in flight; map() keeps `selected` order, so the raw files
            # and the printed lines are deterministic whatever finishes first.
            results: dict[tuple[str, int], tuple] = {}
            with ThreadPoolExecutor(max_workers=CLASSIFY_WORKERS) as pool:
                for key, raw, usage, error in pool.map(work, selected):
                    results[key] = (raw, usage, error)

            for k in selected:
                slug, oid = k
                raw, usage, error = results[k]
                if error is None:
                    try:
                        (raw_dir / f"{slug}_{oid}.json").write_text(
                            json.dumps(raw, indent=2, ensure_ascii=False))
                        calls += 1
                        for kk in usage_total:
                            usage_total[kk] += usage.get(kk) or 0
                        labels[k] = parse_label(raw)
                        print(f"  {slug}#{oid} -> {labels[k]['category']} "
                              f"({labels[k]['confidence']})")
                    except Exception as exc:  # keep going; the row records the failure
                        filled[k]["error"] = f"{type(exc).__name__}: {exc}"
                if error is not None or filled[k]["error"]:
                    print(f"  {slug}#{oid} -> ERROR {(error or filled[k]['error'])[:160]}")
                    filled[k]["error"] = error or filled[k]["error"]
                if on_track:
                    on_track(k, labels.get(k), filled[k].get("error"))
    else:
        for k in selected:
            slug, oid = k
            filled[k] = {"crop": str(run / "crops" / f"{slug}_{oid}.jpg"),
                         "context": str(run / "crops" / f"{slug}_{oid}_ctx.jpg"),
                         "error": None}
            raw_path = raw_dir / f"{slug}_{oid}.json"
            if not raw_path.exists():
                filled[k]["error"] = "missing muse_raw file"
                if on_track:
                    on_track(k, None, filled[k]["error"])
                continue
            try:
                raw = json.loads(raw_path.read_text())
                labels[k] = parse_label(raw)
                for kk in usage_total:  # usage lives inside the saved response
                    usage_total[kk] += (raw.get("usage") or {}).get(kk) or 0
            except Exception as exc:
                filled[k]["error"] = f"{type(exc).__name__}: {exc}"
            if on_track:
                on_track(k, labels.get(k), filled[k].get("error"))

    for k in skipped:
        filled[k] = {"crop": None, "context": None,
                     "error": f"skipped: over --max-tracks={max_tracks}"}

    # Door state, one question at a time, once the category is a fact. Saved raw
    # responses win over calling again (a copied run reuses what was measured).
    door_targets = [k for k in selected if (labels.get(k) or {}).get("category") == "fire_door"]
    door_states: dict = {}
    door_calls = 0
    door_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if door_targets:
        door_dir = run / DOOR_STATE_DIR
        door_dir.mkdir(exist_ok=True)
        allow_calls = door_state_calls or not reparse
        with httpx.Client(base_url=BASE_URL,
                          headers={"Authorization": f"Bearer {api_key()}"} if allow_calls else {}) as client:
            for k in door_targets:
                context = Path(filled[k]["context"])
                got = door_state_for(k, context, door_dir, client if allow_calls else None)
                door_states[k] = got
                door_calls += got["calls"]
                for kk in door_usage:
                    door_usage[kk] += got["usage"].get(kk) or 0
                print(f"  door state {k[0]}#{k[1]} -> {got['door_state']} "
                      f"({len(got['votes'])}/{DOOR_STATE_CHECKS} agree)"
                      + (f" · {got['errors'][0][:120]}" if got["errors"] else ""))

    print(f"probe: {meta['width']}x{meta['height']} {meta['frames']} frames")
    return write_outputs(run, video, tracks, rep_of, labels, filled, calls, usage_total,
                         door_states, door_calls, door_usage)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True, help="gate run dir with <slug>.txt files")
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--max-tracks", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true", help="list tracks and call count, no API")
    ap.add_argument("--reparse", action="store_true", help="rebuild from <run>/muse_raw, no API")
    ap.add_argument("--exclude-concepts", default="",
                    help="comma-separated SAM concepts to drop before labelling (the orchestrator drops "
                         "`person`; pass it here too when you re-label by hand)")
    ap.add_argument("--door-state", action="store_true",
                    help="ask the single-question door checks for fire_door tracks (automatic on a "
                         "fresh classification; with --reparse it only calls for missing door_state_raw)")
    args = ap.parse_args()

    try:
        excluded = frozenset(s.strip() for s in args.exclude_concepts.split(",") if s.strip())
        classify(args.run, args.video, args.max_tracks, reparse=args.reparse,
                 exclude_concepts=excluded, dry_run=args.dry_run,
                 door_state_calls=args.door_state)
    except FileNotFoundError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()