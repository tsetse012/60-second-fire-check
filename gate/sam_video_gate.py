# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "pillow>=10"]
# ///
"""Day-1 gate: does SAM 3.1 video keep stable track IDs on fire-safety items?

One request per concept (the API matches exactly one noun phrase per request).
Raw stream text is saved per concept before any parsing, so the summary can be
re-derived later without calling the API again (`--reparse <run_dir>`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

BASE_URL = "https://api.meta.ai/v1"
MODEL = "sam-3.1"
PRICE_PER_1K_FRAMES = 0.20  # dev.meta.ai/docs/pricing-rate-limits, checked 2026-10-28
MAX_FRAMES = 15_000
MAX_OBJECTS_PER_FRAME = 16

DEFAULT_CONCEPTS = [
    "fire extinguisher",
    "fire hose cabinet",
    "exit sign",
    "emergency light",
    "fire door",
    "sprinkler head",
    "smoke detector",
    "cardboard boxes",
    "bicycle",
    "wheelchair",
]

FRAME_RE = re.compile(r"<(\d+)f>")
RECORD_RE = re.compile(r"(\d+)<\|box;([^|]*)\|>")


def api_key() -> str:
    key = os.environ.get("META_MODEL_API_KEY") or os.environ.get("MODEL_API_KEY")
    if not key:
        sys.exit("META_MODEL_API_KEY is not set (put it in ~/.secrets/credentials.env)")
    return key


def probe(video: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=codec_name,width,height,nb_read_packets,r_frame_rate:format=duration",
         "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(out.stdout)
    s = info["streams"][0]
    return {
        "codec": s["codec_name"],
        "width": s["width"],
        "height": s["height"],
        "frames": int(s["nb_read_packets"]),
        "fps": s["r_frame_rate"],
        "duration_s": float(info["format"]["duration"]),
    }


def upload(client: httpx.Client, video: Path) -> str:
    with video.open("rb") as f:
        r = client.post("/files", data={"purpose": "user_data"},
                        files={"file": (video.name, f, "video/mp4")}, timeout=600)
    r.raise_for_status()
    return r.json()["id"]


def segment(client: httpx.Client, file_id: str, concept: str, raw_path: Path) -> dict:
    """Stream one concept; write every SSE data line to raw_path as it arrives."""
    body = {
        "model": MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_video", "file_id": file_id},
        ]}],
        "stream": True,
        "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
    }
    t0 = time.monotonic()
    first_delta = None
    status = None
    error = None
    with raw_path.open("w") as raw, client.stream("POST", "/responses", json=body, timeout=1800) as r:
        status = r.status_code
        if r.status_code != 200:
            error = r.read().decode(errors="replace")
            raw.write(error)
        else:
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                raw.write(payload + "\n")
                try:
                    kind = json.loads(payload).get("type")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if kind == "response.output_text.delta" and first_delta is None:
                    first_delta = time.monotonic() - t0
                elif kind in {"error", "response.failed", "response.incomplete"}:
                    error = payload
    return {
        "http_status": status,
        "error": error,
        "seconds_total": round(time.monotonic() - t0, 2),
        "seconds_to_first_frame": round(first_delta, 2) if first_delta else None,
    }


def stream_text(raw_path: Path) -> str:
    """Rebuild output text from saved SSE events (deltas concatenated in order)."""
    parts = []
    for line in raw_path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "response.output_text.delta":
            parts.append(ev.get("delta", ""))
    return "".join(parts)


def parse_tracks(text: str) -> dict[int, dict[int, dict]]:
    """frame -> object id -> {box, c}. Mask payloads are skipped."""
    frames: dict[int, dict[int, dict]] = {}
    # FRAME_RE.split gives [pre, f0, body0, f1, body1, ...]
    pieces = FRAME_RE.split(text)
    for i in range(1, len(pieces) - 1, 2):
        frame = int(pieces[i])
        body = pieces[i + 1]
        objs = frames.setdefault(frame, {})
        for m in RECORD_RE.finditer(body):
            kv = dict(p.split("=", 1) for p in m.group(2).split(";") if "=" in p)
            objs[int(m.group(1))] = {
                "box": [int(kv["x1"]), int(kv["y1"]), int(kv["x2"]), int(kv["y2"])],
                "c": float(kv["c"]) if "c" in kv else None,
                "wh": [int(kv["w"]), int(kv["h"])],
            }
    return frames


def track_stats(frames: dict[int, dict[int, dict]], total_frames: int) -> dict:
    ids: dict[int, list[int]] = {}
    confs: dict[int, list[float]] = {}
    for f, objs in frames.items():
        for oid, rec in objs.items():
            ids.setdefault(oid, []).append(f)
            if rec["c"] is not None:
                confs.setdefault(oid, []).append(rec["c"])
    per_id = {}
    for oid, fs in sorted(ids.items()):
        fs.sort()
        span = fs[-1] - fs[0] + 1
        gaps = [b - a - 1 for a, b in zip(fs, fs[1:])]
        cs = confs.get(oid, [])
        per_id[oid] = {
            "first": fs[0], "last": fs[-1], "frames": len(fs),
            "coverage_in_span": round(len(fs) / span, 3),
            "max_gap": max(gaps) if gaps else 0,
            "mean_c": round(sum(cs) / len(cs), 3) if cs else None,
        }
    counts = [len(o) for o in frames.values()]
    return {
        "frames_returned": len(frames),
        "frames_expected": total_frames,
        "frames_with_objects": sum(1 for c in counts if c),
        "unique_ids": len(per_id),
        "max_objects_in_frame": max(counts) if counts else 0,
        "hit_16_cap": bool(counts) and max(counts) >= MAX_OBJECTS_PER_FRAME,
        "ids": per_id,
    }


def overlay_sheet(video: Path, frames: dict[int, dict[int, dict]], out: Path, n: int = 4) -> None:
    hit = sorted(f for f, o in frames.items() if o)
    if not hit:
        return
    picks = [hit[round(i * (len(hit) - 1) / max(n - 1, 1))] for i in range(min(n, len(hit)))]
    tiles = []
    for f in dict.fromkeys(picks):
        png = out.with_suffix(f".f{f}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
                        f"select=eq(n\\,{f})", "-frames:v", "1", str(png)], check=True)
        im = Image.open(png).convert("RGB")
        d = ImageDraw.Draw(im)
        for oid, rec in frames[f].items():
            sx = im.width / rec["wh"][0]
            sy = im.height / rec["wh"][1]
            x1, y1, x2, y2 = rec["box"]
            box = [x1 * sx, y1 * sy, x2 * sx, y2 * sy]
            d.rectangle(box, outline=(255, 0, 255), width=4)
            label = f"#{oid}" + (f" {rec['c']:.2f}" if rec["c"] is not None else "")
            d.text((box[0] + 4, box[1] + 4), label, fill=(255, 0, 255))
        d.text((10, 10), f"frame {f}", fill=(255, 255, 0))
        im.thumbnail((640, 640))
        tiles.append(im)
        png.unlink()
    sheet = Image.new("RGB", (sum(t.width for t in tiles), max(t.height for t in tiles)))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width
    sheet.save(out)


def summarize(run_dir: Path, video: Path, meta: dict, calls: dict[str, dict]) -> None:
    summary = {"video": str(video), "probe": meta, "concepts": {}}
    lines = [f"# SAM 3.1 video gate — {video.name}", "",
             f"{meta['width']}x{meta['height']} {meta['codec']} · {meta['frames']} frames · "
             f"{meta['duration_s']:.1f}s", "",
             "| concept | http | s total | frames ret/exp | unique IDs | max/frame | IDs (first-last, coverage, mean c) |",
             "|---|---|---|---|---|---|---|"]
    for concept, call in calls.items():
        slug = concept.replace(" ", "_")
        raw = run_dir / f"{slug}.sse.jsonl"
        text = stream_text(raw) if raw.exists() else ""
        (run_dir / f"{slug}.txt").write_text(text)
        frames = parse_tracks(text)
        stats = track_stats(frames, meta["frames"])
        summary["concepts"][concept] = {**call, **stats}
        overlay_sheet(video, frames, run_dir / f"{slug}.sheet.jpg")
        id_txt = "; ".join(
            f"#{i} {s['first']}-{s['last']} {s['coverage_in_span']:.0%} c{s['mean_c']}"
            for i, s in list(stats["ids"].items())[:8])
        lines.append(f"| {concept} | {call.get('http_status')} | {call.get('seconds_total')} | "
                     f"{stats['frames_returned']}/{meta['frames']} | {stats['unique_ids']} | "
                     f"{stats['max_objects_in_frame']} | {id_txt} |")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video", type=Path)
    ap.add_argument("--concepts", default=",".join(DEFAULT_CONCEPTS),
                    help="comma-separated; each becomes its own request")
    ap.add_argument("--dry-run", action="store_true", help="probe + cost estimate, no API call")
    ap.add_argument("--reparse", type=Path, help="rebuild summary from an existing run dir")
    ap.add_argument("--file-id", help="reuse an uploaded file instead of uploading again")
    args = ap.parse_args()

    concepts = [c.strip() for c in args.concepts.split(",") if c.strip()]
    meta = probe(args.video)
    cost = meta["frames"] / 1000 * PRICE_PER_1K_FRAMES * len(concepts)
    print(f"{args.video.name}: {meta}")
    print(f"{len(concepts)} concepts x {meta['frames']} frames -> est. ${cost:.2f}")
    if meta["frames"] > MAX_FRAMES:
        sys.exit(f"over the {MAX_FRAMES}-frame request limit; trim the clip first")
    if meta["codec"] not in {"h264", "hevc", "mpeg4"}:
        sys.exit(f"codec {meta['codec']} not accepted (H.264/H.265/MPEG-4 only)")

    if args.reparse:
        calls = json.loads((args.reparse / "calls.json").read_text())
        summarize(args.reparse, args.video, meta, calls)
        return
    if args.dry_run:
        return

    run_dir = Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}-{args.video.stem}"
    run_dir.mkdir(parents=True)
    headers = {"Authorization": f"Bearer {api_key()}"}
    calls: dict[str, dict] = {}
    with httpx.Client(base_url=BASE_URL, headers=headers) as client:
        file_id = args.file_id or upload(client, args.video)
        print(f"file_id={file_id}")
        for concept in concepts:
            slug = concept.replace(" ", "_")
            call = segment(client, file_id, concept, run_dir / f"{slug}.sse.jsonl")
            call["file_id"] = file_id
            calls[concept] = call
            print(f"  {concept}: {call}")
            (run_dir / "calls.json").write_text(json.dumps(calls, indent=2))
    summarize(run_dir, args.video, meta, calls)


if __name__ == "__main__":
    main()
