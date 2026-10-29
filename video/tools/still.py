#!/usr/bin/env python3
"""One 16:9 still through Codex's built-in image_gen (ChatGPT subscription, no API charge).

    python3 video/tools/still.py <prompt.txt> <out.png> [--ref a.png --ref b.png]

Codex pitfalls this follows (~/Claude/knowledge/tools/codex-imagegen-subscription.md): the prompt goes in on
stdin, there is no size argument (landscape comes back at a fixed size), the file lands under
~/.codex/generated_images/ so we ask Codex to copy it and fall back to the newest PNG there.
Every call is appended to video/work/stills.jsonl.
"""
import argparse, json, os, pathlib, shutil, subprocess, sys, time

GEN_DIR = pathlib.Path.home() / ".codex" / "generated_images"
LOG = pathlib.Path(__file__).resolve().parents[1] / "work" / "stills.jsonl"
HEADER = (
    "Use ONLY your built-in `image_gen` tool. Do NOT use any API key or write scripts that call OpenAI. "
    "Pass the SPEC below to image_gen VERBATIM as the prompt — do not restructure, shorten or augment it. "
    "The image must be LANDSCAPE 16:9. Produce exactly ONE image{refs}, then copy that exact output file "
    "(no re-encoding, no resizing) to this absolute path and confirm the size with "
    "`sips -g pixelWidth -g pixelHeight`:\n  {out}\n\nSPEC:\n"
)


def newest_png(since: float):
    pngs = [p for p in GEN_DIR.rglob("*.png") if p.stat().st_mtime >= since]
    return max(pngs, key=lambda p: p.stat().st_mtime) if pngs else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt_file")
    ap.add_argument("out")
    ap.add_argument("--ref", action="append", default=[], help="reference image (repeatable)")
    a = ap.parse_args()
    out = pathlib.Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    refs = [str(pathlib.Path(r).resolve()) for r in a.ref]
    ref_txt = (", using these reference images for look and layout: " + ", ".join(refs)) if refs else ""
    prompt = HEADER.format(out=out, refs=ref_txt) + pathlib.Path(a.prompt_file).read_text()
    work = out.parent / ".codex_work"
    work.mkdir(exist_ok=True)
    cmd = ["codex", "exec", "--skip-git-repo-check", "-C", str(work), "--json",
           "-o", str(work / f"{out.stem}.last.txt")]
    for r in refs:
        cmd += ["-i", r]
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}  # force the subscription path
    t0 = time.time()
    r = subprocess.run(cmd, input=prompt, text=True, capture_output=True, timeout=900, env=env)
    (work / f"{out.stem}.events.jsonl").write_text(r.stdout)
    fallback = False
    if not out.exists():
        g = newest_png(t0)
        if g:
            shutil.copy2(g, out)
            fallback = True
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "out": str(out), "refs": refs, "rc": r.returncode,
           "sec": round(time.time() - t0, 1), "ok": out.exists(), "fallback_pick": fallback,
           "stderr_tail": r.stderr[-300:] if not out.exists() else ""}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False))
    sys.exit(0 if out.exists() else 1)


if __name__ == "__main__":
    main()
