# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "pillow>=10"]
# ///
"""Stage 3: a tool-using inspection agent (Muse Spark) -> checklist + email.

Stage 1 tracked generic objects with SAM 3.1, stage 2 labelled each track with
Muse Spark. Stage 3 does not trust those labels: it gives the model a ruleset
and four tools, and lets it pull evidence again before it decides.

    uv run pipeline/inspect_agent.py --run runs/<run> --video data/<clip>.mp4 \\
        --ruleset rulesets/kr-fire.json --lang en

The loop is a plain Responses-API tool loop (`tools` + `function_call` ->
`function_call_output` with `previous_response_id`). Tools:

    list_tracks(category?)       summary of every labelled track
    view_track(track_id, frame?) re-crop that track at any frame it was tracked
                                 in, returned as images inside the tool result
    get_rule(rule_id)            one ruleset item, with its citation
    submit_report(...)           final structured answer; ends the loop

Every step's request and response are written to `<run>/agent_raw/step_NN.json`
before the answer is parsed, so `--reparse` rebuilds report.json / report.md /
email.md without calling the API. Base64 image payloads are stored once, as the
JPEG files the encode came from (path + sha256 kept in the request record) —
the rest of the request is stored verbatim.

Self-check: submit_report is rejected (the loop continues) when an item claims
ok/issue on a track that was never shown by view_track, or when a ruleset item
is missing. At most MAX_SUBMIT_REJECTIONS rejections, then the report is
accepted and the violations are recorded in report.json — never silently.

Two independent passes: one verdict from a single conversation drifts on the same
pixels (gate/RESULTS.md — the 45 s fire door came back closed twice out of five
runs), so `--passes 2` runs two separate conversations side by side and merges
them item by item. Agreement keeps the verdict; disagreement becomes
needs_human_check with both notes; a pass that never filed falls back to the one
that did, marked single-pass. Raw steps are kept per pass in `agent_raw/passN/`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))
from classify_tracks import extract_frames, probe, save_context, save_crop  # stage-2 image code

BASE_URL = "https://api.meta.ai/v1"
MODEL = "muse-spark-1.3"
# muse-spark-1.3 accepts `temperature` (2026-10-28: a 1-token probe request with
# temperature=0.2 returned HTTP 200 and echoed "temperature": 0.2 back).
TEMPERATURE = 0.2
DEFAULT_MAX_STEPS = 12
DEFAULT_PASSES = 2
MAX_SUBMIT_REJECTIONS = 2
DETAIL_FRAMES = 3
STATUSES = ["ok", "issue", "possibly_missing", "needs_human_check", "not_checkable"]

SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "status": {"type": "string", "enum": STATUSES},
                    "evidence_track_ids": {"type": "array", "items": {"type": "string"}},
                    "frames": {"type": "array", "items": {"type": "integer"}},
                    "note": {"type": "string"},
                },
                "required": ["rule_id", "status", "evidence_track_ids", "frames", "note"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
        "email_draft": {
            "type": "object",
            "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
            "required": ["subject", "body"],
            "additionalProperties": False,
        },
    },
    "required": ["items", "summary", "email_draft"],
    "additionalProperties": False,
}

TOOLS = [
    {
        "type": "function",
        "name": "list_tracks",
        "description": "List the objects stage 2 labelled in this video: track id, category, condition, "
                       "confidence, frame span, the frames where the box is largest (best for a closer "
                       "look) and the one-sentence evidence stage 2 gave. Filter by category if you want.",
        "parameters": {
            "type": "object",
            "properties": {"category": {"type": "string", "description": "optional category filter"}},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "view_track",
        "description": "Look at a track yourself: returns a tight crop plus the full frame with the box drawn, "
                       "as images. Use it before you trust a stage-2 label, and to check the frame where the "
                       "object is largest (detail_frames) when the label was made from a distant frame.",
        "parameters": {
            "type": "object",
            "properties": {
                "track_id": {"type": "string", "description": "e.g. sign_16"},
                "frame": {"type": "integer", "description": "optional frame number this track was seen in; "
                                                            "omit for the largest-box frame"},
            },
            "required": ["track_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_rule",
        "description": "Return one ruleset item: what counts as a violation, whether video can judge it, "
                       "and the legal source with its verification status.",
        "parameters": {
            "type": "object",
            "properties": {"rule_id": {"type": "string"}},
            "required": ["rule_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "submit_report",
        "description": "Submit the final checklist. One item per ruleset item, plus a summary and a "
                       "remediation email. Calling this ends the inspection.",
        "parameters": SUBMIT_SCHEMA,
    },
]


def api_key() -> str:
    key = os.environ.get("META_MODEL_API_KEY")
    if not key:
        sys.exit("META_MODEL_API_KEY is not set (set -a; . ~/.secrets/credentials.env; set +a)")
    return key


# ---------------------------------------------------------------- inputs

def load_ruleset(path: Path) -> tuple[dict, dict[str, dict]]:
    rs = json.loads(path.read_text())
    return rs, {it["id"]: it for it in rs["items"]}


def load_tracks(run: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """track id -> summary, plus merged track id -> representative id."""
    data = json.loads((run / "classified.json").read_text())
    tracks: dict[str, dict] = {}
    merged: dict[str, str] = {}
    for row in data["tracks"]:
        if row["merged_into"]:
            merged[row["track"]] = row["merged_into"]
            continue
        label = row["label"] or {}
        tracks[row["track"]] = {
            "track_id": row["track"],
            "concept": row["concept"],
            "category": label.get("category") or "unclear",
            "condition": label.get("condition") or "needs_human_check",
            "confidence": label.get("confidence"),
            "first_frame": row["first_frame"],
            "last_frame": row["last_frame"],
            "rep_frame": row["rep_frame"],
            "evidence": label.get("evidence") or row.get("error") or "",
        }
    return tracks, merged


def frame_index(run: Path) -> dict[str, dict[int, dict]]:
    """track id -> {frame: {box, wh}} straight from the saved SAM text files."""
    sys.path.insert(0, str(REPO / "gate"))
    from sam_video_gate import parse_tracks

    out: dict[str, dict[int, dict]] = {}
    for txt in sorted(run.glob("*.txt")):
        slug = txt.stem
        for f, objs in parse_tracks(txt.read_text()).items():
            for oid, rec in objs.items():
                out.setdefault(f"{slug}_{oid}", {})[f] = rec
    return out


def fps_of(video: Path) -> float:
    num, _, den = probe(video)["fps"].partition("/")
    try:
        return float(num) / float(den or 1)
    except ValueError:
        return 25.0


def to_seconds(frame: int, fps: float) -> float:
    return round(frame / fps, 1)


def detail_frames(track_frames: dict[int, dict], n: int = DETAIL_FRAMES) -> list[int]:
    """Frames with the largest boxes for this track — stage 2 only ever saw one."""
    ranked = sorted(track_frames, key=lambda f: (track_frames[f]["box"][2] - track_frames[f]["box"][0])
                     * (track_frames[f]["box"][3] - track_frames[f]["box"][1]), reverse=True)
    return sorted(ranked[:n])


# ---------------------------------------------------------------- tools

class Inspector:
    def __init__(self, run: Path, video: Path, fps: float, tracks: dict, merged: dict[str, str],
                 idx: dict[str, dict[int, dict]], rules: dict[str, dict], lang: str):
        self.run, self.video, self.fps = run, video, fps
        self.tracks, self.merged, self.idx, self.rules = tracks, merged, idx, rules
        self.lang = lang
        self.viewed: set[str] = set()
        self.crops_dir = run / "agent_frames"

    # -- tool 1
    def list_tracks(self, category: str | None = None) -> str:
        rows = []
        for tid, t in sorted(self.tracks.items(), key=lambda kv: (kv[1]["first_frame"], kv[0])):
            if category and t["category"] != category:
                continue
            frames = self.idx.get(tid, {})
            rows.append({
                **t,
                "first_second": to_seconds(t["first_frame"], self.fps),
                "last_second": to_seconds(t["last_frame"], self.fps),
                "detail_frames": detail_frames(frames) if frames else [t["rep_frame"]],
            })
        payload = {
            "tracks": rows,
            "count": len(rows),
            "categories_present": sorted({t["category"] for t in self.tracks.values()}),
            "merged_duplicates_not_listed": self.merged,
            "note": "cats/conf/evidence are stage-2 guesses, not facts. detail_frames = frames where the box "
                    "is largest, best candidates for view_track(frame=...).",
        }
        return json.dumps(payload, ensure_ascii=False)

    # -- tool 2
    def view_track(self, track_id: str, frame: int | None = None) -> tuple[str, list[str]]:
        tid = track_id.strip()
        if tid in self.merged:
            text = f"{tid} was merged into {self.merged[tid]} (same pixels); showing {self.merged[tid]}."
            tid = self.merged[tid]
            prefix = text + "\n"
        else:
            prefix = ""
        if tid not in self.tracks:
            return (f"unknown track_id {track_id!r}. Call list_tracks() — do not invent ids."
                    + (f" (merged into {self.merged[track_id]})" if track_id in self.merged else "")), []
        frames = self.idx.get(tid) or {}
        if not frames:
            return f"{tid} has no box records", []
        if frame is None:
            pick, why = self.tracks[tid]["rep_frame"], "largest-box frame"
        else:
            pick = int(frame)
            if pick not in frames:
                pick = min(frames, key=lambda f: abs(f - int(frame)))
                why = f"requested frame {frame} is not a frame this track was seen in; nearest tracked frame {pick}"
            else:
                why = "requested frame"
        crop = self.crops_dir / f"{tid}_f{pick}.jpg"
        ctx = self.crops_dir / f"{tid}_f{pick}_ctx.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            png = extract_frames(self.video, [pick], Path(tmp))[pick]
            from PIL import Image
            im = Image.open(png).convert("RGB")
            rec = frames[pick]
            # Two passes run at the same time and share agent_frames/ (same track,
            # same frame -> same pixels), so write aside and rename: a reader in
            # the other pass never sees a half-written JPEG.
            staged_crop, staged_ctx = Path(tmp) / "crop.jpg", Path(tmp) / "ctx.jpg"
            save_crop(im, rec["box"], rec["wh"], staged_crop)
            save_context(im.copy(), rec["box"], rec["wh"], staged_ctx, f"{tid} frame {pick}")
            os.replace(staged_crop, crop)
            os.replace(staged_ctx, ctx)
        self.viewed.add(tid)
        t = self.tracks[tid]
        x1, y1, x2, y2 = frames[pick]["box"]
        text = (f"{prefix}track {tid} · frame {pick} ({to_seconds(pick, self.fps)} s) · {why} · "
                f"box {int(x2 - x1)}x{int(y2 - y1)} px at {int(x1)},{int(y1)}\n"
                f"stage-2 said: category={t['category']} condition={t['condition']} "
                f"confidence={t['confidence']} — \"{t['evidence']}\"\n"
                f"Image 1 = full frame with the box drawn in magenta (look at what is beside the box: a gap "
                f"or dark opening, a wedge, an obstruction). Image 2 = tight crop (box + 30% margin), which "
                f"cuts that context away — a single door leaf filling a crop can look closed.")
        return text, [str(ctx), str(crop)]

    # -- tool 3
    def get_rule(self, rule_id: str) -> str:
        rule = self.rules.get(rule_id)
        if not rule:
            return f"unknown rule_id {rule_id!r}. Known: {', '.join(self.rules)}"
        return json.dumps(rule, ensure_ascii=False)

    # -- tool 4 validation
    def check_submission(self, args: dict) -> list[str]:
        problems = []
        reported = {it.get("rule_id") for it in args.get("items", [])}
        missing = [rid for rid in self.rules if rid not in reported]
        if missing:
            problems.append(f"missing ruleset items (every item must be reported): {', '.join(missing)}")
        for it in args.get("items", []):
            rid = it.get("rule_id")
            if rid not in self.rules:
                problems.append(f"unknown rule_id {rid!r}")
                continue
            if it.get("status") not in STATUSES:
                problems.append(f"{rid}: status {it.get('status')!r} is not one of {STATUSES}")
                continue
            if it["status"] in {"ok", "issue"}:
                ev = it.get("evidence_track_ids") or []
                if not ev:
                    problems.append(f"{rid}: status {it['status']} needs evidence_track_ids")
                elif not (set(ev) & self.viewed):
                    problems.append(f"{rid}: {it['status']} on {ev} but view_track was never called for "
                                    f"{sorted(set(ev) & set(self.tracks)) or ev} — look at it first")
        return problems


# ---------------------------------------------------------------- loop

def run_instructions(lang: str, rs: dict, stats: dict, max_steps: int) -> str:
    lang_name = "Korean" if lang == "ko" else "English"
    return f"""\
You are the inspection agent of "60-Second Fire Check". Someone walked a building corridor with a phone
for {stats['duration']} s ({stats['frames']} frames). Stage 1 tracked objects with SAM 3.1 on generic
concepts; stage 2 labelled each track with a vision model. Your job is to turn that into a fire-safety
self-inspection checklist under the ruleset "{rs['id']}" — and to look again, with tools, wherever a label
is not enough to decide.

Rules of evidence
1. Decide from images, not from stage-2 labels. Labels are guesses with a confidence value; if a label is
   decisive for an item, call view_track on that track before you rely on it. Stage 2 picked ONE frame per
   track (often the distant one) — view_track(frame=...) can show any frame the track was seen in, and
   list_tracks gives you detail_frames, the frames where the box is largest.
2. Every ruleset item ends in exactly one status:
   - ok                 you saw the item and it is correct.
   - issue              you saw a violation.
   - possibly_missing   the item does not appear anywhere in this footage. Phrase it as "not seen in this
                        walk" — video can never prove something is absent.
   - needs_human_check  the footage cannot settle it (too small, too dark, angle, or the check needs a
                        physical test).
   - not_checkable      video cannot judge this item at all (ruleset says video_checkable=false). Report it
                        anyway, with the reason — the checklist must carry "verify on site" lines.
3. ok/issue must name evidence_track_ids AND at least one frame, and those ids must be tracks you actually
   viewed with view_track. submit_report is rejected otherwise.
4. Never invent a track id or a frame. Never cite a law you did not read from get_rule.
5. Budget: {max_steps} model steps for the whole inspection. tool results and your own turns both count, so
   prefer a few decisive views over many: usually 1 view per rule that has a matching track, not per track.
6. Items whose matches_categories have no track at all: use possibly_missing when the walk should have
   shown such a thing, needs_human_check/not_checkable when the answer is genuinely outside the video.
   An item marked not_checkable in the ruleset must never become ok.
7. Escape routes do not get a "people can still squeeze past" test: a wheelchair, cart, pallet or stack
   parked in a corridor or in front of an exit is itself the violation (법 제16조제1항제2호 forbids
   storing things in or placing obstacles around escape facilities). If such an object is clearly in the
   corridor, status=issue; if you cannot tell whether it sits in the corridor or inside a room,
   needs_human_check.
8. Judge a door from the full frame (image 1), never from the tight crop alone: one leaf filling a crop
   looks like a closed door. A dark opening or gap beside the leaf, or a closer arm stretched across the
   top of an opened leaf, means the door is standing open — a fire door standing open is
   fd_selfclose=issue (add fd_held_open=issue when something holds it there). A fire door you never saw
   standing open but also never saw latched shut is needs_human_check, not ok.
9. Write the checklist from the verdicts you reached while the image was in front of you: the note you
   file must match what you concluded right after view_track. Do not re-decide a door from memory.

When you are done, call submit_report with one item per ruleset item ({len(rs['items'])} items), a summary
of 2-4 sentences that states what this footage can and cannot establish, and an email_draft to the building
manager asking for the fixes — polite, itemised, each line pointing at the video timestamp, and ending with
the limits of the clip. Write the summary, the notes and the email in {lang_name}.
"""


def first_user_message(rs: dict, stats: dict, lang: str) -> dict:
    items = "\n".join(f"- {it['id']}: {it['name_en']} (categories: {', '.join(it['matches_categories'])}; "
                      f"video_checkable={str(it['video_checkable']).lower()})" for it in rs["items"])
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text":
            f"Footage: {stats['video']} — {stats['width']}x{stats['height']}, {stats['frames']} frames, "
            f"{stats['duration']} s.\nRuleset: {rs['id']} ({rs['title_en']}), {len(rs['items'])} items.\n\n"
            f"Ruleset items:\n{items}\n\n"
            f"Start with list_tracks(). Then view what you need, then submit_report."}]}


def strip_images(body: dict, shot_dir: Path) -> dict:
    """Replace base64 image payloads with a pointer to the JPEG they came from."""
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "input_image" and isinstance(o.get("image_url"), str) \
                    and o["image_url"].startswith("data:"):
                raw = base64.b64decode(o["image_url"].partition(",")[2])
                path = next((str(p) for p in shot_dir.glob("*.jpg")
                             if _same_bytes(p, raw)), None)
                return {"type": "input_image",
                        "_stored_as": path or "unknown",
                        "_sha256": hashlib.sha256(raw).hexdigest(),
                        "_bytes": len(raw),
                        "_note": "base64 payload stored once, as the JPEG file above"}
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return walk(body)


def _same_bytes(path: Path, raw: bytes) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(raw).hexdigest()
    except OSError:
        return False


def data_uri(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status, self.message = status, message


def post(client: httpx.Client, body: dict, attempts: int = 4) -> dict:
    """POST /responses, retrying the transient server side (5xx / 429) a few times.

    Seen in the wild: `503 persistence service error` on a step-3 store=true request.
    Retrying the same body is safe — the loop chains from whichever response id comes
    back, so a duplicate stored response only costs tokens, never correctness.
    """
    last = (0, "")
    for n in range(attempts):
        r = client.post("/responses", json=body, timeout=600)
        if r.status_code == 200:
            return r.json()
        last = (r.status_code, r.text[:400])
        if r.status_code < 500 and r.status_code != 429:
            break
        wait = 5 * 3 ** n
        print(f"    HTTP {last[0]}: {last[1][:160]} — retry in {wait}s ({n + 1}/{attempts})")
        time.sleep(wait)
    raise ApiError(*last)


def ask(client: httpx.Client, body: dict, history: list) -> dict:
    """One model step, falling back to a stateless resend if the chain broke.

    The API also drops stored responses (`400 referenced response not found or
    expired`), which killed a run at step 6. When that happens the whole transcript
    is resent without previous_response_id — same conversation, no chain to lose.
    """
    try:
        return post(client, body)
    except ApiError as exc:
        broken = "referenced response" in exc.message.lower() or "reasoning item" in exc.message.lower()
        if exc.status == 400 and broken and body.get("previous_response_id"):
            print("    previous response expired — resending the full transcript statelessly")
            body.pop("previous_response_id")
            # Reasoning items point into the stored response that just expired, so
            # replaying them without the chain is exactly what the API rejects
            # (`Referenced reasoning item ... was not found or has expired`).
            # Messages, tool calls and tool outputs are all the conversation needs.
            body["input"] = [h for h in history if h.get("type") != "reasoning"]
            return post(client, body)
        raise


def output_text(raw: dict) -> str:
    return "".join(p.get("text", "") for item in raw.get("output", [])
                   if item.get("type") == "message" for p in item.get("content", [])
                   if p.get("type") == "output_text")


def run_loop(run: Path, video: Path, rs: dict, rules: dict[str, dict], tracks: dict, merged: dict,
             idx: dict, lang: str, max_steps: int, stats: dict, on_step=None,
             pass_no: int = 1) -> dict:
    """One independent inspection conversation. Two of these (pass 1 and 2) are run at the
    same time on the same inputs; neither sees the other's response ids or transcript."""
    raw_dir = run / "agent_raw" / f"pass{pass_no}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for old in raw_dir.glob("step_*.json"):
        old.unlink()  # a fresh run owns the directory; --reparse reads these files
    (run / "agent_frames").mkdir(exist_ok=True)
    tag = f"[pass {pass_no}] "
    insp = Inspector(run, video, stats["fps"], tracks, merged, idx, rules, lang)
    instructions = run_instructions(lang, rs, stats, max_steps)
    trace: list[dict] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    submission: dict | None = None
    rejections: list[list[str]] = []
    stop = "max_steps"
    inputs = [first_user_message(rs, stats, lang)]
    history = list(inputs)  # everything sent so far, for the stateless fallback
    prev_id = None

    with httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {api_key()}"}) as client:
        for step in range(1, max_steps + 1):
            body = {"model": MODEL, "instructions": instructions, "tools": TOOLS,
                    "parallel_tool_calls": False, "input": inputs, "store": True,
                    "temperature": TEMPERATURE}
            if prev_id:
                body["previous_response_id"] = prev_id
            raw = ask(client, body, history)
            step_path = raw_dir / f"step_{step:02d}.json"
            record = {"step": step, "pass": pass_no,
                      "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "request": strip_images(body, run / "agent_frames"), "response": raw}
            step_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
            step_trace: list[dict] = []
            for k in usage_total:
                usage_total[k] += (raw.get("usage") or {}).get(k) or 0
            prev_id = raw["id"]
            history += raw["output"]
            calls = [o for o in raw["output"] if o.get("type") == "function_call"]
            said = output_text(raw)
            print(f"  {tag}step {step}: {[c['name'] for c in calls] or 'text'}"
                  + (f" — {said[:90]!r}" if said else ""))

            if not calls:
                stop = "model_replied_without_tool"
                inputs = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text":
                           "Call submit_report now (or another tool first). Free text is not a result."}]}]
                history += inputs
                continue

            outputs = []
            for call in calls:
                name = call["name"]
                try:
                    args = json.loads(call.get("arguments") or "{}")
                except json.JSONDecodeError:
                    outputs.append({"type": "function_call_output", "call_id": call["call_id"],
                                    "output": f"arguments were not valid JSON: {call.get('arguments')!r}"})
                    step_trace.append({"step": step, "tool": name, "args": None, "error": "bad json"})
                    continue

                if name == "list_tracks":
                    text = insp.list_tracks(args.get("category"))
                    outputs.append({"type": "function_call_output", "call_id": call["call_id"], "output": text})
                    step_trace.append({"step": step, "tool": name, "args": args, "chars": len(text)})
                elif name == "view_track":
                    text, images = insp.view_track(args.get("track_id", ""), args.get("frame"))
                    outputs.append({"type": "function_call_output", "call_id": call["call_id"],
                                    "output": [{"type": "input_text", "text": text}]
                                    + [{"type": "input_image", "image_url": data_uri(Path(p))} for p in images]})
                    step_trace.append({"step": step, "tool": name, "args": args, "images": images,
                                       "chars": len(text)})
                elif name == "get_rule":
                    text = insp.get_rule(args.get("rule_id", ""))
                    outputs.append({"type": "function_call_output", "call_id": call["call_id"], "output": text})
                    step_trace.append({"step": step, "tool": name, "args": args, "chars": len(text)})
                elif name == "submit_report":
                    problems = insp.check_submission(args)
                    if problems and len(rejections) < MAX_SUBMIT_REJECTIONS:
                        rejections.append(problems)
                        msg = ("submit_report rejected — fix these and call it again:\n- "
                               + "\n- ".join(problems))
                        outputs.append({"type": "function_call_output", "call_id": call["call_id"],
                                        "output": msg})
                        step_trace.append({"step": step, "tool": name, "args": {"rejected": True},
                                           "problems": problems})
                        print(f"    {tag}rejected: {len(problems)} problem(s)")
                    else:
                        submission = args
                        stop = "submitted"
                        step_trace.append({"step": step, "tool": name,
                                           "args": {"rules": len(args.get("items", []))},
                                           "accepted": True, "unresolved": problems})
                        print(f"    {tag}accepted: {len(args.get('items', []))} items"
                              + (f" ({len(problems)} unresolved check(s))" if problems else ""))
                        break
                else:
                    outputs.append({"type": "function_call_output", "call_id": call["call_id"],
                                    "output": f"unknown tool {name!r}"})
                    step_trace.append({"step": step, "tool": name, "args": args, "error": "unknown tool"})
            trace += step_trace
            record["executed"] = step_trace  # so --reparse can rebuild the trace without tools
            step_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
            if on_step:
                on_step(pass_no, step, step_trace)
            if submission is not None:
                break
            inputs = outputs
            history += outputs

    return {"pass": pass_no, "submission": submission, "trace": trace, "usage": usage_total,
            "stop_reason": stop, "rejections": rejections,
            "viewed_tracks": sorted(insp.viewed)}


# ---------------------------------------------------------------- agreement

DISAGREE_NOTE = "two independent checks disagreed: pass1 {s1} ({n1}) / pass2 {s2} ({n2})"


def _pass_items(outcome: dict) -> dict[str, dict]:
    sub = outcome.get("submission") or {}
    return {it.get("rule_id"): it for it in sub.get("items", [])}


def combine_outcomes(outcomes: list[dict], rs: dict) -> dict:
    """Merge the independent passes item by item.

    Same status twice -> that status (pass-1 note, union of the evidence).
    Different statuses -> needs_human_check, both verdicts in the note. One pass
    could not answer -> that pass's verdict, agreement unknown. Video cannot judge
    an item (video_checkable=false) -> not_checkable, never merged.
    """
    maps = [_pass_items(o) for o in outcomes]
    merged: dict[str, dict] = {}
    for rule in rs["items"]:
        rid = rule["id"]
        per = []
        for outcome, got in zip(outcomes, maps):
            it = got.get(rid)
            if not it:
                continue
            per.append({"pass": outcome.get("pass", 0), "status": it.get("status"),
                        "note": it.get("note") or "",
                        "evidence": it.get("evidence_track_ids") or [],
                        "frames": [int(f) for f in it.get("frames") or []]})
        if not per:
            continue  # build_report autofills it
        frames = sorted({f for p in per for f in p["frames"]})
        evidence = list(dict.fromkeys(t for p in per for t in p["evidence"]))
        if not rule["video_checkable"]:
            merged[rid] = {"status": "not_checkable", "note": per[0]["note"],
                           "evidence_track_ids": evidence, "frames": frames,
                           "agreement": None, "passes": per}
            continue
        if len(per) < 2:
            merged[rid] = {"status": per[0]["status"], "note": per[0]["note"],
                           "evidence_track_ids": evidence, "frames": frames,
                           "agreement": None, "passes": per}
            continue
        a, b = per[0], per[1]
        if a["status"] == b["status"]:
            merged[rid] = {"status": a["status"], "note": a["note"],
                           "evidence_track_ids": evidence, "frames": frames,
                           "agreement": True, "passes": per}
        else:
            merged[rid] = {"status": "needs_human_check",
                           "note": DISAGREE_NOTE.format(s1=a["status"], n1=a["note"] or "no note",
                                                        s2=b["status"], n2=b["note"] or "no note"),
                           "evidence_track_ids": evidence, "frames": frames,
                           "agreement": False, "passes": per}

    submitted = [o for o in outcomes if o.get("submission") is not None]
    lead = (submitted[0].get("submission") if submitted else None) or {}
    usage = {k: sum(o["usage"].get(k) or 0 for o in outcomes)
             for k in ("input_tokens", "output_tokens", "total_tokens")}
    trace = []
    for o in outcomes:
        for entry in o["trace"]:
            trace.append({**entry, "pass": o.get("pass", 0)})
    return {
        "submission": lead if submitted else None,
        "merged_items": merged,
        "trace": trace,
        "usage": usage,
        "stop_reason": "submitted" if submitted else " / ".join(
            f"pass{o.get('pass', 0)}: {o['stop_reason']}" for o in outcomes),
        "rejections": [r for o in outcomes for r in o["rejections"]],
        "viewed_tracks": sorted({t for o in outcomes for t in o["viewed_tracks"]}),
        "passes": [{"pass": o.get("pass", 0), "stop_reason": o["stop_reason"],
                    "partial": o.get("submission") is None, "usage": o["usage"],
                    "viewed_tracks": o["viewed_tracks"],
                    "rejections": len(o["rejections"])} for o in outcomes],
        "checks": len(outcomes),
        "single_pass": len(submitted) < 2,
    }


# ---------------------------------------------------------------- report

STATUS_LABEL = {
    "en": {"ok": "OK", "issue": "ISSUE", "possibly_missing": "possibly missing (not seen in this walk)",
           "needs_human_check": "needs human check", "not_checkable": "verify on site (not judgeable from video)"},
    "ko": {"ok": "양호", "issue": "위반", "possibly_missing": "영상 내 미확인(이 구간에서 안 보임)",
           "needs_human_check": "현장 확인 필요", "not_checkable": "현장 확인 필요(영상 판정 불가)"},
}
HEAD = {
    "en": {"title": "Fire-safety self-inspection report", "item": "Item", "result": "Result",
           "evidence": "Evidence (video time)", "note": "Note", "source": "Source",
           "summary": "Summary", "site": "Needs a person on site", "how": "How the agent checked",
           "limits": "What this report can and cannot say"},
    "ko": {"title": "소방시설 외관 점검표", "item": "점검 항목", "result": "결과",
           "evidence": "근거 영상 시점(초)", "note": "비고", "source": "출처",
           "summary": "총평", "site": "현장 확인 필요 항목", "how": "에이전트가 확인한 순서",
           "limits": "이 보고서가 말할 수 있는 것과 없는 것"},
}


def build_report(run: Path, video: Path, rs: dict, rules: dict[str, dict], tracks: dict, idx: dict,
                 fps: float, lang: str, outcome: dict, stats: dict) -> dict:
    sub = outcome["submission"] or {}
    got = outcome.get("merged_items") or {}
    autofilled = []
    items = []
    for rule in rs["items"]:
        it = got.get(rule["id"])
        if not it:
            autofilled.append(rule["id"])
            it = {"status": "needs_human_check", "evidence_track_ids": [], "frames": [],
                  "agreement": None, "passes": [],
                  "note": "not reported by the agent" if lang == "en" else "에이전트가 보고하지 않음"}
        frames = [int(f) for f in it.get("frames") or []]
        items.append({
            "rule_id": rule["id"],
            "name_en": rule["name_en"],
            "name_ko": rule["name_ko"],
            "status": it.get("status"),
            "video_checkable": rule["video_checkable"],
            "agreement": it.get("agreement"),
            "passes": it.get("passes") or [],
            "evidence_track_ids": it.get("evidence_track_ids") or [],
            "frames": frames,
            "seconds": [to_seconds(f, fps) for f in frames],
            "note": it.get("note") or "",
            "source": rule["source"],
            "source_url": rule.get("source_url"),
            "source_status": rule.get("source_status"),
            "source_note": rule.get("source_note"),
        })
    counts: dict[str, int] = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    disagree = [it["rule_id"] for it in items if it["agreement"] is False]
    return {
        "run": str(run), "video": str(video), "ruleset": rs["id"], "ruleset_title": rs.get("title_en"),
        "model": MODEL, "lang": lang, "temperature": TEMPERATURE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "partial": outcome["submission"] is None,
        "stop_reason": outcome["stop_reason"],
        "footage": stats,
        "counts": counts,
        "checks": outcome.get("checks", 1),
        "single_pass": outcome.get("single_pass", True),
        "disagreements": disagree,
        "passes": outcome.get("passes", []),
        "summary": sub.get("summary") or "",
        "email_draft": sub.get("email_draft") or {},
        "items": items,
        "autofilled_rule_ids": autofilled,
        "submit_rejections": outcome["rejections"],
        "viewed_tracks": outcome["viewed_tracks"],
        "trace": outcome["trace"],
        "usage": outcome["usage"],
    }


def write_report(rep: dict, run: Path, lang: str) -> None:
    (run / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    h = HEAD[lang]
    lab = STATUS_LABEL[lang]
    lines = [f"# {h['title']}", "",
             f"`{rep['video']}` · {rep['footage']['width']}x{rep['footage']['height']} · "
             f"{rep['footage']['frames']} frames · {rep['footage']['duration']} s · "
             f"ruleset `{rep['ruleset']}` · lang `{rep['lang']}` · agent `{rep['model']}`", "",
             f"{rep['footage']['tracks']} labelled tracks · {len(rep['viewed_tracks'])} looked at again by the "
             f"agent ({', '.join(rep['viewed_tracks']) or 'none'}) · "
             + " · ".join(f"{k}: {v}" for k, v in sorted(rep["counts"].items())), "",
             f"{rep.get('checks', 1)} independent check{'s' if rep.get('checks', 1) > 1 else ''} per item"
             + (f" · **{len(rep['disagreements'])} item(s) disagreed** ("
                + ", ".join(f"`{r}`" for r in rep["disagreements"]) + " → needs a person)"
                if rep["disagreements"] else " · every item got the same verdict in every check")
             + (" · **single pass** — one check did not file a report, so those items have one verdict"
                if rep.get("single_pass") and rep.get("checks", 1) > 1 else ""), ""]
    if rep["partial"]:
        lines += [f"> **INCOMPLETE** — the agent stopped before submit_report ({rep['stop_reason']}). "
                  f"Every item below is unfilled.", ""]
    if rep["summary"]:
        from_check = ""
        if rep.get("checks", 1) > 1:
            from_check = (f" — check 1 of {rep['checks']}" if lang == "en"
                          else f" — 점검 1/{rep['checks']}")
        lines += [f"## {h['summary']}{from_check}", "", rep["summary"], ""]
    lines += [f"| # | {h['item']} | {h['result']} | {h['evidence']} | {h['note']} | {h['source']} |",
              "|---|---|---|---|---|---|"]
    for n, it in enumerate(rep["items"], 1):
        name = it["name_ko"] if lang == "ko" else it["name_en"]
        ev = ", ".join(f"{s} s (frame {f})" for s, f in zip(it["seconds"], it["frames"])) or "—"
        note = (it["note"] or "").replace("|", "/").replace("\n", " ")
        src = it["source"].replace("|", "/")
        flag = "" if it["source_status"] == "verified" else f" ⚠️{it['source_status']}"
        lines.append(f"| {n} | {name} (`{it['rule_id']}`) | {lab[it['status']]} | {ev} | {note} | {src}{flag} |")
    site = [it for it in rep["items"] if it["status"] in {"needs_human_check", "not_checkable"}]
    if site:
        lines += ["", f"## {h['site']}", ""]
        lines += [f"- `{it['rule_id']}` — {it['note']}" for it in site]
    lines += ["", f"## {h['how']}", ""]
    if rep.get("checks", 1) > 1:
        lines += ["| check | step | tool | args | result |", "|---|---|---|---|---|"]
    else:
        lines += ["| step | tool | args | result |", "|---|---|---|---|"]
    for t in rep["trace"]:
        res = (f"{t['chars']} chars" if t.get("chars") else "")
        if t.get("images"):
            res += f", {len(t['images'])} images ({', '.join(Path(p).name for p in t['images'])})"
        if t.get("problems"):
            res += f" · rejected: {'; '.join(t['problems'])}"
        if t.get("error"):
            res += f" · {t['error']}"
        if t.get("accepted"):
            res += " · accepted"
        args = t.get("args")
        cells = (f"{t.get('pass', 1)} | " if rep.get("checks", 1) > 1 else "") + \
            f"{t['step']} | {t['tool']} | `{json.dumps(args, ensure_ascii=False) if args else ''}` | {res} |"
        lines.append(f"| {cells}")
    lines += ["", f"## {h['limits']}", "",
              "- Absence is never proven: `possibly_missing` means \"not seen in this walk\", nothing more.",
              "- Items with ⚠️unverified sources cite the standard only; no clause number is asserted.",
              "- Checks that need a physical test (pressure gauge, lamp test, hose inside a closed cabinet) stay "
              "`needs_human_check` / `not_checkable` even when the object is visible.",
              f"- {len(rep['items'])} ruleset items · {rep['footage']['frames']} frames · "
              f"usage {rep['usage']}", ""]
    (run / "report.md").write_text("\n".join(lines))

    mail = rep.get("email_draft") or {}
    if mail:
        (run / "email.md").write_text(f"Subject: {mail.get('subject', '')}\n\n{mail.get('body', '')}\n")


# ---------------------------------------------------------------- reparse

def reparse_outcome(raw_dir: Path, pass_no: int = 1) -> dict:
    """Rebuild one pass's outcome from its saved step files — no API calls."""
    steps = sorted(raw_dir.glob("step_*.json"))
    if not steps:
        sys.exit(f"no step_*.json in {raw_dir}")
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    trace: list[dict] = []
    submission, rejections = None, []
    for path in steps:
        rec = json.loads(path.read_text())
        for k in usage:
            usage[k] += (rec["response"].get("usage") or {}).get(k) or 0
        trace += rec.get("executed") or []
        for call in [o for o in rec["response"]["output"] if o.get("type") == "function_call"]:
            if call["name"] != "submit_report":
                continue
            mark = next((e for e in rec.get("executed") or [] if e["tool"] == "submit_report"), {})
            if mark.get("accepted"):
                submission = json.loads(call.get("arguments") or "{}")
            elif mark.get("problems"):
                rejections.append(mark["problems"])
    return {"pass": pass_no, "submission": submission, "trace": trace, "usage": usage,
            "stop_reason": "submitted" if submission else "no_accepted_submit_in_raw",
            "rejections": rejections,
            "viewed_tracks": sorted({e["args"].get("track_id", "") for e in trace
                                     if e["tool"] == "view_track" and e.get("args")} - {""})}


def reparse_passes(run: Path) -> list[dict]:
    """agent_raw/passN/ (two-pass runs) or agent_raw/step_*.json (single-pass runs, older)."""
    base = run / "agent_raw"
    dirs = [d for d in base.glob("pass*") if d.is_dir()]
    if dirs:
        def number(d: Path) -> int:
            tail = d.name[4:]
            return int(tail) if tail.isdigit() else 9_999
        return [reparse_outcome(d, number(d)) for d in sorted(dirs, key=number)]
    return [reparse_outcome(base)]


def inspect(run: Path, video: Path, ruleset: Path = REPO / "rulesets" / "kr-fire.json",
            lang: str = "en", max_steps: int = DEFAULT_MAX_STEPS, reparse: bool = False,
            passes: int = DEFAULT_PASSES, on_step=None) -> dict:
    """Run (or reparse) stage 3 for one run dir and write report.json/md + email.md.

    `passes` independent conversations run at the same time on the same inputs (no
    shared response ids); an item the passes see differently comes out as
    needs_human_check instead of whichever pass looked first. `on_step(pass_no,
    step, executed)` fires after each model step with that step's tool trace, so an
    orchestrator can stream tool calls to a UI. Returns the report dict (`partial`
    is True when no pass got submit_report accepted).
    """
    if not (run / "classified.json").exists():
        raise FileNotFoundError(f"{run}/classified.json missing — run classify_tracks first")
    rs, rules = load_ruleset(ruleset)
    tracks, merged = load_tracks(run)
    idx = frame_index(run)
    meta = probe(video)
    fps = fps_of(video)
    stats = {"video": video.name, "width": meta["width"], "height": meta["height"],
             "frames": meta["frames"], "duration": round(meta["frames"] / fps, 1),
             "tracks": len(tracks), "fps": round(fps, 3)}
    print(f"{run.name}: {len(tracks)} tracks · ruleset {rs['id']} ({len(rs['items'])} items) · "
          f"{stats['duration']} s · lang {lang}")

    if reparse:
        outcomes = reparse_passes(run)
    else:
        for old in (run / "agent_raw").glob("step_*.json"):
            old.unlink()  # single-pass leftovers would be stale next to the pass dirs
        passes = max(1, passes)
        if passes == 1:
            outcomes = [run_loop(run, video, rs, rules, tracks, merged, idx, lang,
                                 max_steps, stats, on_step=on_step)]
        else:
            with ThreadPoolExecutor(max_workers=passes) as pool:
                futures = [pool.submit(run_loop, run, video, rs, rules, tracks, merged, idx,
                                       lang, max_steps, stats, on_step, n)
                           for n in range(1, passes + 1)]
                outcomes = [f.result() for f in futures]
    outcome = combine_outcomes(outcomes, rs)

    rep = build_report(run, video, rs, rules, tracks, idx, fps, lang, outcome, stats)
    write_report(rep, run, lang)
    print(f"-> {run}/report.json · report.md · email.md"
          f" ({'partial' if rep['partial'] else 'complete'}, {rep['counts']}, "
          f"{len(rep['disagreements'])} disagreement(s), usage {rep['usage']['total_tokens']})")
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True, help="run dir containing classified.json")
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--ruleset", type=Path, default=REPO / "rulesets" / "kr-fire.json")
    ap.add_argument("--lang", choices=["en", "ko"], default="en")
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    ap.add_argument("--passes", type=int, default=DEFAULT_PASSES,
                    help="independent inspections to run and merge (default 2)")
    ap.add_argument("--reparse", action="store_true", help="rebuild report from agent_raw, no API")
    args = ap.parse_args()

    try:
        rep = inspect(args.run, args.video, args.ruleset, args.lang, args.max_steps, args.reparse,
                      args.passes)
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    if rep["partial"]:
        sys.exit(2)


if __name__ == "__main__":
    main()