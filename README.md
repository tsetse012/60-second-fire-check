# 60-Second Fire Check

Walk a building corridor with your phone for a minute. Get a fire-safety self-inspection report.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Dev track).

**Status: day 1 — model gate.** Checking whether SAM 3.1 video segmentation keeps a stable track ID on
fire extinguishers, exit signs and fire doors across a hand-held corridor walk.

## Pipeline (planned)

1. Phone video (1–3 min) of corridors, stairwells, parking levels.
2. **SAM 3.1 video** — one concept per request (`fire extinguisher`, `exit sign`, `fire door`, ...).
   Track IDs de-duplicate items so the same extinguisher is counted once.
3. **Muse Spark** agent — tracked items + sample frames + a ruleset JSON (Korean fire-safety self-inspection
   checklist, NFPA 10/101 core items, each clause cited) → checklist with status
   `ok / blocked / possibly missing / needs human check`, plus a remediation email draft.
4. Measured against a manual clipboard count in 3 buildings.

## Gate script

```bash
export META_MODEL_API_KEY=...
uv run gate/sam_video_gate.py data/clip.mp4 --dry-run   # cost estimate only
uv run gate/sam_video_gate.py data/clip.mp4             # upload + one request per concept
```

Outputs go to `runs/<timestamp>/` (raw stream per concept, `summary.json`, `summary.md`, box overlay sheets).

## License

MIT
