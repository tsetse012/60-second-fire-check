# Day-1 gate results (2026-10-28)

Three free Pexels clips, no people: a hand-held walk down a mall corridor (32 s, 779 frames),
a static hospital corridor (21 s, 504 frames), an almost empty corridor as a control (15 s, 446 frames).
37 requests, 22,135 frames billed (~$4.43 at $0.20 / 1k frames). Every request returned HTTP 200.
Each concept took 15–40 s per clip; the first frame streams back in ~2–3 s.

## Track-ID stability — pass

Where SAM 3.1 found an object, it kept the same ID for its whole visible span
(coverage 100 % inside the span for almost every track):

- hospital `exit sign`: 1 ID, frames 0–503, mean confidence 0.70 (correct: red EXIT sign at the far end)
- hospital `wheelchair`: 1 ID, frames 1–503 (correct: wheelchair parked in the corridor)
- mall `door`: 6 IDs while walking; IDs follow glass doors as they move across the frame
- mall `red box`: 1 ID, frames 620–778 (correct: red fire-hose cabinet on a pillar)

## Fire-specific concept names — fail on this footage

- `fire hose cabinet`, `fire extinguisher`, `exit sign`, `emergency light`, `fire door`, `sprinkler head`:
  0 matches on the mall walk, although red hose cabinets are clearly visible.
- `smoke detector` on the hospital clip matched the round ceiling air diffuser (false positive).
- Generic names do catch them: `red box` (the cabinet), `sign` (16 IDs, signs and cabinets mixed).

## What this changes

SAM 3.1 is reliable as a **tracker of generic objects** (`sign`, `red box`, `door`, `wheelchair`),
not as a fire-equipment classifier. Pipeline becomes two stages:

1. SAM 3.1 video with a few generic concepts → stable track IDs + boxes.
2. Muse Spark looks at one crop per track ID and classifies it against the ruleset
   (extinguisher / hose cabinet / exit sign / not fire equipment / needs human check).

Still unverified: Korean apartment corridors (floor-standing extinguishers, green exit signs);
this footage is not Korean. Next gate = own footage.
