#!/usr/bin/env bash
# Pack finished runs for the public server — allowlisted files only.
#
#   ./deploy/pack_showcase.sh 20261029-082301-IMG_2559-r2 20261028-155043-hospital-corridor-static-s8h
#
# Produces showcase-<timestamp>.tar.gz holding, per run, exactly what app/server.py
# serves in public mode: job.json, report.json, overlay.mp4, crops/*.jpg,
# agent_frames/*.jpg. The unblurred input.mp4, the raw SAM streams (*.sse.jsonl),
# muse_raw/, agent_raw/, door_state_raw/, windows/ and the intermediate summaries stay
# on the machine that ran them.
#
# On the server:  mkdir -p /srv/firecheck/runs && tar -xzf showcase-*.tar.gz -C /srv/firecheck/runs
# The extracted report.json still carries the recording machine's absolute paths; the
# server rewrites them out of every response, so nothing leaks to the browser.
set -euo pipefail

RUNS="${RUNS:-$(cd "$(dirname "$0")/.." && pwd)/runs}"
[ $# -ge 1 ] || { echo "usage: $0 <run_id> [run_id...]" >&2; exit 2; }

out="showcase-$(date +%Y%m%d-%H%M%S).tar.gz"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

for id in "$@"; do
  src="$RUNS/$id"
  [ -d "$src" ] || { echo "no such run: $src" >&2; exit 1; }
  dst="$stage/$id"
  mkdir -p "$dst"
  for f in job.json report.json overlay.mp4; do
    [ -f "$src/$f" ] && cp "$src/$f" "$dst/" || true
  done
  [ -f "$dst/report.json" ] || { echo "$id: no report.json — not a finished run" >&2; exit 1; }
  [ -f "$dst/overlay.mp4" ] || echo "  ! $id has no overlay.mp4 (the player will be empty)" >&2
  for d in crops agent_frames; do
    [ -d "$src/$d" ] || continue
    mkdir -p "$dst/$d"
    # `-exec cp {} dir +` needs `{}` last on BSD find, so pipe instead — this has to
    # run on the Mac that recorded the runs as well as on the server.
    find "$src/$d" -maxdepth 1 -type f \
      \( -name '*.jpg' -o -name '*.jpeg' -o -name '*.png' \) -print0 |
      while IFS= read -r -d '' file; do cp "$file" "$dst/$d/"; done
  done
  printf '%s: %s files, %s\n' "$id" \
    "$(find "$dst" -type f | wc -l | tr -d ' ')" "$(du -sh "$dst" | cut -f1)"
done

tar -czf "$out" -C "$stage" .
printf '\nwrote %s (%s)\n' "$out" "$(du -h "$out" | cut -f1)"
printf 'on the server: tar -xzf %s -C /srv/firecheck/runs\n' "$out"