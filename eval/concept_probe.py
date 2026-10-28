# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "pillow>=10"]
# ///
"""Concept probe: which SAM concept name catches a thing the defaults miss?

    uv run eval/concept_probe.py --video runs/<run>/input.mp4 --out runs/<probe> \\
        --segments 0-99,440-719,1240-1359 \\
        --concepts "wall cabinet,metal cabinet,standing sign"

One request per (concept x segment), nothing else: no tracking stitch, no labels.
The point is a sheet you can open to see whether the object you care about got a
box, so the frames you care about are rendered too (`--check-frames`), not just a
sample of the hits. Raw SSE streams are kept, so `--reparse` re-reads the sheets
without paying again.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "gate"))
sys.path.insert(0, str(REPO / "pipeline"))
import sam_video_gate as stage1
from run_all import cut_window  # frame-accurate cut, same one the pipeline uses


def parse_segments(text: str) -> list[tuple[int, int]]:
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        out.append((int(a), int(b)))
    return out


def slug_of(concept: str) -> str:
    return concept.replace(" ", "_")


def sheet_at(video: Path, frames: dict[int, dict[int, dict]], picks: list[int],
             out: Path, label: str) -> None:
    """Render the given frames (a box-less frame still shows, with 'no box')."""
    import subprocess
    tiles = []
    for f in picks:
        png = out.with_suffix(f".f{f}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
                        f"select=eq(n\\,{f})", "-frames:v", "1", str(png)], check=True)
        im = Image.open(png).convert("RGB")
        d = ImageDraw.Draw(im)
        objs = frames.get(f) or {}
        for oid, rec in objs.items():
            sx, sy = im.width / rec["wh"][0], im.height / rec["wh"][1]
            x1, y1, x2, y2 = rec["box"]
            box = [x1 * sx, y1 * sy, x2 * sx, y2 * sy]
            d.rectangle(box, outline=(255, 0, 255), width=5)
            d.text((box[0] + 6, box[1] + 6), f"#{oid}", fill=(255, 0, 255))
        d.text((10, 10), f"{label} frame {f} · " + (f"{len(objs)} box(es)" if objs else "no box"),
               fill=(255, 255, 0))
        im.thumbnail((420, 420))
        tiles.append(im)
        png.unlink()
    sheet = Image.new("RGB", (sum(t.width for t in tiles), max(t.height for t in tiles)))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width
    sheet.save(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, required=True, help="preprocessed input.mp4 of a run")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--segments", default="0-99,440-719,1240-1359")
    ap.add_argument("--concepts", required=True)
    ap.add_argument("--check-frames", default="480,500,640,700,0,20,1260,1300,1340")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--reparse", action="store_true", help="rebuild from saved SSE, no calls")
    args = ap.parse_args()

    segments = parse_segments(args.segments)
    concepts = [c.strip() for c in args.concepts.split(",") if c.strip()]
    checks = [int(f) for f in args.check_frames.split(",") if f.strip()]
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    meta = stage1.probe(args.video)
    num, _, den = str(meta["fps"]).partition("/")
    fps = float(num) / float(den or 1)
    print(f"{args.video.name}: {meta['frames']} frames · {len(segments)} segment(s) × "
          f"{len(concepts)} concept(s) = {len(segments) * len(concepts)} request(s)")

    windows = []
    for k, (start, end) in enumerate(segments):
        path = out / f"seg{k:02d}_{start}-{end}.mp4"
        if args.reparse and path.exists():
            frames = end - start + 1
        else:
            frames = cut_window(args.video, start, end, path, fps)
        windows.append({"index": k, "start": start, "end": end, "path": path, "frames": frames})
    print(", ".join(f"s{w['index']}: frames {w['start']}-{w['end']} ({w['frames']}f)" for w in windows))

    file_ids: dict[int, str] = {}
    if not args.reparse:
        headers = {"Authorization": f"Bearer {stage1.api_key()}"}
        with httpx.Client(base_url=stage1.BASE_URL, headers=headers) as client:
            for w in windows:
                file_ids[w["index"]] = stage1.upload(client, w["path"])
        print(f"{len(file_ids)} segment(s) uploaded")

        def one(w: dict, concept: str) -> tuple[str, str]:
            slug = slug_of(concept)
            raw = out / f"{slug}.s{w['index']:02d}.sse.jsonl"
            with httpx.Client(base_url=stage1.BASE_URL, headers=headers) as client:
                call = stage1.segment(client, file_ids[w["index"]], concept, raw)
            return (f"{concept} s{w['index']}", f"HTTP {call['http_status']} "
                    f"{call['frames_processed']} frames")

        jobs = [(w, c) for c in concepts for w in windows]
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for msg in pool.map(lambda jc: one(*jc), jobs):
                print(f"  {msg[0]}: {msg[1]}")

    rows = []
    by_concept: dict[str, dict[int, dict[int, dict]]] = {}
    for concept in concepts:
        slug = slug_of(concept)
        per_seg = {}
        for w in windows:
            raw = out / f"{slug}.s{w['index']:02d}.sse.jsonl"
            local = stage1.parse_tracks(stage1.stream_text(raw)) if raw.exists() else {}
            per_seg[w["index"]] = local
            stats = stage1.track_stats(local, w["frames"])
            spans = ", ".join(f"#{i} {w['start'] + s['first']}-{w['start'] + s['last']}"
                              for i, s in list(stats["ids"].items())[:6])
            rows.append((concept, w["index"], w["start"], w["end"], stats["unique_ids"],
                         stats["frames_with_objects"], spans))
            if local:
                stage1.overlay_sheet(w["path"], local, out / f"sheet-{slug}-s{w['index']:02d}.jpg")
        by_concept[concept] = per_seg

    # The frames that matter: a box there means the name catches the object.
    check_rows = []
    for concept in concepts:
        slug = slug_of(concept)
        hits = []
        picks_by_seg: dict[int, list[int]] = {}
        for f in checks:
            w = next((w for w in windows if w["start"] <= f <= w["end"]), None)
            if not w:
                continue
            local = f - w["start"]
            objs = (by_concept[concept].get(w["index"]) or {}).get(local) or {}
            hits.append((f, w["index"], local, len(objs)))
            picks_by_seg.setdefault(w["index"], []).append(local)
        for k, picks in picks_by_seg.items():
            w = windows[k]
            sheet_at(w["path"], by_concept[concept][k], picks,
                     out / f"check-{slug}-s{k:02d}.jpg", f"{concept} (+{w['start']})")
        check_rows.append((concept, hits))

    lines = [f"# Concept probe — {args.video.name}", "",
             f"{len(windows)} segment(s) · {len(concepts)} concept(s) · "
             f"{len(windows) * len(concepts)} request(s) · "
             f"{sum(w['frames'] for w in windows) * len(concepts)} frames sent "
             f"(${sum(w['frames'] for w in windows) * len(concepts) / 1000 * stage1.PRICE_PER_1K_FRAMES:.2f})",
             "", f"generated {datetime.now().isoformat(timespec='seconds')}", "",
             "| concept | segment | frames | unique IDs | frames w/ box | spans (global) |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {c} | s{k} | {u}-{v} | {uids} | {hit} | {spans} |"
              for c, k, u, v, uids, hit, spans in rows]
    lines += ["", "## Check frames (a box on the frame you care about = the name works)", "",
              "| concept | " + " | ".join(str(f) for f in checks) + " |",
              "|---" * (len(checks) + 1) + "|"]
    for concept, hits in check_rows:
        cells = {f: ("**" + str(n) + "**" if n else "0") for f, _, _, n in hits}
        lines.append(f"| {concept} | " + " | ".join(cells.get(f, "-") for f in checks) + " |")
    (out / "probe.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"-> {out}/probe.md · sheets sheet-*.jpg · check-*.jpg")


if __name__ == "__main__":
    main()