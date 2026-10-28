# 60-Second Fire Check

Walk a building corridor with your phone for a minute. Get a fire-safety self-inspection report.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Dev track).

**Status: day 1 — model gate.** Checking whether SAM 3.1 video segmentation keeps a stable track ID on
fire extinguishers, exit signs and fire doors across a hand-held corridor walk.

## Pipeline (planned)

1. Phone video (1–3 min) of corridors, stairwells, parking levels.
2. **SAM 3.1 video** with generic concepts only (`door`, `sign`, `red box`, `wheelchair`) — stable track IDs
   and boxes. Fire-specific nouns are unreliable here; see `gate/RESULTS.md`.
3. **Track classification** (`pipeline/classify_tracks.py`) — crops the largest frame of every track plus a
   boxed context frame and asks `muse-spark-1.3` what each one is (fire hose cabinet / exit sign / not fire
   equipment / unclear), with a condition, confidence and one-sentence evidence.
4. **Muse Spark** agent — classified tracks + a ruleset JSON (Korean fire-safety self-inspection
   checklist, NFPA 10/101 core items, each clause cited) → checklist with status
   `ok / blocked / possibly missing / needs human check`, plus a remediation email draft.
5. Measured against a manual clipboard count in 3 buildings.

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
