#!/usr/bin/env python3
"""Upscale the generated shots to 1080p with Atlas Cloud video-upscaler ($0.018/s, rounded up per clip).

    python3 video/tools/upscale.py            # every video/work/renders/GEN*_v1.mp4/spot_*.mp4

Pitfalls this follows (~/Claude/knowledge/tools/video-i2v-atlas.md): use the video key, one job at a time
(parallel submits get 403), send a User-Agent on the download (403 without it). The 1080p target takes clips up
to 30 s whole. Audio is stripped before upload and the original track is muxed back with -c:a copy.
Output: video/work/upscaled/GENn_1080.mp4. Existing outputs are skipped, so a rerun only does what is missing.
"""
import glob, json, os, pathlib, subprocess, time, urllib.error, urllib.request

KEY = os.environ["ATLASCLOUD_VIDEO_API_KEY"]
UPLOAD = "https://api.atlascloud.ai/api/v1/model/uploadMedia"
GEN = "https://api.atlascloud.ai/api/v1/model/generateVideo"
POLL = "https://api.atlascloud.ai/api/v1/model/prediction/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) curl/8.4"
WORK = pathlib.Path(__file__).resolve().parents[1] / "work"
OUT = WORK / "upscaled"


def sh(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def api(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {KEY}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}") from None


def upload(path: pathlib.Path) -> str:
    boundary = f"----up{path.stat().st_size}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: video/mp4\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(UPLOAD, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {KEY}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=900) as r:
        j = json.load(r)
    d = j.get("data") or {}
    url = d.get("download_url") or d.get("url") or j.get("url")
    if not url:
        raise RuntimeError(f"upload returned no url: {str(j)[:300]}")
    return url


def upscale(name: str, src: pathlib.Path) -> None:
    dst = OUT / f"{name}_1080.mp4"
    if dst.exists():
        print(f"[{name}] exists, skipped", flush=True)
        return
    silent = OUT / f"{name}_silent.mp4"
    sh("ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-an", "-c:v", "copy", str(silent))
    rid = None
    r = api(GEN, {"model": "atlascloud/video-upscaler", "video": upload(silent), "target_resolution": "1080p"})
    rid = (r.get("data") or {}).get("id") or r.get("id")
    print(f"[{name}] submitted {rid}", flush=True)
    t0 = time.time()
    while time.time() - t0 < 1800:
        time.sleep(15)
        d = api(POLL + rid)
        d = d.get("data") or d
        if d.get("status") in ("completed", "succeeded"):
            outs = d.get("outputs") or ([d["video_url"]] if d.get("video_url") else [])
            raw = OUT / f"{name}_up_raw.mp4"
            with urllib.request.urlopen(urllib.request.Request(outs[0], headers={"User-Agent": UA}), timeout=900) as resp:
                raw.write_bytes(resp.read())
            sh("ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-i", str(src), "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-c:a", "copy", "-shortest", str(dst))
            raw.unlink(); silent.unlink()
            print(f"[{name}] done {int(time.time() - t0)} s", flush=True)
            return
        if d.get("status") in ("failed", "canceled", "error"):
            raise RuntimeError(f"[{name}] failed: {str(d)[:300]}")
    raise RuntimeError(f"[{name}] timed out, prediction {rid}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for src in sorted(glob.glob(str(WORK / "renders" / "GEN*_v1.mp4" / "spot_*.mp4"))):
        name = pathlib.Path(src).parent.name.split("_")[0]
        upscale(name, pathlib.Path(src))  # sequential on purpose: parallel submits get 403


if __name__ == "__main__":
    main()
