# 3-minute demo video — script v2 (2026-10-29, after fact-check `.omc/handoffs/agents/video-factcheck.md`)

Voice: ElevenLabs, English, one narrator, calm and plain. Target ≈ 400 words at ~140 wpm, with breathing
room for the screen. Every number below is traced to a file in this repo (see "Claims" at the end).
Footage is real: one phone walk (IMG_2559, 145 s, no people in frame), the live site at
https://firecheck.getnunchi.app, and the run artefacts. No generated video.

Timing is the cut; word counts are the budget for each block.

---

## 1 · The problem — 0:00–0:20 (≈ 45 words)

**Screen:** phone footage walking a corridor, real speed. At 0:12 the walk reaches the white fire door
standing open (source 44.5 s — both leaves open, closer visible on the right leaf). Freeze. A red outline
is drawn around the door **in the edit** (these are the raw frames, before any boxes; in the product
overlay the same door is red now — the checklist fails it, `fd_selfclose = issue`).

> This fire door should be shut. Propped open, it lets smoke into the corridor.
> In Korea, building owners and managers are required by law to inspect their own fire equipment.
> Today that means a paper checklist, filled in on foot.

**On-screen text (0:14):** `Fire Facilities Act, Art. 22 — owners and managers must self-inspect`

## 2 · What it does — 0:20–0:40 (≈ 45 words)

**Screen:** the live site. Drop a ~60 s corridor clip (no people) on the upload area, type the passcode
(blurred), press **Run inspection**. Cut to the progress screen — stage list ticking, tool calls streaming
in. Corner tag: `sped up`.

> 60-Second Fire Check turns that walk into the inspection.
> Film the corridor on your phone and upload it. About fifteen minutes later you get a filled-in
> checklist, with a timestamp for every finding and an email for the building manager.

## 3 · How: two Meta models, three jobs — 0:40–1:40 (≈ 140 words)

**3a · Track (0:40–1:00).** Screen: the overlay video, boxes following doors, extinguishers, signs.

> SAM 3.1 follows each object through the video and keeps one identity for it while it stays on screen.
> It tracks sixteen objects per request at most, so the walk is cut into ten-second windows
> and stitched back together at the seams.

**3b · Label (1:00–1:15).** Screen: an edit graphic built from the run's real `crops/` — a grid of
tracked objects, each with its Muse Spark label appearing (not an app screen).

> SAM finds "a door" or "a red box", not "a fire door". So Muse Spark looks at each tracked object,
> in the frame where it is fully in view, and names it.

**3c · Inspect (1:15–1:40).** Screen: the agent's tool-call feed — `list_tracks`, `view_track`,
`get_rule`, `submit_report` — then the "How the agent checked" tab with its `check 1` / `check 2` chips.

> Then an inspection agent works through seventeen rules from the Korean self-inspection form.
> If it tries to pass or fail an item without opening the footage, it is sent back.
> It runs twice, independently, and where the two runs disagree, the item goes to a person.
> Whether a door is open is asked as a single question of its own: inside the long checklist, that
> answer flipped.

## 4 · Measured against a person — 1:40–2:15 (≈ 80 words)

**Screen:** the 18 items I counted by hand (blind, from contact sheets) on the left; the agent's matches
filling in on the right. Big number: `9 / 18 → 14 / 18`.

> I counted the same walk by hand, without looking at the results: eighteen items.
> The first version found nine. Picking a better frame, adding two object names, and counting
> free-standing signs as obstructions brought it to fourteen.

**Screen:** the closed fire door at 1:20–1:22 (source 79.5–82 s) — the door's top edge never enters the frame.

> Three of the four it still misses are closed fire doors whose top, where the closer sits, was never in
> the shot. That is a filming rule, not a model problem: keep the whole door in frame once.

## 5 · What you get — 2:15–2:45 (≈ 65 words)

**Screen:** the result page. Click a timestamp chip → the video jumps to that second. Scroll to the
email draft. Then the "not seen" rows.

> Every finding links to the second it happened.
> The email tells the manager what to fix and where.
> And the report says what video cannot settle. A smoke detector that was not seen is "not seen in this
> walk", never "missing". If in doubt, it asks for a person on site.

## 6 · Close — 2:45–3:00 (≈ 30 words)

**Screen:** URL + GitHub link, ruleset JSON file scrolling.

> Built this week on SAM 3.1 and Muse Spark. The rules are data, not code: one file, every item
> tied to its legal source.
> 60-Second Fire Check.

**End card:** `firecheck.getnunchi.app · github.com/tsetse012/60-second-fire-check`

---

## Claims → evidence

| line in script | source |
|---|---|
| owners and managers (관계인) required to self-inspect | 소방시설법 제22조① (law.go.kr DRF, MST 236977, read 2026-10-29); `rulesets/kr-fire.json` sources |
| fire door must not be left open / no stored items in escape route | 소방시설법 제16조①1·2·3호; `rulesets/kr-fire.json` `fd_selfclose`, `evac_route_obstruct` |
| open door at 45 s | `runs/…-IMG_2559-show/report.json` `fd_selfclose` issue 45.0 s; `eval/IMG_2559.groundtruth.json` (same door first seen 24 s) |
| SAM keeps one identity while on screen (not a unique count: 13 extinguisher tracks vs 7 real) | `gate/RESULTS.md` day-1 gate (IDs held 100 % within span) |
| 16 objects per request → 10-second windows, stitched at the seams (box IoU > 0.3) | `gate/RESULTS.md` stage 5; `pipeline/run_all.py` window logic; commit b35ba0b, 6c27770 |
| fire-specific names missed → generic track + Muse Spark label | `gate/RESULTS.md` gate result; commit 0adfa73 |
| frame where the object is fully in view | `pipeline/classify_tracks.py` `pick_rep_frame`; commit 7e4f75d |
| 17 rules from the Korean self-inspection form | `rulesets/kr-fire.json` (17 items, 별지 제6호서식) |
| must open footage before pass/fail | `pipeline/inspect_agent.py` (rejects ok/issue without viewed evidence) |
| two independent runs, disagreement → person | commit 26fee17 |
| door state as a single question | commit 0a1e8bd; README decision log 10/28 (10/10 open on single question vs 2/5 flips in the long loop) |
| 18 items, blind, 9 → 14 (three fixes: frame rule, two concepts, free-standing sign = obstruction) | `eval/IMG_2559.groundtruth.json`; `gate/RESULTS.md` "Measuring against a blind human count" |
| 3 of 4 misses = door top never in frame | `gate/RESULTS.md` Fault 1 table (door_24, door_26, door_28/29) |
| timestamp chip seeks the video | `app/static/app.js` |
| "not seen" ≠ missing | `rulesets/kr-fire.json` scope_note |
| this week | first commit 2026-10-28 (`7bfba01`); recording 11/3 |

| about fifteen minutes | cold run of the 145 s walk: preprocess 16 s + SAM 338 s + classify 209 s + inspect ≈200 s + overlay ≈203 s ≈ 16 min (`runs/20261029-082301-IMG_2559*/job.json`) |
| must open footage or be sent back | `pipeline/inspect_agent.py:364-370` (after 2 rejections the gap is recorded, not hidden) |

## Open points

- ~~Product mismatch to fix before recording~~ **fixed 2026-10-29:** the overlay colour now follows the
  filed checklist (`report.json` `evidence_track_ids` → red for `issue`, amber for `needs_human_check`,
  else the stage-2 label), so the open door is red and the legend under the video matches
  (`pipeline/run_all.py` `load_verdicts`).
- Live upload for section 2: a ~60 s cut with no people keeps the run near 6–7 minutes and ≈ $1.5–2.
  The public server's CPU is slower than this Mac — time one run there before recording.
- Section 4: "two object names" = `fire hose cabinet`, `standing sign` — on screen, not in voice.
- Passcode field: blur the typed value.
