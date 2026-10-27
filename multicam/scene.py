"""Scene mode: "talk to your other self". One real take becomes a two-person shot / reverse-shot scene.

Speaker A is you: the real take (CAM A) plus CrossView re-shoots of the same take (A side / A wide).
Speaker B is the partner, answering between your sentences:
  clone      your other self: same face (Qwen-Image-Edit of your own frame, mirrored to face the other way),
             a different wardrobe / light / room, and YOUR voice (the LTX-2.3 ID-LoRA voice reference is a
             slice of your real audio)
  photo      a person from a photo you upload (only photos of people who consented)
  character  a fictional AI person made from a text description (Qwen-Image-Edit)
B's lines are typed (or suggested from the transcript) and spoken by LTX-2.3 + ID LoRA from the exact text
([SPEECH]), so the lip sync is native. B's voice reference: your own voice (clone) or an open TTS voice
(Kokoro-82M, Apache-2.0; pre-rendered samples ship in multicam/voices/).

Editing grammar (180-degree rule): A's side angle looks screen-LEFT, every B shot looks screen-RIGHT
(clone stills are mirrored for that), B gets a single, an over-the-shoulder (A's shoulder in the right
foreground) and a close-up. A turn after a B line opens on A's side angle (matched eyeline), long A turns
get an L-cut to B listening (A's audio keeps running). Each speaker gets at least two angles.

Files (work dir):  scene.json (state), transcript.json, stills/, clips/, refs/, scene.mp4
"""
from __future__ import annotations

import json
import math
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .util import load_json, need, probe, run, save_json

WF_DIR = Path(__file__).resolve().parent.parent / "workflows"
VOICE_DIR = Path(__file__).resolve().parent / "voices"
PARTNER_MODES = ("clone", "photo", "character")
OUT_W, OUT_H, OUT_FPS = 720, 1280, 30

LOOKS = {
    "night": ("a black leather jacket over a plain white t-shirt",
              "a dark apartment at night lit by cold blue-teal moonlight, blurred city lights behind",
              "dark apartment at night, cold teal moonlight"),
    "noir": ("an olive green hoodie", "a dark room with a single hard cold white key light and deep shadows",
             "dark room, hard cold key light, noir"),
    "office": ("a crisp white shirt and a dark navy suit jacket",
               "a bright minimal office in warm golden late-afternoon sunlight, plants behind",
               "bright office, warm golden sunlight"),
    "studio": ("a black turtleneck", "an industrial loft with a red brick wall in teal and orange cinematic light",
               "brick loft, teal and orange light"),
}
# B framings (all look screen-right, toward A who is off-screen right)
B_SHOTS = ("single", "ots", "close")
VOICES = {  # Kokoro-82M (Apache-2.0) samples rendered by `multicam scene voices`
    "af_heart": "female, warm (US)", "af_bella": "female, bright (US)", "bf_emma": "female (UK)",
    "am_michael": "male, calm (US)", "am_fenrir": "male, deep (US)", "bm_george": "male (UK)",
}
VOICE_TEXT = ("Honestly, I think the agents are great at the boring parts. But somebody still has to decide "
              "what to build, and why it matters.")
JOB_COST = {"stills": 0.06, "line": 0.033, "react": 0.033, "reshoot": 0.055}


# ------------------------------------------------------------------ Comfy helper
def run_graph(workflow: dict, bindings: dict, values: dict, out_dir: Path, files: tuple[str, ...] = (),
              key=None, rename: dict | None = None, on_status: Callable[[dict], None] | None = None) -> dict:
    """Run one API-format graph: values named in `files` are local paths to upload; all outputs are downloaded."""
    from .comfy import IMAGE_EXT, VIDEO_EXT, download, find_files, job_detail, patch_workflow, submit, upload, wait
    values = dict(values)
    rec = {"status": "uploading", "started": time.time(), "outputs": []}
    emit = (lambda: on_status(dict(rec))) if on_status else (lambda: None)
    emit()
    cache: dict[str, str] = {}
    for k in files:
        if values.get(k):
            src = str(values[k])
            if src not in cache:
                cache[src] = upload(src, key)
            values[k] = cache[src]
    rec["prompt_id"] = submit(patch_workflow(workflow, bindings, values), key)
    rec["status"] = "submitted"; emit()

    def st_cb(st):
        rec["status"] = st; emit()
    st = wait(rec["prompt_id"], key, on_status=st_cb)
    rec["elapsed_s"] = round(time.time() - rec["started"], 1)
    if st == "success":
        out = Path(out_dir)
        for fi in find_files(job_detail(rec["prompt_id"], key).get("outputs"), IMAGE_EXT + VIDEO_EXT):
            if fi.get("type", "output") != "output":
                continue
            name = (rename or {}).get(fi["node"])
            dest = out / (f"{name}{Path(fi['filename']).suffix}" if name else
                          f"{fi['node'].replace(':', '_')}_{Path(fi['filename']).name}")
            rec["outputs"].append(str(download(fi, dest, key)))
    else:
        rec["error"] = json.dumps(job_detail(rec["prompt_id"], key).get("execution_error"))[:1500]
    emit()
    return rec


def _wf(name: str) -> tuple[dict, dict, dict]:
    from .comfy import load_bindings
    raw = json.loads((WF_DIR / f"{name}_bindings.json").read_text())
    return json.loads((WF_DIR / f"{name}_api.json").read_text()), load_bindings(WF_DIR / f"{name}_bindings.json"), raw


# ------------------------------------------------------------------ state
def load(work: Path) -> dict:
    return load_json(Path(work) / "scene.json")


def save(work: Path, sc: dict) -> None:
    save_json(sc, Path(work) / "scene.json")


def parse_lines(items: list[str] | None, n_sentences: int) -> list[dict]:
    """'2:Wait, really?' -> B speaks after A's sentence #2 (0-based). Without an index: after successive sentences."""
    out, nxt = [], 0
    for it in items or []:
        m = re.match(r"^\s*(\d+)\s*[:=]\s*(.+)$", it)
        after, text = (int(m.group(1)), m.group(2)) if m else (nxt, it)
        out.append({"after": min(after, n_sentences - 1), "text": text.strip()})
        nxt = after + 1
    out.sort(key=lambda x: x["after"])
    return out


def suggest_lines(tr: dict, partner: str = "clone", max_lines: int = 3) -> list[dict]:
    """Partner replies from the transcript. Uses an OpenAI-compatible LLM if MULTICAM_LLM_URL/MULTICAM_LLM_KEY are
    set (optional), else simple skeptical templates. Manual lines are the default; this only pre-fills them."""
    import os
    sents = tr["sentences"]
    url, key = os.environ.get("MULTICAM_LLM_URL"), os.environ.get("MULTICAM_LLM_KEY")
    if url and key:
        try:
            import requests
            numbered = "\n".join(f"{s['i']}: {s['text']}" for s in sents)
            who = "the speaker's skeptical other self" if partner == "clone" else "a sharp podcast co-host"
            msg = (f"Here is a monologue split into numbered sentences:\n{numbered}\n\nWrite {max_lines} short replies "
                   f"(max 10 words each) by {who}, turning it into a dialogue. Answer ONLY JSON: "
                   '[{"after": <sentence number>, "text": "..."}]')
            r = requests.post(url.rstrip("/") + "/chat/completions", timeout=60,
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": os.environ.get("MULTICAM_LLM_MODEL", "gpt-4o-mini"),
                                    "messages": [{"role": "user", "content": msg}]})
            txt = r.json()["choices"][0]["message"]["content"]
            got = json.loads(txt[txt.index("["): txt.rindex("]") + 1])
            return [{"after": int(g["after"]), "text": str(g["text"])[:120]} for g in got][:max_lines]
        except Exception:  # noqa: BLE001 - fall back to templates
            pass
    tmpl = ["Wait. Really?", "Come on. Prove it.", "And you're sure about that?", "That's a bold claim.",
            "Okay, but at what cost?"]
    idx = [s["i"] for s in sents[:-1]] or [0]
    step = max(1, len(idx) // max_lines)
    return [{"after": i, "text": tmpl[k % len(tmpl)]} for k, i in enumerate(idx[step - 1::step][:max_lines])]


def init(video: str | Path, work: Path, partner: str = "clone", lines: list[str] | None = None,
         look: str = "night", photo: str | None = None, character: str | None = None, voice: str | None = None,
         person: str = "man", a_look: str | None = None, script: str | None = None, suggest: bool = False,
         transcript: str | None = None, model: str = "small") -> dict:
    """Transcribe the real take (free) and write scene.json."""
    if partner not in PARTNER_MODES:
        raise SystemExit(f"--partner must be one of {PARTNER_MODES}")
    if partner == "photo" and not photo:
        raise SystemExit("--partner photo needs --photo (only photos of people who consented)")
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    video = Path(video).resolve()
    trp = work / "transcript.json"
    if transcript:
        save_json(load_json(transcript), trp)
    elif not trp.exists():
        from .transcribe import analyze
        analyze(video, trp, model=model, script=script)
    tr = load_json(trp)
    ls = parse_lines(lines, len(tr["sentences"])) if lines else (suggest_lines(tr, partner) if suggest else [])
    sc = {"version": 1, "source": str(video), "transcript": str(trp), "partner": partner, "person": person,
          "look": look, "photo": str(Path(photo).resolve()) if photo else None, "character": character,
          "voice": voice or ("self" if partner == "clone" else "af_heart"),
          "a_look": a_look or "the other speaker", "lines": ls, "stills": {}, "a_clips": {}, "b_clips": {},
          "jobs": [], "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    save(work, sc)
    return sc


# ------------------------------------------------------------------ prompts
def _look(sc) -> tuple[str, str, str]:
    lk = sc.get("look") or "night"
    if lk in LOOKS:
        return LOOKS[lk]
    return (lk, "a different room with different lighting", lk)  # free text: "<wardrobe>; <place/light>"


def _ots(sc) -> str:
    return (" Add an over-the-shoulder foreground: on the right edge of the frame, the out-of-focus back of the head "
            f"and shoulder of {sc.get('a_look') or 'another person'}. Shallow depth of field.")


def still_prompts(sc) -> list[tuple[str, str, float]]:
    """8 Qwen-Image-Edit branches -> [(role, prompt, angle_lora_strength)]. Roles: single/ots/close (+ alternates)."""
    p = sc.get("person") or "person"
    if sc["partner"] == "clone":
        wear, place, _ = _look(sc)
        keep = (f"Keep exactly the same {p} with the same face, same face shape, same eyes, same nose, same facial hair, "
                f"same hairstyle, same head pose. Remove any headphones. Dress the {p} in {wear}. Change the lighting "
                f"and background to {place}. ")
        base = [("single", keep + "Same camera angle and framing. Photorealistic film still.", 0.0),
                ("ots", keep + "Same camera angle." + _ots(sc) + " Photorealistic film still.", 0.0),
                ("close", keep + "Move the camera closer to a tight close-up of the face. Photorealistic film still.", 0.0)]
    else:
        keep = (f"Keep exactly the same {p}: same face, same hair, same clothes, same room and lighting. ")
        base = [("single", "Rotate the camera 45 degrees to the left.", 1.0),
                ("ots", keep + f"The {p} looks toward the right side of the frame, listening." + _ots(sc), 0.0),
                ("close", keep + f"Move the camera closer to a close-up of the face, the {p} looks toward the right "
                 "side of the frame. Photorealistic.", 0.0)]
    alts = [(r + "_2", t, l) for r, t, l in base] + [(base[0][0] + "_3", base[0][1], base[0][2]),
                                                      (base[1][0] + "_3", base[1][1], base[1][2])]
    return base + alts


def character_prompt(sc) -> str:
    desc = sc.get("character") or "a fictional woman in her early 30s with shoulder-length dark curly hair, wearing a mustard yellow sweater"
    return (f"Replace the person with a completely different, fictional person: {desc}, sitting in a cozy podcast studio "
            "with acoustic foam panels, warm lighting and a podcast microphone on a boom arm near them. Medium close-up, "
            "photorealistic.")


def b_visual(sc, shot: str, listening: bool = False) -> str:
    p = sc.get("person") or "person"
    if sc["partner"] == "clone":
        wear, _, place = _look(sc)
        who = f"The same {p} wearing {wear}, {place}"
    else:
        who = f"The same {p}, same clothes, same room and lighting"
    frame = {"single": "", "ots": ", seen over the out-of-focus shoulder of the other speaker in the foreground",
             "close": ", tight close-up"}.get(shot.split("_")[0], "")
    act = ("listens silently with the mouth closed, small nod, thoughtful skeptical expression, blinking naturally"
           if listening else "talking with natural head movement, expressive face and accurate lip sync")
    return (f"{who}{frame}, looking at someone off-screen to the right, {act}, static camera, shallow depth of field, "
            "photorealistic film.")


def b_sounds(sc) -> str:
    if sc.get("voice") == "self":
        return "The same male voice, close to the microphone, quiet room, no music."
    return f"{VOICES.get(sc.get('voice'), 'natural').split(',')[0].capitalize()} voice, close microphone, quiet room, no music."


def line_prompt(sc, shot: str, text: str) -> str:
    return f"[VISUAL]: {b_visual(sc, shot)}\n[SPEECH]: {text}\n[SOUNDS]: {b_sounds(sc)}\n"


def react_prompt(sc, shot: str) -> str:
    return (f"[VISUAL]: {b_visual(sc, shot, listening=True)}\n[SPEECH]: Hmm.\n"
            "[SOUNDS]: Quiet room tone, no speech, no music.\n")


# ------------------------------------------------------------------ timing / turns
def turns(sc: dict, tr: dict) -> list[dict]:
    """Alternate A (groups of real sentences) and B (lines). Times are source-relative for A."""
    sents = tr["sentences"]
    after = {}
    for k, ln in enumerate(sc["lines"]):
        after.setdefault(ln["after"], []).append(k)
    out, cur = [], []
    for s in sents:
        cur.append(s["i"])
        if s["i"] in after:
            out.append({"who": "A", "sents": cur}); cur = []
            for k in after[s["i"]]:
                out.append({"who": "B", "line": k})
    if cur:
        out.append({"who": "A", "sents": cur})
    for t in out:
        if t["who"] == "A":
            a, b = sents[t["sents"][0]], sents[t["sents"][-1]]
            prev_end = sents[a["i"] - 1]["end"] if a["i"] > 0 else 0.0
            nxt = sents[b["i"] + 1]["start"] if b["i"] + 1 < len(sents) else tr["duration"]
            t["start"] = round(max(prev_end + 0.5 * (a["start"] - prev_end), a["start"] - 0.25, 0.0), 3)
            t["end"] = round(min(b["end"] + 0.28, nxt - 0.08 if nxt > b["end"] else b["end"] + 0.28, tr["duration"]), 3)
    return out


def b_shot_for(k: int) -> str:
    return B_SHOTS[k % len(B_SHOTS)]


def line_seconds(text: str) -> float:
    words = len(re.findall(r"[\w']+", text))
    return float(min(5, max(3, math.ceil(words / 2.6 + 1.3))))


# ------------------------------------------------------------------ A frames / refs
def _frame(video, t, out, hflip=False):
    vf = ["hflip"] if hflip else []
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
         *(["-vf", ",".join(vf)] if vf else []), str(out)])
    return Path(out)


def _a_side_clip(sc) -> dict | None:
    side = [c for c in sc["a_clips"].values() if c.get("preset") in ("right90", "right45", "lowangle")]
    return side[0] if side else None


def voice_ref(sc, work: Path) -> Path:
    """Reference audio for the ID-LoRA voice: a slice of your real take (clone) or an open TTS sample."""
    work = Path(work)
    if sc.get("voice") == "self":
        tr = load_json(sc["transcript"])
        s = max(tr["sentences"], key=lambda x: x["end"] - x["start"])
        t0 = max(0.0, min(s["start"] - 0.2, tr["duration"] - 5.0))
        out = work / "refs" / "voice_self.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t0:.3f}", "-t", "5", "-i", sc["source"], "-vn", "-ac", "1",
             "-ar", "44100", str(out)])
        return out
    v = sc.get("voice") or "af_heart"
    if Path(v).is_file():
        return Path(v)
    p = VOICE_DIR / f"{v}.wav"
    if not p.exists():
        p = VOICE_DIR / f"{v}.flac"
    if not p.exists():
        raise SystemExit(f"voice sample {v} not found in {VOICE_DIR} (run `multicam scene voices`)")
    return p


def render_voices(out_dir: Path = VOICE_DIR, text: str = VOICE_TEXT, names=None, model_dir: str | None = None) -> list:
    """Render Kokoro-82M (Apache-2.0) reference voices on CPU: pip install kokoro-onnx soundfile, and download
    kokoro-v1.0.int8.onnx + voices-v1.0.bin (github.com/thewh1teagle/kokoro-onnx releases) into model_dir."""
    import soundfile as sf
    from kokoro_onnx import Kokoro
    md = Path(model_dir or ".")
    k = Kokoro(str(md / "kokoro-v1.0.int8.onnx"), str(md / "voices-v1.0.bin"))
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for n in names or VOICES:
        s, sr = k.create(text, voice=n, speed=1.0, lang="en-gb" if n.startswith("b") else "en-us")
        p = out_dir / f"{n}.flac"
        sf.write(str(p), s, sr)
        done.append(str(p))
    return done


# ------------------------------------------------------------------ paid steps
def estimate(sc: dict, what: str = "all") -> dict:
    n_lines = len(sc["lines"])
    jobs = {"stills": 0, "line": 0, "react": 0}
    if what in ("all", "stills") and not sc["stills"]:
        jobs["stills"] = 2 if sc["partner"] == "character" and not sc.get("photo") else 1
    if what in ("all", "clips"):
        jobs["line"] = sum(1 for k in range(n_lines) if str(k) not in sc["b_clips"])
        jobs["react"] = 0 if "react" in sc["b_clips"] else 1
    usd = sum(JOB_COST[k] * v for k, v in jobs.items())
    return {"jobs": jobs, "n_jobs": sum(jobs.values()), "usd": round(usd, 3)}


def make_stills(sc: dict, work: Path, key=None, seed=None, log=print) -> dict:
    """1 Qwen-Image-Edit job (2 for a fictional character): 8 partner stills, defaults picked for single/ots/close."""
    work = Path(work)
    wf, b, raw = _wf("scene_edit")
    sd = work / "stills"
    sd.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    if sc["partner"] == "clone":
        side = _a_side_clip(sc)
        # mirror A's side angle so the clone faces screen-right (180-degree rule); without one, mirror the real take
        src = _frame(side["path"], 1.4, sd / "src_a_side_flip.png", hflip=True) if side else \
            _frame(sc["source"], load_json(sc["transcript"])["sentences"][0]["end"] + 0.2, sd / "src_a_flip.png", hflip=True)
    elif sc["partner"] == "photo":
        src = Path(sc["photo"])
    else:
        src = Path(sc["photo"]) if sc.get("photo") else None
        if src is None:
            tr = load_json(sc["transcript"])
            a = _frame(sc["source"], tr["sentences"][0]["end"] + 0.2, sd / "src_a.png")
            vals = {"image": str(a), **{f"p{i}": character_prompt(sc) for i in range(1, 9)},
                    **{f"s{i}": rng.randint(0, 2**48) for i in range(1, 9)}, "angle_lora": 0.0}
            log("stills: creating the fictional character (1 Qwen job)")
            rec = run_graph(wf, b, vals, sd / "character", files=("image",), key=key, rename=raw["_outputs"])
            _job(sc, "character", rec)
            if rec["status"] != "success":
                raise RuntimeError(f"character job failed: {rec.get('error')}")
            src = sd / "character" / "e1.png"
            sc["photo"] = str(src)
    roles = still_prompts(sc)
    vals = {"image": str(src)}
    for i, (role, prompt, lora) in enumerate(roles, 1):
        vals[f"p{i}"], vals[f"l{i}"], vals[f"s{i}"] = prompt, lora, rng.randint(0, 2**48)
    log(f"stills: 8 partner framings from {Path(src).name} (1 Qwen job)")
    rec = run_graph(wf, b, vals, sd, files=("image",), key=key, rename=raw["_outputs"])
    _job(sc, "stills", rec)
    if rec["status"] != "success":
        raise RuntimeError(f"stills job failed: {rec.get('error')}")
    sc["stills"] = {role: str(sd / f"e{i}.png") for i, (role, _, _) in enumerate(roles, 1)}
    save(work, sc)
    return sc["stills"]


def _job(sc, kind, rec):
    sc["jobs"].append({"kind": kind, "status": rec.get("status"), "prompt_id": rec.get("prompt_id"),
                       "elapsed_s": rec.get("elapsed_s"), "error": rec.get("error"),
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})


def make_clips(sc: dict, work: Path, key=None, only: list[str] | None = None, seed=None, parallel: int = 3,
               log=print, on_status: Callable[[str, dict], None] | None = None) -> dict:
    """LTX-2.3 ID LoRA: one clip per B line (exact words, B voice) + one B listening clip for L-cuts."""
    work = Path(work)
    if not sc["stills"]:
        raise SystemExit("no partner stills yet: run `multicam scene stills` first")
    wf, b, _ = _wf("idlora")
    ref = voice_ref(sc, work)
    cd = work / "clips"
    cd.mkdir(parents=True, exist_ok=True)
    tasks = []
    for k, ln in enumerate(sc["lines"]):
        if (only and str(k) not in only) or (not only and str(k) in sc["b_clips"]):
            continue
        shot = b_shot_for(k)
        tasks.append((str(k), shot, line_prompt(sc, shot, ln["text"]), line_seconds(ln["text"]), ln["text"]))
    if (only and "react" in only) or (not only and "react" not in sc["b_clips"]):
        tasks.append(("react", "ots", react_prompt(sc, "ots"), 5.0, ""))

    def go(t):
        name, shot, prompt, secs, text = t
        still = sc["stills"].get(shot) or sc["stills"]["single"]
        s = seed if seed is not None else random.randint(0, 2**48)
        vals = {"image": still, "audio": str(ref), "prompt": prompt, "width": 720, "height": 1280, "fps": 24,
                "duration": secs, "seed": s}
        sub = cd / f"_{name}"
        rec = run_graph(wf, b, vals, sub, files=("image", "audio"), key=key,
                        on_status=(lambda r: on_status(name, r)) if on_status else None)
        if rec["status"] == "success" and rec["outputs"]:
            dst = cd / f"b_{name}.mp4"
            Path(rec["outputs"][0]).replace(dst)
            rec["clip"] = {"path": str(dst), "shot": shot, "text": text, "seed": s, "seconds": secs,
                           "still": Path(still).name}
        return name, rec

    log(f"clips: {len(tasks)} LTX-2.3 ID LoRA jobs ({parallel} in parallel)")
    with ThreadPoolExecutor(max(1, parallel)) as ex:
        for name, rec in ex.map(go, tasks):
            _job(sc, f"clip:{name}", rec)
            if rec.get("clip"):
                sc["b_clips"][name] = rec["clip"]
                sc["b_clips"][name].pop("words", None)
            log(f"  {name}: {rec['status']} {rec.get('error') or ''}")
    save(work, sc)
    return sc["b_clips"]


def make_a_clips(sc: dict, work: Path, key=None, log=print) -> dict:
    """CrossView re-shoots of the real take for A's own angles (side = right90, matched eyeline; wide)."""
    from .reshoot import run_reshoot_job
    work = Path(work)
    tr = load_json(sc["transcript"])
    wf, b, _ = _wf("crossview")
    T = turns(sc, tr)
    wants = []
    for i, t in enumerate(T):
        if t["who"] != "A":
            continue
        preset = "right90" if i > 0 else "wide"
        st = max(0.0, t["start"] - 0.3)
        if not any(c["src_start"] <= t["start"] and c["src_start"] + 4.9 >= min(t["end"], t["start"] + 2)
                   and c.get("preset") == preset for c in sc["a_clips"].values()):
            wants.append((f"a{i}", st, preset))
    for name, st, preset in wants:
        log(f"A re-shoot {name} @ {st:.2f}s ({preset})")
        out = work / "a_clips" / f"{name}_{preset}.mp4"
        rec = run_reshoot_job(sc["source"], st, preset, 0, out, wf, b, key=key, work_dir=work / "a_clips")
        _job(sc, f"reshoot:{name}", rec)
        if rec["status"] == "success":
            sc["a_clips"][name] = {"path": str(out), "src_start": st, "preset": preset, "synced": True}
    save(work, sc)
    return sc["a_clips"]


def import_a_clips(sc: dict, work: Path, clips_json: str) -> dict:
    """Reuse time-locked re-shoot clips from a talk-mode run (clips.json with src_start/preset/synced)."""
    cj = Path(clips_json).resolve()
    for name, c in load_json(cj).items():
        if c.get("synced"):
            sc["a_clips"][name] = {"path": str((cj.parent / c["path"]).resolve()), "src_start": float(c["src_start"]),
                                   "preset": c.get("preset", "right90"), "synced": True}
    save(Path(work), sc)
    return sc["a_clips"]


# ------------------------------------------------------------------ edit
def _clip_words(path: str, cache: Path) -> list[dict]:
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / (Path(path).stem + ".words.json")
    if p.exists() and p.stat().st_mtime >= Path(path).stat().st_mtime:
        return load_json(p)
    from .transcribe import whisper_words
    w = whisper_words(path)
    save_json(w, p)
    return w


def _norm(x: str) -> str:
    return re.sub(r"[^a-z0-9']", "", x.lower())


def _match_line(words: list[dict], text: str) -> list[dict]:
    """Keep the clip words that belong to the line: from the first to the last word matching the text in order
    (ID LoRA sometimes pads a short line with an extra syllable at the end)."""
    exp = [_norm(t) for t in re.findall(r"[\w']+", text)]
    if not exp or not words:
        return words
    j, hit = 0, []
    for k, w in enumerate(words):
        n = _norm(w["text"])
        for jj in range(j, min(j + 3, len(exp))):
            if n and (n == exp[jj] or n.startswith(exp[jj]) or exp[jj].startswith(n)):
                hit.append(k); j = jj + 1
                break
    if len(hit) < max(1, len(exp) // 2):
        return words
    return words[hit[0]: hit[-1] + 1]


def _cut_near(words: list[dict], t: float, lo: float, hi: float) -> float:
    """Nearest word-gap time to t inside [lo, hi] (cuts land between words)."""
    gaps = [(a["end"] + b["start"]) / 2 for a, b in zip(words, words[1:])]
    gaps = [g for g in gaps if lo <= g <= hi]
    return min(gaps, key=lambda g: abs(g - t)) if gaps else t


def plan_edit(sc: dict, work: Path, react: bool = True) -> list[dict]:
    """-> output timeline of shots: {who, cam, src, path, in, dur, audio:(path,in,dur)|None, label}."""
    work = Path(work)
    tr = load_json(sc["transcript"])
    words = tr["words"]
    T = turns(sc, tr)
    side = sorted([c for c in sc["a_clips"].values() if c.get("preset") in ("right90", "right45", "lowangle")],
                  key=lambda c: c["src_start"])
    wide = sorted([c for c in sc["a_clips"].values() if c.get("preset") in ("wide", "aerial", "closeup", "left45", "left90")],
                  key=lambda c: c["src_start"])
    rc = sc["b_clips"].get("react")
    react_used: list[tuple[float, float]] = []
    edl = []

    def covering(cl, t0, t1):
        return next((c for c in cl if c["src_start"] + 0.05 <= t0 and t1 <= c["src_start"] + 5.0), None)

    for i, t in enumerate(T):
        if t["who"] == "B":
            c = sc["b_clips"].get(str(t["line"]))
            if not c:
                raise SystemExit(f"line {t['line']} has no clip yet (run `multicam scene clips`)")
            w = _clip_words(c["path"], work / "cache")
            dur_clip = probe(c["path"])["duration"]
            w = _match_line(w, c.get("text") or "")
            if w:
                c0 = max(0.0, w[0]["start"] - 0.12)
                c1 = min(dur_clip - 0.05, w[-1]["end"] + 0.3)
            else:
                c0, c1 = 0.2, dur_clip - 0.1
            edl.append({"who": "B", "cam": f"B {c['shot']}", "src": "ai", "path": c["path"], "in": round(c0, 3),
                        "dur": round(c1 - c0, 3), "audio": (c["path"], round(c0, 3), round(c1 - c0, 3)),
                        "words": c.get("text")})
            continue
        t0, t1 = t["start"], t["end"]
        aw = [w for w in words if t0 <= w["start"] < t1]
        audio = (sc["source"], t0, round(t1 - t0, 3))
        segs = []  # (cam, src, clip, start, end) in source time
        cur, used = t0, None
        if i > 0 and T[i - 1]["who"] == "B":  # open on A's side angle: matched eyeline after B's shot
            cut = _cut_near(aw, min(t1 - 0.8, t0 + 1.6), t0 + 0.9, t1 - 0.6)
            for cam, pool in (("A side", side), ("A wide", wide)):
                c = covering(pool, t0, cut)
                if c and cut - t0 >= 0.8:
                    segs.append((cam, "ai", c, t0, cut)); cur, used = cut, cam
                    break
        # long stretches: real A, then an insert (B listening = L-cut with A's audio running, A side, A wide)
        pool = [("B listens", "react", rc)] if (react and rc) else []
        pool += [("A side", "ai", None), ("A wide", "ai", None)]
        while t1 - cur > 3.2 and pool:
            a = _cut_near(aw, cur + 1.5, cur + 1.1, t1 - 1.8)
            bb = _cut_near(aw, a + 1.4, a + 0.9, t1 - 0.9)
            if bb - a < 0.8:
                break
            pick = None
            for n, (cam, src, c) in enumerate(pool):
                if cam == used:
                    continue
                c = c or covering(side if cam == "A side" else wide, a, bb)
                if c:
                    pick = (cam, src, c); pool.pop(n)
                    break
            if not pick:
                break
            segs.append(("A real", "real", None, cur, a)); segs.append((*pick, a, bb)); cur, used = bb, pick[0]
        segs.append(("A real", "real", None, cur, t1))
        first = True
        for cam, src, c, s0, s1 in segs:
            if s1 - s0 < 0.05:
                continue
            if src == "real":
                path, cin = sc["source"], s0
            elif src == "ai":
                path, cin = c["path"], s0 - c["src_start"]
            else:  # reaction clip: take a stretch away from its "Hmm"
                rw = _clip_words(c["path"], work / "cache")
                rdur = probe(c["path"])["duration"]
                d = s1 - s0
                cands = [round(0.3 + 0.1 * q, 2) for q in range(int(max(0.0, rdur - d - 0.35) / 0.1) + 1)]
                free = [q for q in cands if not any(q - 0.1 < w["end"] and w["start"] < q + d for w in rw)] or cands or [0.0]
                fresh = [q for q in free if not any(q < u1 and u0 < q + d for u0, u1 in react_used)]
                cin = (fresh or free)[0]
                react_used.append((cin, cin + d))
                path = c["path"]
            edl.append({"who": "A", "cam": cam, "src": src, "path": path, "in": round(max(0.0, cin), 3),
                        "dur": round(s1 - s0, 3), "audio": audio if first else None,
                        "words": " ".join(w["text"] for w in aw if s0 <= w["start"] < s1)})
            first = False
    return edl


def _gain_db(path: str, t0: float, dur: float) -> float:
    out = run(["ffmpeg", "-v", "info", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", path, "-vn",
               "-af", "volumedetect", "-f", "null", "-"]).stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    return float(m.group(1)) if m else -20.0


def render(sc: dict, work: Path, out: str | Path | None = None, labels: bool = False, react: bool = True,
           crf: int = 19, preset: str = "medium") -> dict:
    """Shot / reverse-shot edit -> scene.mp4 (720x1280, 30 fps, yuv420p, +faststart). A = real audio, B = clip audio."""
    need("ffmpeg")
    work = Path(work)
    out = Path(out or work / "scene.mp4")
    edl = plan_edit(sc, work, react=react)
    W, H, F = OUT_W, OUT_H, OUT_FPS
    # frame-exact durations from cumulative rounding
    t, frames = 0.0, []
    for s in edl:
        f0, t = round(t * F), t + s["dur"]
        frames.append(round(t * F) - f0)
    from .assemble import FONT_CANDIDATES
    font = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
    inputs, idx = [], {}
    for s in edl:
        for p in (s["path"], *( [s["audio"][0]] if s["audio"] else [])):
            if p not in idx:
                idx[p] = len(inputs); inputs.append(p)
    a_level = _gain_db(sc["source"], 0, probe(sc["source"])["duration"])
    f, vl, al = [], [], []
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    for i, (s, n) in enumerate(zip(edl, frames)):
        k = idx[s["path"]]
        chain = (f"[{k}:v]trim=start={s['in']:.4f}:end={s['in'] + (n + 3) / F:.4f},setpts=PTS-STARTPTS,fps={F},{cover},"
                 f"setsar=1,format=yuv420p,tpad=stop_mode=clone:stop={n},trim=end_frame={n},setpts=PTS-STARTPTS")
        if labels and font:
            tag = {"A real": "A  real take", "A side": "A  side (AI)", "A wide": "A  wide (AI)",
                   "B listens": "B  listening (AI)", "B single": "B  single (AI)", "B ots": "B  over-the-shoulder (AI)",
                   "B close": "B  close-up (AI)"}.get(s["cam"], s["cam"])
            fs = 34
            chain += (f",drawtext=fontfile='{font}':text='{tag}':x={fs}:y={int(fs * 1.7)}:fontsize={fs}:"
                      f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw={fs // 3}")
        f.append(chain + f"[v{i}]"); vl.append(f"[v{i}]")
    # audio: one piece per turn (A turn audio spans its shots)
    j = 0
    for i, (s, n) in enumerate(zip(edl, frames)):
        if not s["audio"]:
            continue
        nf = n
        for s2, n2 in zip(edl[i + 1:], frames[i + 1:]):
            if s2["audio"]:
                break
            nf += n2
        p, a0, _ = s["audio"]
        dur = nf / F
        g = 0.0 if s["who"] == "A" else max(-12.0, min(12.0, a_level - _gain_db(p, a0, dur)))
        f.append(f"[{idx[p]}:a]atrim=start={a0:.4f}:end={a0 + dur + 0.2:.4f},asetpts=PTS-STARTPTS,"
                 f"aresample=48000,aformat=channel_layouts=stereo,volume={g:.2f}dB,apad,atrim=end={dur:.4f},"
                 f"afade=t=in:d=0.012,afade=t=out:st={max(0.0, dur - 0.02):.4f}:d=0.02[a{j}]")
        al.append(f"[a{j}]"); j += 1
    f.append("".join(vl) + f"concat=n={len(vl)}:v=1:a=0[vout]")
    f.append("".join(al) + f"concat=n={len(al)}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]")
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in inputs:
        cmd += ["-i", p]
    script = work / "render_filter.txt"
    script.write_text(";\n".join(f))
    cmd += ["-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264",
            "-preset", preset, "-crf", str(crf), "-r", str(F), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(out)]
    run(cmd)
    t = 0.0
    for s, n in zip(edl, frames):
        s["t"] = round(t, 3); t += n / F
    sc["edl"] = edl
    sc["output"] = str(out)
    save(work, sc)
    return {"output": str(out), "duration": round(sum(frames) / F, 3), "shots": edl}
