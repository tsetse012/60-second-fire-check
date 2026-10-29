# 3-minute demo video — script v1 (2026-10-29)

Voice: ElevenLabs, English, one narrator, calm and plain. Target ≈ 400 words at ~140 wpm, with breathing
room for the screen. Every number below is traced to a file in this repo (see "Claims" at the end).
Footage is real: one phone walk (IMG_2559, 145 s, no people in frame), the live site at
https://firecheck.getnunchi.app, and the run artefacts. No generated video.

Timing is the cut; word counts are the budget for each block.

---

## 1 · The problem — 0:00–0:20 (≈ 45 words)

**Screen:** phone footage walking a corridor, real speed. At 0:12 the walk reaches the white fire door
standing open (overlay timestamp 45 s). Freeze. A red box draws around the door.

> This fire door should be shut. Propped open, it lets smoke into the corridor.
> In Korea, building owners are required by law to inspect their own fire equipment.
> Today that means a clipboard, a paper checklist and a walk down every corridor.

**On-screen text (0:14):** `Fire Facilities Act, Art. 22 — owners must self-inspect`

## 2 · What it does — 0:20–0:40 (≈ 45 words)

**Screen:** the live site. Drop the phone video on the upload area, type the passcode, press
**Run inspection**. Cut to the progress screen — stage list ticking, tool calls streaming in.
Corner tag: `sped up`.

> 60-Second Fire Check turns that walk into the inspection.
> Film the corridor on your phone, upload it, and a few minutes later you get a filled-in checklist,
> with a timestamp for every finding and an email for the building manager.

## 3 · How: two Meta models, three jobs — 0:40–1:40 (≈ 140 words)

**3a · Track (0:40–1:00).** Screen: the overlay video, boxes following doors, extinguishers, signs.

> SAM 3.1 follows every object through the video and keeps its identity from frame to frame,
> so one extinguisher is counted once, not thirty times.
> It tracks sixteen objects per request at most, so the walk is cut into ten-second windows
> and stitched back together where they overlap.

**3b · Label (1:00–1:15).** Screen: grid of crops, each with its Muse Spark label appearing.

> SAM finds "a door" or "a red box", not "a fire door". So Muse Spark looks at each tracked object,
> in the frame where it is fully in view, and names it.

**3c · Inspect (1:15–1:40).** Screen: the agent's tool-call feed — `list_tracks`, `view_track`,
`get_rule`, `submit_report` — then the two passes side by side.

> Then an inspection agent works through seventeen rules from the Korean self-inspection form.
> It must open the footage before it can pass or fail anything.
> It runs twice, independently, and where the two runs disagree, the item goes to a person.
> Whether a door is open is asked as a single question of its own: inside the long checklist, that
> answer flipped.

## 4 · Measured against a person — 1:40–2:15 (≈ 80 words)

**Screen:** the 18 items I counted by hand (blind, from contact sheets) on the left; the agent's matches
filling in on the right. Big number: `9 / 18 → 14 / 18`.

> I counted the same walk by hand, without looking at the results: eighteen items.
> The first version found nine. Fixing how we choose the frame, and two object names, brought it to fourteen.

**Screen:** the closed fire door at 1:18 — the door's top edge never enters the frame.

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

> Built in ten days on SAM 3.1 and Muse Spark. The rules live in one JSON file with their legal sources,
> so another country's code is a file swap.
> 60-Second Fire Check.

**End card:** `firecheck.getnunchi.app · github.com/tsetse012/60-second-fire-check`

---

## Claims → evidence

| line in script | source |
|---|---|
| owners required to self-inspect | 소방시설법 제22조① (law.go.kr DRF, MST 236977, read 2026-10-29); `rulesets/kr-fire.json` sources |
| fire door must not be left open / no stored items in escape route | 소방시설법 제16조①1·2·3호; `rulesets/kr-fire.json` `fd_selfclose`, `evac_route_obstruct` |
| open door at 45 s | `runs/…-IMG_2559-show/report.json` `fd_selfclose` issue 45.0 s; `eval/IMG_2559.groundtruth.json` (same door first seen 24 s) |
| SAM keeps identity frame to frame | `gate/RESULTS.md` day-1 gate (IDs held 100 % within span) |
| 16 objects per request → 10-second windows, stitched on overlap | `gate/RESULTS.md` stage 5; `pipeline/run_all.py` window logic; commit b35ba0b, 6c27770 |
| fire-specific names missed → generic track + Muse Spark label | `gate/RESULTS.md` gate result; commit 0adfa73 |
| frame where the object is fully in view | `pipeline/classify_tracks.py` `pick_rep_frame`; commit 7e4f75d |
| 17 rules from the Korean self-inspection form | `rulesets/kr-fire.json` (17 items, 별지 제6호서식) |
| must open footage before pass/fail | `pipeline/inspect_agent.py` (rejects ok/issue without viewed evidence) |
| two independent runs, disagreement → person | commit 26fee17 |
| door state as a single question | commit 0a1e8bd; README decision log 10/28 (10/10 open on single question vs 2/5 flips in the long loop) |
| 18 items, blind, 9 → 14 | `eval/IMG_2559.groundtruth.json`; `gate/RESULTS.md` "Measuring against a blind human count" |
| 3 of 4 misses = door top never in frame | `gate/RESULTS.md` Fault 1 table (door_24, door_26, door_28/29) |
| timestamp chip seeks the video | `app/static/app.js` |
| "not seen" ≠ missing | `rulesets/kr-fire.json` scope_note |
| ten days | build window 10/27–11/4 (first commit 10/28) |

## Open points for v2

- 0:12 freeze: confirm the exact overlay frame where the open door is clearest (≈ 45 s).
- Section 4: "two object names" = `fire hose cabinet`, `standing sign` — fine to say on screen, not in voice.
- Whether to show the passcode field on screen (blur the typed value either way).
- Live run for section 2 is recorded once on the public site (≈ $4.6) — the upload must be a clip with no people.
