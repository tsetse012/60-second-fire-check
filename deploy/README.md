# Deploying the public demo

One container, one volume, no database. The image carries code only — no footage, no
API key, no run artifacts.

## Build

```bash
docker build -f deploy/Dockerfile -t firecheck:latest .
```

## Run

```bash
sudo mkdir -p /srv/firecheck/runs && sudo chown 10001:10001 /srv/firecheck/runs

docker run -d --name firecheck --network coolify --restart unless-stopped -p 8000:8000 \
  -v /srv/firecheck/runs:/app/runs \
  -e META_MODEL_API_KEY \
  -e FIRECHECK_PUBLIC=1 \
  -e UPLOAD_PASSCODE \
  -e FIRECHECK_SHOWCASE='20261029-082301-IMG_2559-show=Building corridor walk (phone),20261028-155043-hospital-corridor-static-show=Hospital corridor (stock clip)' \
  firecheck:latest
```

The container runs as uid 10001, so the host `runs/` directory has to be owned by that
uid (the `chown` above) or uploads fail with permission errors.

| Variable | Meaning |
| --- | --- |
| `META_MODEL_API_KEY` | model key; without it the site loads but refuses new runs |
| `FIRECHECK_PUBLIC=1` | turns on public mode (allowlisted `/runs`, passcode, quotas) |
| `UPLOAD_PASSCODE` | shared passcode for the upload form; **empty = uploads off** |
| `FIRECHECK_SHOWCASE` | comma-separated `run_id=Card label` entries (label optional) that open instantly for anyone |
| `MAX_RUNS_PER_DAY` | uploads per calendar day, Asia/Seoul (default 4) |
| `MAX_RUNS_TOTAL` | uploads over the container's lifetime (default 15) |

Without `FIRECHECK_PUBLIC=1` the container behaves exactly like the local demo: no
passcode, `/runs/<id>/` serves the whole run directory (unblurred `input.mp4`
included), and samples in `data/` can be run.

## Showcase runs

Packing happens on the machine that recorded the runs:

```bash
./deploy/pack_showcase.sh <run_id> [<run_id> ...]       # writes showcase-<ts>.tar.gz
scp showcase-*.tar.gz <server>:/tmp/
ssh <server> 'tar -xzf /tmp/showcase-*.tar.gz -C /srv/firecheck/runs'
```

The tarball holds only what public mode serves: `job.json`, `report.json`,
`overlay.mp4`, `crops/*.jpg`, `agent_frames/*.jpg` (~16 MB for two runs). The
unblurred `input.mp4`, the raw SAM streams and the model raw dumps never leave the
recording machine. Absolute paths inside `report.json` are rewritten out of every
response by the server.

## Reverse proxy

`deploy/traefik.yaml` is a dynamic-config file: `firecheck.getnunchi.app`, http →
https redirect, `letsencrypt` certResolver, `http://firecheck:8000`. Check the
comments there about the entry point `readTimeout` before the first 400 MB upload.

## Things that are per-container, not shared

- The "one video at a time" lock lives in the process. **Run exactly one replica.**
- The upload quota lives in `/app/runs/_quota.json` (inside the volume), so it
  survives restarts but is only as safe as the single container.
- Clips longer than 3 minutes are rejected at the preprocessing step, not trimmed.