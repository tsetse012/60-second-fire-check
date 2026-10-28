# 60-Second Fire Check

Walk a building corridor with your phone for a minute. Get a fire-safety self-inspection report.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Dev track).

**Status: day 1 — stage 4.** SAM tracks generic objects, Muse Spark labels each track, an inspection
agent re-opens the footage through tools before it files the checklist, and a local web app
(`app/`) runs the whole thing from a browser: upload → live processing → report.

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
uv run pipeline/run_all.py data/clip.mp4 --fps 10 --max-cost 3            # full run
```

Preprocessing (H.264, long side 1920, 10 fps, up to 3 minutes) is the one timeline every later stage
indexes: SAM frame numbers, the crops the agent re-cuts and `overlay.mp4` all line up, so a report
timestamp seeks to the same second in the overlay. Artefacts land in `runs/<id>/` — the stage 1–3
outputs plus `overlay.mp4` (boxes coloured by label: green fire equipment, red issue, amber needs a
person, grey not fire equipment; people are blurred and never labelled) and `job.json` (per-stage
status, timing, cost estimate, log).

## Pipeline

1. Phone video (1–3 min) of corridors, stairwells, parking levels.
2. **SAM 3.1 video** with generic concepts only (`door`, `sign`, `red box`, `wheelchair`) — stable track IDs
   and boxes. Fire-specific nouns are unreliable here; see `gate/RESULTS.md`.
3. **Track classification** (`pipeline/classify_tracks.py`) — crops the largest frame of every track plus a
   boxed context frame and asks `muse-spark-1.3` what each one is (fire hose cabinet / exit sign / not fire
   equipment / unclear), with a condition, confidence and one-sentence evidence. Tracks that sit on the
   same pixels are merged, within one concept or across two.
4. **Inspection agent** (`pipeline/inspect_agent.py`) — the classified tracks plus a ruleset
   (`rulesets/kr-fire.json`, 17 Korean self-inspection items; `rulesets/nfpa.json`) go to `muse-spark-1.3`
   with four tools: `list_tracks`, `view_track` (re-crop any frame that track was seen in, returned to the
   model as images), `get_rule`, `submit_report`. The agent is told not to trust stage-2 labels and to look
   again before it decides; `submit_report` is rejected if it marks something `ok`/`issue` on a track it
   never re-viewed, or leaves a ruleset item out. Every step's request/response is kept in `agent_raw/`, so
   `--reparse` rebuilds the report offline.
5. Measured against a manual clipboard count in 3 buildings.

## Inspection agent

```bash
uv run pipeline/inspect_agent.py --run runs/<run> --video data/clip.mp4 \
    --ruleset rulesets/kr-fire.json --lang en          # 12 model steps max
uv run pipeline/inspect_agent.py --run runs/<run> --video data/clip.mp4 --lang ko --reparse  # no API calls
```

Writes `report.json`, `report.md` (inspection-form table: 항목 / 결과 / 근거 영상 시점(초) / 비고 / 출처),
`email.md` (remediation email to the building manager) and the per-step raw records in `agent_raw/`.
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
```

## License

MIT
