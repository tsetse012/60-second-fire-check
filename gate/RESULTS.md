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
`door`), `exit sign` comes back as 12 tracks instead of the 7 the whole-clip request could hold, and where
that request went silent after frame 776 the windowed run has green signs at 21.9–74.8 s (frames
563–731) and 106.5–138.7 s (frames 1065–1385). The checklist reads `exit_sign_present = ok` at 74.7 s.

Billing: `usage.video_frames_processed` sums to 12,950 for that run against 13,041 frames expected
(9 concepts x 1,449) — the difference is one failed request and a **50-frame minimum per request**: the
last, 49-frame window is billed 50, and on a 21 s clip whose tail window is 10 frames the same floor
costs 40 x 9 = 360 frames (2,250 billed vs 1,890 expected). Splitting is free for windows of 50 frames
or more; a sub-5 s tail should be merged into the window before it if cost matters.

## Inspection-agent verdicts drift on the same frame — measured 2026-10-28

The stage-3 agent re-opens frames through `view_track` before filing, but its verdict on one crop is not
stable between runs. The 45 s fire door in `data/IMG_2559.MOV` (frame 450: the right leaf of a double
stairwell door standing open, surface closer on top) drew three different answers in five passes of the
same model on the same pixels:

| pass | `fd_selfclose` | `fd_held_open` | what the note claims |
|---|---|---|---|
| whole-clip run (before windowing) | issue | needs_human_check | door open |
| windowed, first pass | needs_human_check | ok | "seen open" |
| windowed, second pass | ok | ok | "shown closed" |
| windowed, third pass | ok | ok | "shown closed" |
| windowed, fourth pass | issue | needs_human_check | door open |

Two of the five say the open door is closed, so a single pass is not evidence for this item. The stage-2
label of that track (`fire_door`, rep frame 450) was identical across the passes, so this is model
variance, not a classification change. Open fix: lower the temperature, or file two independent passes and
report a disagreement as `needs_human_check` instead of picking one.

### Two-pass agreement — measured 2026-10-28

Both fixes are in (`pipeline/inspect_agent.py`): `temperature: 0.2` (the API takes it — a probe request
returned HTTP 200 and echoed the value back, so it is set, not guessed), and `view_track` now returns the
full frame **first**, with the prompt saying that one leaf filling a tight crop looks like a closed door.
Neither one stops the flip on its own. Three fresh copies of the same clip, run end to end (`--passes 2`),
give six raw verdicts on `fd_selfclose` for the same open door:

| run | check 1 | check 2 | merged |
|---|---|---|---|
| `…-s6a` | ok ("no dark gap beside leaf") | issue ("standing open — dark opening beside leaf") | needs_human_check |
| `…-s6b` | needs_human_check ("cannot tell latching shut vs standing open") | ok ("no gap or dark opening") | needs_human_check |
| `…-s6c` | issue ("dark opening/gap visible beside leaf") | needs_human_check ("full frame does not confirm it latches") | needs_human_check |

Two of six checks still call the open door closed, so the variance is in the model, not in what it was
shown. Merging is what buys the guarantee: **`fd_selfclose` was never `ok` in three out of three runs**,
where a single pass was `ok` in two of five. The cost is that it is never `issue` either — the ground truth
is a violation, and no merge of `ok`/`issue`/`needs_human_check` can outvote it into `issue`. For a
self-inspection form "a person must look at this door" is the right answer; for a demo headline it is a
weaker scene than the single pass that happened to be right.

`fd_held_open` followed the same door (3/3 `needs_human_check`). `exit_sign_present`, `ext_present`,
`ext_access`, `ext_condition` and `hc_*` were `ok` in all six checks of all three runs — the things that
were stable stayed stable.

One regression from merging: `evac_route_obstruct` came out `ok` in one check and `possibly_missing` in the
other in two of three runs, so it became `needs_human_check` although both checks described a clear
corridor ("Corridor sections … show clear floor" vs "No corridor obstruction seen in this walk"). That is a
wording difference, not a judgement difference — the ruleset item forbids something, so "I walked it and
saw nothing blocking it" is `ok`, and `possibly_missing` is for a fitting that should have been there. The
prompt does not say that yet. Fix not applied in this stage (it would have changed the prompt in the middle
of the three runs): make rule 6 say so, or special-case ok/possibly_missing on prohibitive items.

Hospital regression (one copy, `--passes 2`): `evac_route_obstruct` = `issue`, `exit_sign_present` = `ok`,
one disagreement (`fd_selfclose` ok / needs_human_check). `--reparse` on a two-pass run rebuilds
`report.json` byte-identical apart from `generated_at`, and an older single-pass run (`agent_raw/step_*.json`
without pass dirs) still reparses to the same statuses and usage.

Cost: two passes double the agent's model calls (12–13 per run instead of 6). The four runs above cost
457,897 input + 113,638 output tokens ≈ **$1.06** at $1.25 / $4.25 per million, against $0.13 for one
single-pass run of the same clip.

## Single-question door state — measured 2026-10-28

The two-pass merge stopped the open door from coming back `ok`, but it also stopped it from coming back
`issue`: no vote over `ok` / `issue` / `needs_human_check` ever reaches the right answer. The model does see
the door, though — it just loses the answer while holding sixteen other items at once. Asked **one**
question about the same boxed full frame (`agent_frames/door_15_f450_ctx.jpg`, 45.0 s), with nothing else in
the conversation, `muse-spark-1.3` answered `door_state = open` **10 times out of 10** at temperature 0.2,
with matching evidence ("swung out ~90°, dark opening exposed"). Ten calls cost **$0.10**.

Stage 2 now asks it there instead: every track it labels `fire_door` gets two independent calls of that one
question on its boxed frame (`pipeline/classify_tracks.py`, `door_state_raw/<slug>_<oid>_<k>.json`). Two
agreeing answers are the value; anything else is `unclear`. The inspection agent receives it as a measured
fact (`door_state: open (2/2 independent checks)` in `list_tracks` / `view_track`) and `submit_report` is
rejected when an item contradicts it: `open` must be `fd_selfclose = issue`, `closed` cannot be, `unclear`
must be `needs_human_check`; `fd_held_open` stays a wedge-visible-only issue.

Three fresh copies of the 45 s clip, each with two inspection passes:

| copy | measured door state | check 1 | check 2 | merged |
|---|---|---|---|---|
| `…-s7a` | `door_15 open (2/2)` | issue | issue | **issue** |
| `…-s7b` | `door_15 open (2/2)` | issue | issue | **issue** |
| `…-s7c` | `door_15 open (2/2)` | issue | issue | **issue** |

`fd_selfclose` came out `issue` in 6 of 6 conversations (in stage 6 it was `issue` in 2 of 6, and never
after merging), and no item disagreed in any of the three runs. `evac_route_obstruct` is now `ok` 6/6 on
this clip — the ruleset item forbids something, so "walked it, nothing in the way" is `ok`, not
`possibly_missing` (rule 6 says so now; stage 6's wording-only disagreement is gone). `exit_sign_*`,
`ext_*`, `hc_*` stayed `ok` as before.

Hospital copy (two doors): `door_0` = **unclear** (the two calls split — one read the leaf swung out along
the hallway, the other read it flush in the wall plane; the box is a leaf seen at a grazing angle at the
frame edge, where either reading is defensible, so `unclear` is the right output), `door_1` = **closed 2/2**
(leaf flush in its frame, vision panel and closer visible). With no door open
and one unclear, `fd_selfclose` = `needs_human_check`; `evac_route_obstruct` = `issue`, `exit_sign_present`
= `ok`. The one remaining disagreement there is `fd_held_open` (`ok` vs `needs_human_check` on a closed
door) — not a door-state contradiction, both passes agree nothing is holding it.

The report summary is no longer one pass's prose. With two checks it is assembled from the merged checklist
(plus the measured door states), because the pass-1 summary used to say "fire door appearing closed" on the
row above its own `needs_human_check`. The passes' own wording is kept in `report.json` as `check_summary`.

Cost: the six door calls (2 on the 45 s clip, 4 on the hospital clip) cost 4,884 input + 11,542 output
tokens ≈ **$0.055**; the four runs in this section (8 inspection conversations, 49 calls) came to
$0.97 — **$1.02** together, SAM untouched. `--reparse` rebuilds `door_state` from `door_state_raw/` with no
calls, and `report.json` comes back identical apart from `generated_at`.

Limits: the answer is about the leaf at the track's representative frame only — a door that is open at 45 s and
shut at 50 s is measured once; damage and a wedge are still the agent's call, and if that frame shows
the leaf edge-on the honest answer is `unclear` (as on `door_0`). The frame is picked by the rule in the
next section (largest box that is not cut off by the frame edge), which moved this crop for door tracks on
the 145 s walk — so the door answers in this section are about the older "largest box" frame.

## Measuring against a blind human count — 2026-10-28 (stage 8)

The owner counted the same 145 s walk from 2-second contact sheets without seeing any pipeline output
(`eval/IMG_2559.groundtruth.json`): **7 extinguishers, 4 hose cabinets (0, 4, 48, 64 s), 1 open fire door
(24 s), 4 closed fire doors (78, 82, 114, 122 s), 2 obstacles (0 s, 126 s)** — 18 units. The stage-7 run
reported 9 of them (50 %). Three measurement faults were behind the misses, and each is fixed here.

### Fault 1 — the representative frame was the frame where you cannot see the object

The largest box is the moment the walker stands right in front of the thing: the box touches the frame
edge and fills the picture. On the four closed fire doors that crop was a bare leaf — the closer above it
was out of shot — and stage 2 answered "plain door, no visible self-closer" for all four. The frame rule is
now: **largest box that sits ≥2 % inside every edge and covers ≤60 % of the frame**, falling back to the
largest box when no frame qualifies (`pipeline/classify_tracks.py`, `REP_EDGE_MARGIN` / `REP_MAX_FRAME_SHARE`
/ `pick_rep_frame`). Merged groups pick their representative with the same preference.

| track | representative frame before → after | usable frames | stage-2 label before → after | door state |
|---|---|---|---|---|
| `door_27` (113–122 s) | 1206 → **1172** | 33 of 88 | not_fire_equipment → **fire_door** | — → **closed 2/2** |
| `door_13` (24–25 s) | 249 → 248 | 14 of 19 | not_fire_equipment → **fire_door** | — → **open 2/2** |
| `door_24` (78–82 s) | 795 → 795 (fallback) | **0 of 39** | not_fire_equipment → not_fire_equipment | not measured |
| `door_26` (82–88 s) | 849 → 849 (fallback) | **0 of 47** | not_fire_equipment → not_fire_equipment | not measured |
| `door_28`/`door_29` (120–124 s) | 1232 / 1234 (fallback) | 0 of 19 / 0 of 10 | unclear → unclear | not measured |

The crops were opened and the walk frames between 780 and 877 rendered as a strip: on `door_24` and
`door_26` the top of the leaf — where a closer would be — **is never in the footage at all**. Every box
touches an edge because the walker passes within arm's length, so no frame rule can recover those two: this
is a capture limit, not a labelling one. `door_27` is the one door whose whole leaf and its hydraulic closer
were in shot at some frame, and the new rule takes it: `crops/door_27_ctx.jpg` (117.2 s) shows the leaf,
the "01" number and the closer arm at the top; the old frame was a hand on a handle against a featureless
grey surface. `door_13` flipped on a one-frame move (249 → 248), so its label is fragile — and its span
overlaps `door_15` (23.8–25.1 s vs 24.4–45.5 s), so these are probably **one physical door counted twice**;
the merge did not join them (the boxes never overlap enough in ≥50 % of shared frames).

### Fault 2 — two concept names were missing

The silver cabinets at 48 s and 64–70 s and the A-frame signs at 0 s and 126 s were never tracked. Probed
first, on three frame-exact segments of the same clip (0–99, 440–719, 1240–1359), one request per name per
segment — 18 requests, 3,000 frames, **$0.60** (`eval/concept_probe.py`, `runs/20261028-concept-probe/`):

| name | 0 s A-frame | 48 s cabinet | 6–7 s cabinet | ~70 s cabinet | 126 s A-frame | kept? |
|---|---|---|---|---|---|---|
| `wall cabinet` | — | 0 | 0 | 0 | — | no (no box anywhere) |
| `metal cabinet` | — | 0 | 0 | #0 707–713 only | — | no |
| `fire hose cabinet` | — | **#0 474–496** | **#0 62–75** | **#1 703–713** | — | **yes** |
| `a-frame sign` | #0 6–16 | — | — | — | 0 | no (missed the pharmacy sign) |
| `sandwich board` | 0 | — | — | — | 0 | no (no box anywhere) |
| `standing sign` | **#0 0–16, #1 3–18** | — | #2 46–99 | #3 706–709 | **6 IDs, 1268–1359** | **yes** |

Both are now in `DEFAULT_CONCEPTS`, and `--reparse --add-concepts "…"` folds concepts into an existing run
without re-billing the old ones: the two new names cost 2,900 frames = **$0.58** on the copied run, while
the other nine came back from their saved streams at no charge. `standing sign` also boxes wall-mounted
signs (frames 567–699), which is fine — stage 2 labels those not_fire_equipment.

### Fault 3 — a free-standing sign was not an obstruction

Stage 2's `corridor_obstruction` said "boxes, bicycle, wheelchair, cart, pallet", so the A-frame outside the
pharmacy came back not_fire_equipment. The definition now names a free-standing sign (standing sign, banner
stand, A-frame / sandwich board) on the corridor floor and excludes signs fixed to a wall or ceiling; the
inspection agent's rule 7 sends any `corridor_obstruction` track to `evac_route_obstruct = issue` after one
`view_track` (소방시설법 제16조제1항제2호, as already in `rulesets/kr-fire.json`).

### Re-measured on the same walk (`runs/20261028-153239-IMG_2559-s8`)

110 tracks labelled (12 merged, 0 errors), 3 door-state questions, two inspection passes that disagreed on
nothing:

| item | human count | tracks this run reports | units matched by hand |
|---|---|---|---|
| 소화기 | 7 | 12 (`fire_extinguisher_0…13`) | 7, unverified — the human listed 9 sightings for 7 units, and the tracks at 19.3 s and 77.4 s have no clean counterpart |
| 소화전함 | 4 (0, 4, 48, 64 s) | 5 (`6.2–7.5`, `7.0–7.3`, **`47.4–49.6`**, **`70.3–77.9`**, `70.5–70.9`) | 3 of 4 — 48 s and 64/70 s are new; the 0 s cabinet at the far end of the first corridor still gets no box |
| 방화문 열림 | 1 (24 s) | 2 (`door_13` 23.8–25.1, `door_15` 24.4–45.5) | 1 — the same door twice |
| 방화문 닫힘 | 4 (78, 82, 114, 122 s) | 1 (`door_27` 113.3–122.4, **closed 2/2**) | 1 of 4 — the other three are never in shot whole |
| 적치물 | 2 (0, 126 s) | 6 (`0.0–1.8` ×2, `16.5–21.7`, `76.6–77.9`, `126.8–136.8` ×2) | 2 counted, plus two banners at 16.5 s and 76.6 s the human did not count |

So the measured set went from 9 to **14 of 18** (cabinets 1 → 3, closed fire doors 0 → 1, obstacles 0 → 2,
extinguishers and the open door held), and the report now carries `fd_selfclose = issue` at 45.0 s and
`evac_route_obstruct = issue` at 135.6 s, both agreed by both checks. `eval/units.py` writes the
track-by-track table (`units.md`) beside each report; matching tracks to physical units stays a hand step,
as above.

Hospital regression (`…-s8h`, copied from `…-s7h`): re-classified and re-inspected with no new SAM spend
(its door crops are at the same frames, so the four saved door-state answers still describe them) — all 17
items came back with the same status (`evac_route_obstruct = issue` on the parked wheelchair and cart,
`exit_sign_present = ok`), and the one disagreement stage 7 had (`fd_held_open`) is now agreement. Two
concepts (`fire hose cabinet`, `standing sign`) have no saved stream for that clip, so the job says
"2 concept(s) failed" and $0.00 — they were not requested; the regression did not need them.

Cost of stage 8: SAM **$0.60** probe + **$0.58** new concepts = **$1.18** (hospital $0.00); Muse **151 calls**
— 110 classification, 6 door state, 15 inspection on `-s8`, 6 + 14 on the hospital copy — ≈ **$1.49**
(508,593 input / 200,244 output tokens, incl. the hospital copy's 4 saved door answers re-read at $0). That is one call over the 150-call budget the stage was given:
the hospital regression was kept at two passes so it stays comparable with its stage-7 baseline, which
cost two calls; the four door-state calls were not paid again (saved answers on identical frames).

Still open: the closed fire doors that the footage never shows whole (no frame rule can fix those — the
camera has to step back), the 0 s cabinet at the end of the first corridor, and the `door_13`/`door_15`
duplicate that the IoU merge does not catch.
