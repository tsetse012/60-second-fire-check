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

## 16 unique objects per request — measured 2026-10-28

Own phone footage (`data/IMG_2559.MOV`, 1080x1920, 1449 frames at 10 fps, 145 s) showed the limit:

- One request over the whole clip: `door` and `sign` each stopped at exactly **16 unique IDs**, and
  `sign` found no new object after frame 776 (77 s). The green evacuation lights at 70–100 s were never
  tracked, so the checklist reported `exit_sign_present = possibly_missing` — wrong.
- One request over a 30 s crop (`data/seg-70-100.mp4`, 300 frames): `sign` used all 16 IDs within 7.5 s
  and returned nothing after frame 74.
- `max_objects_in_frame` stayed ≤ 9 in both, so the cap is **16 unique objects per request**, not per frame
  (the day-1 note above read the documented limit as per-frame).

Fix: `pipeline/run_all.py` cuts the preprocessed clip into `--window` windows (default 10 s = fps x 10
frames) and sends one request per (window, concept). A track on the last frame of window k and a track on
the first frame of window k+1 are the same object when their boxes overlap (IoU > 0.3), so IDs stay global;
the per-window SSE streams are kept as `<slug>.w<k>.sse.jsonl` and `--reparse` rebuilds the stitched
timeline without calling the API. Same footage, 135 requests: no window used more than 10 IDs (worst =
`door`), `exit sign` tracks 219–731 and 1065–1385 (the second one is the 107–139 s stretch that was
invisible before), and the checklist now reads `exit_sign_present = ok` at 74.7 s.

Billing: `usage.video_frames_processed` sums to 12,950 for that run against 13,041 frames expected
(9 concepts x 1,449) — the difference is one failed request and a **50-frame minimum per request**: the
last, 49-frame window is billed 50, and on a 21 s clip whose tail window is 10 frames the same floor
costs 40 x 9 = 360 frames (2,250 billed vs 1,890 expected). Splitting is free for windows of 50 frames
or more; a sub-5 s tail should be merged into the window before it if cost matters.
