# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Measurement table: one row per tracked unit, straight from a finished run.

    uv run eval/units.py --run runs/<run>

Writes `<run>/units.md` next to report.md. It only counts what stage 2 labelled —
matching those tracks to the physical units a person counted is done by hand,
because two tracks can be one cabinet seen twice and one track can span two doors.
No model calls, no hidden judgement: `--fps` is read from the run's job.json so the
frame numbers turn into the same seconds the report cites.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MEASURED = ["fire_extinguisher", "fire_hose_cabinet", "fire_door", "corridor_obstruction"]


def load(run: Path) -> tuple[list[dict], float]:
    data = json.loads((run / "classified.json").read_text())
    fps = 10.0
    job = run / "job.json"
    if job.exists():
        fps = float(json.loads(job.read_text()).get("fps") or fps)
    return data["tracks"], fps


def units(rows: list[dict], category: str, fps: float) -> list[dict]:
    out = []
    for r in rows:
        label = r.get("label") or {}
        if r.get("merged_into") or label.get("category") != category:
            continue
        out.append({
            "track": r["track"],
            "first_s": round(r["first_frame"] / fps, 1),
            "last_s": round(r["last_frame"] / fps, 1),
            "condition": label.get("condition") or "",
            "confidence": label.get("confidence"),
            "door_state": r.get("door_state") or "",
            "evidence": (label.get("evidence") or r.get("error") or "").replace("|", "/"),
        })
    return sorted(out, key=lambda u: (u["first_s"], u["track"]))


def write(run: Path) -> Path:
    rows, fps = load(run)
    lines = [f"# Units measured in `{run.name}`", "",
             f"{fps:g} fps · {len(rows)} tracks in classified.json "
             f"({sum(1 for r in rows if r.get('merged_into'))} merged away) · "
             f"source: `classified.json`, no model calls", ""]
    totals = [("category", "tracks", "notes")]
    for cat in MEASURED:
        found = units(rows, cat, fps)
        note = ""
        if cat == "fire_door":
            states: dict[str, int] = {}
            for u in found:
                states[u["door_state"] or "not measured"] = states.get(u["door_state"] or "not measured", 0) + 1
            note = ", ".join(f"{k} {v}" for k, v in sorted(states.items()))
        totals.append((cat, str(len(found)), note))
    others: dict[str, int] = {}
    for r in rows:
        if r.get("merged_into"):
            continue
        cat = (r.get("label") or {}).get("category") or "unlabelled"
        if cat not in MEASURED:
            others[cat] = others.get(cat, 0) + 1
    lines += ["| " + " | ".join(totals[0]) + " |", "|---|---|---|"]
    lines += ["| " + " | ".join(t) + " |" for t in totals[1:]]
    lines += ["", "Not measured here: " + ", ".join(f"{k} {v}" for k, v in sorted(others.items())) + ".", ""]

    for cat in MEASURED:
        found = units(rows, cat, fps)
        lines += [f"## {cat} — {len(found)} track(s)", ""]
        if not found:
            lines += ["_no track with this category_", ""]
            continue
        head = "| # | track | seconds | condition | door_state | evidence |"
        lines += [head, "|---|---|---|---|---|---|"]
        for n, u in enumerate(found, 1):
            lines.append(f"| {n} | {u['track']} | {u['first_s']}–{u['last_s']} | {u['condition']} | "
                         f"{u['door_state']} | {u['evidence']} |")
        lines.append("")
    out = run / "units.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True)
    args = ap.parse_args()
    print(f"-> {write(args.run)}")


if __name__ == "__main__":
    main()