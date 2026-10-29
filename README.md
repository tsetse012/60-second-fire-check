# 60-Second Fire Check

Walk a building corridor with your phone. Get a fire-safety self-inspection checklist back — every
finding with a timestamp, an email for the building manager, and a list of what the video cannot settle.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Dev track · Build for Your World).

**Try it: https://firecheck.getnunchi.app** — two finished inspections open instantly with no model
call. Uploading a new clip needs the passcode in the submission notes (each run costs real API credit).

## The problem

In Korea, the people responsible for a building (관계인: owners, managers, occupants in charge) must
inspect its fire equipment themselves, on a schedule (소방시설법 제22조). The same act forbids closing or
damaging fire doors and leaving things in escape routes (제16조). Today that inspection is a paper form
filled in on foot, and faults like a fire door propped open or a sign standing in the corridor are the
kind that come and go between visits.

## What it does

1. Film a 1–3 minute walk down a corridor, stairwell or parking level (no people in frame).
2. Upload it. A one-minute walk takes about 12 minutes on the public server (measured: preprocess 11 s,
   SAM 156 s, labelling 181 s, inspection 169 s, overlay 174 s).
3. Get back: a 17-item checklist against the Korean self-inspection form, each finding linked to the
   second it happened; the video with every tracked object boxed and coloured by the verdict; a draft
   email to the building manager; and the items video cannot judge, marked "verify on site".

Absence is never reported as proof: an item that was not seen is "not seen in this walk".

## How Meta's models are used

| step | model | job |
|---|---|---|
| Track | **SAM 3.1** (video) | Follows doors, red boxes, signs, extinguishers, hose cabinets, standing signs, carts… through the whole walk and keeps one ID per object while it is on screen. SAM takes one concept per request and at most 16 unique objects per request, so the walk is cut into 10-second windows and the tracks are stitched at the seams. A `person` concept drives the blur — people are never labelled. |
| Label | **Muse Spark 1.3** | Looks at each track in the frame where the object is fully in view and names it from eleven categories (fire extinguisher, hose cabinet, fire door, exit sign, emergency light, smoke detector, sprinkler head, alarm device, corridor obstruction, not fire equipment, unclear) with a condition and one sentence of evidence, as JSON-schema output. |
| Measure | **Muse Spark 1.3** | Asks one question per fire door — open or closed? — twice, independently. Agreeing answers become a fact the inspector must respect. |
| Inspect | **Muse Spark 1.3** (tool use) | An agent with `list_tracks`, `view_track`, `get_rule`, `submit_report` works through the ruleset. A pass or fail on a track it never re-opened is sent back. Two independent passes run; where they disagree, the item goes to a person. |

SAM is good at "a door", not at "a fire door" (day-1 gate, `gate/RESULTS.md`); Muse Spark is good at
deciding what a crop is, not at following it for two minutes. Each does the half the other cannot.

## Measured against a person

One 145-second walk in one building. I counted it by hand from 2-second contact sheets before seeing any
output (`eval/IMG_2559.groundtruth.json`), then compared.

| item | counted by hand | found, first version | found, now |
|---|---|---|---|
| fire extinguisher | 7 | 7 | 7 |
| fire hose cabinet | 4 | 1 | 3 |
| fire door, open | 1 | 1 | 1 |
| fire door, closed | 4 | 0 | 1 |
| obstruction (A-frame sign) | 2 | 0 | 2 |
| **total** | **18** | **9 (50 %)** | **14 (78 %)** |

Three changes made the difference: picking the frame where the object is whole instead of the biggest
box, adding two concept names (`fire hose cabinet`, `standing sign`), and counting free-standing signs as
obstructions. Of the four still missed, three are closed fire doors whose top — where the closer sits —
never enters the footage, and one is a hose cabinet at the far end of a corridor. Those are filming limits:
keep each door whole in frame once. Details: `gate/RESULTS.md`.

## Limits

- One building, one walk measured. The number is a first measurement, not a benchmark.
- Track counts are not unit counts: the same extinguisher can come back as two tracks (13 tracks for 7
  extinguishers in the measured walk). The checklist judges items, not totals.
- `rulesets/nfpa.json` is drafted but its clause numbers are unverified and it has not been run.
- Video cannot test a lamp, a gauge or a cabinet's contents; those items say "verify on site".

## Run the demo

```bash
set -a; . ~/.secrets/credentials.env; set +a     # META_MODEL_API_KEY
uv run app/server.py                              # http://127.0.0.1:8000
```

Drop a 1–3 minute corridor video on the page, or pick a clip from `data/` under **Try a sample**.
The server runs `pipeline/run_all.py` in a background thread and the page polls the run's `job.json`,
so stage progress and every agent tool call stream in while they happen. A finished run of a sample
can be re-opened instantly (no model calls) while recording. The Meta API key is read from the
server's environment and never reaches the browser.

`pipeline/run_all.py` on its own:

```bash
uv run pipeline/run_all.py data/hospital-corridor-static.mp4 --dry-run   # preprocess + cost estimate
uv run pipeline/run_all.py data/clip.mp4 --fps 10 --max-cost 4            # full run (145 s walk ≈ $3.2 of SAM)
```

Preprocessing (H.264, long side 1920, 10 fps, up to 3 minutes) is the one timeline every later stage
indexes: SAM frame numbers, the crops the agent re-cuts and `overlay.mp4` all line up, so a report
timestamp seeks to the same second in the overlay. Artefacts land in `runs/<id>/` — the stage 1–3
outputs plus `overlay.mp4` (boxes coloured by the checklist — red an item it failed, amber needs a
person, green fire equipment with nothing against it, grey not fire equipment or too unclear to check;
a track no checklist item cites falls back to its label's colour; people are blurred and never
labelled) and `job.json` (per-stage status, timing, cost estimate, log).

Redrawing an overlay that was skipped, or drawn by an older renderer, needs no model call:

```bash
uv run pipeline/run_all.py --overlay-only --run runs/<id>   # SAM text + classified.json + report.json -> overlay.mp4
```

### Public mode (hackathon review link)

`FIRECHECK_PUBLIC=1` turns the server into a review link: the finished runs named in
`FIRECHECK_SHOWCASE` open for anyone with no model call, and a new upload needs the shared
`UPLOAD_PASSCODE` (from the submission notes) and counts against a daily budget
(`MAX_RUNS_PER_DAY`, default 4, Asia/Seoul; `MAX_RUNS_TOTAL`, default 15). One video runs at a time,
so a second upload gets a 429 rather than a queue. `/runs/<id>/` stops being a dump of the run
directory — only `report.json`, `job.json`, `overlay.mp4`, `crops/` and `agent_frames/` for a
showcase or uploaded run are served, so the unblurred `input.mp4`, the raw SAM streams and the model
dumps never leave the server; paths inside the JSON are rewritten before they are sent. Uploaded
clips go to Meta's model API, so the page asks people not to upload footage with people in it. With
the flag unset nothing changes: the local demo behaves exactly as before. Image build, run command
and showcase packing: `deploy/README.md`.

## Pipeline

1. Phone video (1–3 min) of corridors, stairwells, parking levels.
2. **SAM 3.1 video** with generic concepts only (`exit sign`, `red box`, `fire extinguisher`, `door`,
   `wheelchair`, `bicycle`, `cardboard boxes`, `cart`, `fire hose cabinet`, `standing sign`) — stable
   track IDs and boxes. Fire-specific nouns are unreliable here; see `gate/RESULTS.md`. SAM tracks at
   most **16 unique objects per request**, so the clip is cut into non-overlapping `--window` windows
   (default 10 s) and the tracks on either side of each cut are joined when their boxes overlap
   (IoU > 0.3); the stitched track files keep the IDs stage 2 and 3 read. Concepts added later can be
   folded into an existing run: `--reparse --add-concepts "standing sign"` sends only the missing
   (window × concept) requests and rebuilds everything else from the saved streams.
3. **Track classification** (`pipeline/classify_tracks.py`) — crops the largest frame of every track whose
   box neither touches the frame edge (≥2 % margin) nor covers more than 60 % of the frame — the frame
   before the walker is right in front of the object — plus a boxed context frame, and asks
   `muse-spark-1.3` which of the eleven `CATEGORIES` each one is, with a condition, confidence and one-sentence evidence. Four requests are in
   flight at a time; the outputs are still written in track order. Tracks that sit on the
   same pixels are merged, within one concept or across two. Every track that comes out `fire_door` then
   gets the door-state question **on its own**: two independent calls, one question
   (`open | closed | unclear` + evidence) about the boxed full frame, agreeing answers kept and anything else
   `unclear`. The single question is 10/10 stable where the 17-item agent loop flipped (`gate/RESULTS.md`);
   raw responses are kept in `door_state_raw/` and `--reparse` rebuilds the value with no calls.
4. **Inspection agent** (`pipeline/inspect_agent.py`) — the classified tracks plus a ruleset
   (`rulesets/kr-fire.json`, 17 Korean self-inspection items; `rulesets/nfpa.json`) go to `muse-spark-1.3`
   with four tools: `list_tracks`, `view_track` (re-crop any frame that track was seen in, returned to the
   model as images), `get_rule`, `submit_report`. The agent is told not to trust stage-2 labels and to look
   again before it decides; `submit_report` is rejected if it marks something `ok`/`issue` on a track it
   never re-viewed, if it leaves a ruleset item out, or if it contradicts the measured `door_state`
   (`open` must be `fd_selfclose = issue`, `closed` cannot be, `unclear` must be `needs_human_check`). One
   verdict drifts: the same crop came back "closed" on the same open door twice in five runs
   (`gate/RESULTS.md`), so **two independent conversations** run side by side on the same inputs and are
   merged item by item — same answer twice keeps it, different answers become `needs_human_check` carrying
   both notes. The summary paragraph is assembled from that merged checklist, so it cannot call a door
   closed above a row that says `needs_human_check` (the passes' own prose stays in `check_summary`). Every
   step's request/response is kept per pass in `agent_raw/pass1`, `agent_raw/pass2`, so `--reparse` rebuilds
   the merged report offline.
5. Measured against a blind hand count of one walk — see **Measured against a person** above.

## Inspection agent

```bash
uv run pipeline/inspect_agent.py --run runs/<run> --video data/clip.mp4 \
    --ruleset rulesets/kr-fire.json --lang en          # 12 model steps max per pass
uv run pipeline/inspect_agent.py --run runs/<run> --video data/clip.mp4 --lang ko --reparse  # no API calls
uv run pipeline/inspect_agent.py --run runs/<run> --video data/clip.mp4 --passes 1   # one check only
```

Writes `report.json`, `report.md` (inspection-form table: 항목 / 결과 / 근거 영상 시점(초) / 비고 / 출처),
`email.md` (remediation email to the building manager) and the per-step raw records in `agent_raw/passN/`.
Two checks run by default (`--passes`); each item in `report.json` carries `passes` (every check's status,
note and evidence) and `agreement`, and `needs_human_check` is what a disagreement becomes. If one check
never files, the report falls back to the other one and says `single_pass`.
Statuses are `ok / issue / possibly_missing / needs_human_check / not_checkable` — absence is only ever
reported as "not seen in this walk", never as proof.

## Rulesets

`rulesets/kr-fire.json` — 17 items from 소방시설 설치 및 관리에 관한 법률 and 소방시설 자체점검사항 등에
관한 고시 (별지 제6호서식), each checked against 법제처 and marked `source_status: verified`. Items that
video cannot judge (pressure gauge, lamp test, cabinet interior) carry `video_checkable: false` and come
out of the report as "verify on site".

`rulesets/nfpa.json` — the same items against NFPA 10 / 101 / 25 / 72. Clause numbers there are **not
verified** (`source_status: unverified`, no clause asserted); it exists to show the ruleset is a data file,
not Korean-law code.

## Gate script

```bash
export META_MODEL_API_KEY=...
uv run gate/sam_video_gate.py data/clip.mp4 --dry-run   # cost estimate only
uv run gate/sam_video_gate.py data/clip.mp4             # upload + one request per concept
```

Outputs go to `runs/<timestamp>/` (raw stream per concept, `summary.json`, `summary.md`, box overlay sheets).

## Track classification

```bash
uv run pipeline/classify_tracks.py --run runs/<run> --video data/clip.mp4 --dry-run  # tracks + call count
uv run pipeline/classify_tracks.py --run runs/<run> --video data/clip.mp4            # crops/, muse_raw/, classified.{json,md}
uv run pipeline/classify_tracks.py --run runs/<run> --video data/clip.mp4 --reparse  # rebuild, no API calls
uv run pipeline/classify_tracks.py --run runs/<run> --video data/clip.mp4 --reparse --door-state  # door checks only
```

## License

MIT
