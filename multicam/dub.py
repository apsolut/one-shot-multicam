"""Dub mode: one take in -> the same take in another language, with your face and your voice.

Open weights only, all on Comfy Cloud:
1. translate: Gemma 3 12B (the LTX-2.3 text encoder) through ComfyUI's core TextGenerate node;
   the result is shown for review/editing (a 12B model still makes the odd mistake).
2. dub CAM A: LTX-2.3 + Lightricks' LipDub (Dub-It) IC-LoRA re-renders each sentence window of the real
   take with new lip motion AND new speech generated from the translated text; the real audio is only the
   voice reference, so it speaks in your voice. Head moves, framing and identity come from the real frames.
3. the dubbed take then becomes the source of a normal multicam edit (CrossView re-shoots for B/C, cuts
   on the dubbed words).
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Callable

from .reshoot import GEN, display_size, gen_size, make_ref
from .util import need, probe, run

LANGS = {  # code -> (name used in prompts, LipDub validated by Lightricks)
    "de": ("German", True), "es": ("Spanish", True), "fr": ("French", True), "ru": ("Russian", True),
    "en": ("English", True), "hr": ("Croatian", False), "it": ("Italian", False), "pt": ("Portuguese", False),
}
TRANSLATOR = "gemma_3_12B_it.safetensors"  # bf16: the fp4 build made spelling mistakes in tests
MAX_WINDOW_S = 10.0   # longest LipDub window (cost and memory grow with length)
EXPANSION_WARN = 1.3  # translation/original syllable ratio above which the line may not fit
LIPDUB_USD_PER_S = 0.011  # measured: an 8-10 s LipDub window ~ $0.09-0.11 (two-stage 704x1280)
TRANSLATE_USD = 0.01


def window_frames(dur: float, fps: int = GEN["fps"]) -> int:
    """Smallest 8k+1 frame count covering `dur` seconds (LTX latent constraint)."""
    n = int(dur * fps + 0.999)
    return ((max(n, 9) - 1 + 7) // 8) * 8 + 1


def plan_windows(transcript: dict, duration: float, max_s: float = MAX_WINDOW_S) -> list[dict]:
    """Group whole sentences into windows that start at a sentence start and end where the next window
    starts (so a slightly longer translation can use the pause), each <= max_s where possible."""
    sents = transcript.get("sentences") or []
    if not sents:
        return []
    groups: list[list[dict]] = [[sents[0]]]
    for s in sents[1:]:
        g = groups[-1]
        if s["end"] - g[0]["start"] <= max_s:
            g.append(s)
        else:
            groups.append([s])
    wins = []
    for i, g in enumerate(groups):
        start = 0.0 if i == 0 else round(g[0]["start"] - 0.25, 3)  # a little lead-in before the first word
        end = duration if i == len(groups) - 1 else round(groups[i + 1][0]["start"] - 0.25, 3)
        wins.append({"id": f"w{i + 1:02d}", "start": max(0.0, start), "end": end,
                     "speech_end": g[-1]["end"], "text": " ".join(s["text"] for s in g),
                     "sentences": [s["i"] for s in g]})
    return wins


def syllables(text: str) -> int:
    """Rough vowel-group syllable count, good enough to flag translations that won't fit."""
    return max(1, len(re.findall(r"[aeiouyäöüáéíóúàèìòùâêîôûœæøåıąęěůаеёиоуыэюя]+", text.lower())))


def fit_report(original: str, translated: str) -> dict:
    r = syllables(translated) / syllables(original)
    return {"ratio": round(r, 2), "ok": r <= EXPANSION_WARN,
            "hint": None if r <= EXPANSION_WARN else "longer than the original: shorten it or the end may be cut"}


def translate_workflow(lines: list[str], lang: str) -> dict:
    name = LANGS.get(lang, (lang, False))[0]
    prompt = (f"Translate each numbered English line into natural spoken {name} for a dubbed video. "
              "Use short, natural, correct sentences of similar length (it must fit the same time); "
              "keep product and brand names unchanged. Reply with only the numbered translations, one per line.\n\n"
              + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(lines)))
    return {
        "1": {"class_type": "LTXAVTextEncoderLoader", "inputs": {"text_encoder": TRANSLATOR,
              "ckpt_name": "ltx-2.3-22b-dev-fp8.safetensors", "device": "default"}},
        "2": {"class_type": "TextGenerate", "inputs": {"clip": ["1", 0], "prompt": prompt, "max_length": 600,
              "sampling_mode": "off", "thinking": False, "use_default_template": True}},
        "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}},
    }


def parse_numbered(text: str, n: int) -> list[str]:
    out = {}
    for ln in text.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", ln)
        if m and 1 <= int(m.group(1)) <= n:
            out[int(m.group(1))] = m.group(2).strip()
    return [out.get(i + 1, "") for i in range(n)]


def translate(lines: list[str], lang: str, key=None) -> list[str]:
    """Translate lines with Gemma 3 12B on Comfy Cloud (open weights). Cheap: one short text job."""
    from .comfy import ComfyError, job_detail, submit, wait
    pid = submit(translate_workflow(lines, lang), key)
    st = wait(pid, key, poll=2.0)
    det = job_detail(pid, key)
    if st != "success":
        raise ComfyError(f"translation failed: {json.dumps(det.get('execution_error'))[:300]}")
    texts = [t for o in (det.get("outputs") or {}).values() for t in (o.get("text") or [])]
    if not texts:
        raise ComfyError("translation returned no text")
    out = parse_numbered(texts[0], len(lines))
    return [o or src for o, src in zip(out, lines)]  # keep the source line if a number is missing


def dub_prompt(text: str, lang: str, speaker: str = "A man") -> str:
    name = LANGS.get(lang, (lang, False))[0]
    text = text.replace('"', "'")
    return f'{speaker} is speaking in {name} to the camera, saying: "{text}"'


def run_dub_job(video: str | Path, win: dict, text: str, lang: str, out_path: Path, workflow: dict,
                bindings: dict, key=None, seed=None, speaker: str = "A man", work_dir: Path | None = None,
                on_status: Callable[[dict], None] | None = None) -> dict:
    """Dub one window of the real take with LipDub. Record has no key in it."""
    from .comfy import download, find_videos, job_detail, patch_workflow, submit, upload, wait
    out_path = Path(out_path)
    w, h = gen_size(*display_size(video))
    frames = window_frames(win["end"] - win["start"])
    ref = make_ref(video, win["start"], (work_dir or out_path.parent) / f"{out_path.stem}_ref.mp4", w, h,
                   frames=frames)
    seed = seed if seed is not None else random.randint(0, 2**48)
    prompt = dub_prompt(text, lang, speaker)
    rec = {"engine": "lipdub", "lang": lang, "window": win["id"], "frames": frames, "seed": seed,
           "prompt": prompt, "status": "uploading", "started": time.time()}
    emit = (lambda: on_status(dict(rec))) if on_status else (lambda: None)
    emit()
    values = {"video": upload(str(ref), key), "prompt": prompt, "seed": seed, "lat_w": w // 2, "lat_h": h // 2,
              "frames": frames}
    rec["prompt_id"] = submit(patch_workflow(workflow, bindings, values), key)
    rec["status"] = "submitted"; emit()

    def st_cb(st):
        rec["status"] = st; emit()
    st = wait(rec["prompt_id"], key, on_status=st_cb)
    rec["elapsed_s"] = round(time.time() - rec["started"], 1)
    if st == "success":
        vids = find_videos(job_detail(rec["prompt_id"], key).get("outputs"))
        if vids:
            rec["output"] = str(download(vids[0], out_path, key))
        else:
            rec["status"], rec["error"] = "error", "no video in outputs"
    else:
        rec["error"] = json.dumps(job_detail(rec["prompt_id"], key).get("execution_error"))[:1500]
    emit()
    return rec


def stitch(video: str | Path, wins: list[dict], clips: dict[str, str], out: str | Path, xfade_s: float = 0.25) -> Path:
    """Dubbed CAM A: each window's LipDub clip trimmed to the window and joined with its generated speech.
    Windows without a clip fall back to the real take (video and audio).

    Seams: LipDub renders 8k+1 frames, so a window's clip runs a little past the window end (the real
    motion continues there). The video crossfades from that tail into the next window over `xfade_s`
    (the next window starts exactly on time, so timing and lip sync are unchanged); the audio cuts hard at
    the boundary, which sits in a sentence pause."""
    need("ffmpeg")
    info = probe(video)
    fps = round(info.get("fps", 30.0), 3)
    w, h = info["width"], info["height"]
    total = info["duration"]
    parts, filt, n = [], [], len(wins)
    vfix = f"fps={fps:g},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p"
    tails = []
    for i, win in enumerate(wins):
        d = win["end"] - win["start"]
        c = clips.get(win["id"])
        if i < n - 1 and xfade_s > 0:
            avail = (probe(c)["duration"] - d) if c else (total - win["end"])
            tail = max(0.0, min(xfade_s, avail - 1.0 / fps))
        else:
            tail = 0.0
        tails.append(tail)
        k = sum(1 for p in parts if p == "-i")
        if c:
            parts += ["-i", str(c)]
            filt.append(f"[{k}:v]trim=duration={d + tail:.4f},setpts=PTS-STARTPTS,{vfix}[v{i}];"
                        f"[{k}:a]atrim=duration={d:.4f},asetpts=PTS-STARTPTS,aresample=48000,apad=whole_dur={d:.4f}[a{i}]")
        else:
            parts += ["-ss", f"{win['start']:.4f}", "-t", f"{d + tail:.4f}", "-i", str(video)]
            filt.append(f"[{k}:v]setpts=PTS-STARTPTS,{vfix}[v{i}];"
                        f"[{k}:a]atrim=duration={d:.4f},asetpts=PTS-STARTPTS,aresample=48000,apad=whole_dur={d:.4f}[a{i}]")
    # video: chain crossfades (a window with no usable tail joins with a 1-frame "fade" = hard cut)
    cur, t = "v0", 0.0
    for i in range(1, n):
        t += wins[i - 1]["end"] - wins[i - 1]["start"]
        dur = tails[i - 1] if tails[i - 1] >= 1.5 / fps else 1.0 / fps
        if tails[i - 1] < 1.5 / fps:  # pad the previous segment by one frame so the cut lands on time
            filt.append(f"[{cur}]tpad=stop_mode=clone:stop_duration={1.0 / fps:.4f}[{cur}p]")
            cur = f"{cur}p"
        filt.append(f"[{cur}][v{i}]xfade=transition=fade:duration={dur:.4f}:offset={t:.4f}[x{i}]")
        cur = f"x{i}"
    filt.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]")
    run(["ffmpeg", "-y", "-v", "error", *parts, "-filter_complex", ";".join(filt), "-map", f"[{cur}]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", f"{fps:g}",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    return Path(out)


def estimate(wins: list[dict], n_inserts: int, reshoot_usd: float = 0.055) -> dict:
    """Cost of a full Dub run: one LipDub job per window + one CrossView re-shoot per insert.
    `jobs` counts LipDub windows as ceil(seconds/5) re-shoot-sized jobs for the hosted job caps."""
    import math
    secs = sum(w["end"] - w["start"] for w in wins)
    lip = round(secs * LIPDUB_USD_PER_S, 3)
    return {"windows": len(wins), "inserts": n_inserts, "lipdub_usd": lip,
            "reshoot_usd": round(n_inserts * reshoot_usd, 3), "usd": round(lip + n_inserts * reshoot_usd, 2),
            "jobs": sum(math.ceil((w["end"] - w["start"]) / 5.0) for w in wins) + n_inserts}


def make_dubbed_take(video: str | Path, work: Path, dp: dict, key=None, seed=None, speaker: str = "A man",
                     log: Callable[[str], None] = print) -> Path:
    """LipDub every window that has no clip yet, then stitch the dubbed CAM A -> <work>/dub_<lang>.mp4.
    `dp` is the dub_<lang>.json dict (lang + windows with translations); clips are recorded in it."""
    from .comfy import load_bindings
    from .server import workflows_dir
    wfd = workflows_dir()
    wf = json.loads((wfd / "dub_api.json").read_text())
    b = load_bindings(wfd / "dub_bindings.json")
    lang = dp["lang"]
    out = work / f"dub_{lang}"
    out.mkdir(parents=True, exist_ok=True)
    clips = dp.setdefault("clips", {})
    for w in dp["windows"]:
        if w["id"] in clips and (work / clips[w["id"]]).exists():
            continue
        log(f"{w['id']}: dubbing {w['start']:.1f}-{w['end']:.1f}s into {LANGS[lang][0]} (LipDub)")
        rec = run_dub_job(video, w, w["translation"], lang, out / f"{w['id']}.mp4", wf, b, key=key, seed=seed,
                          speaker=speaker)
        if not rec.get("output"):
            raise RuntimeError(f"{w['id']}: {rec['status']} {(rec.get('error') or '')[:300]}")
        clips[w["id"]] = str(Path(rec["output"]).relative_to(work))
        log(f"{w['id']}: done in {rec.get('elapsed_s')}s")
    final = work / f"dub_{lang}.mp4"
    stitch(video, dp["windows"], {k: str(work / v) for k, v in clips.items()}, final)
    return final


def multicam_from_dub(dubbed: Path, mc: Path, lang: str, script: str, key=None, seed=None,
                      log: Callable[[str], None] = print, preset: str = "medium", model: str = "small",
                      seams: list[float] | None = None, color_match: float = 0.6) -> dict:
    """The dubbed take becomes a normal multicam source: transcribe in the target language (aligned to the
    reviewed script), plan cuts, CrossView re-shoots for B/C, assemble -> <mc>/multicam(_labeled).mp4.
    `seams` (window starts after the first) get a camera cut over them (see hide_seams); inserts are
    colour-matched to CAM A."""
    from .assemble import assemble
    from .comfy import load_bindings
    from .plan import PlanParams, plan
    from .reshoot import DEFAULT_CAMS, angle_prompt, insert_ordinals, run_reshoot_job
    from .server import workflows_dir
    from .transcribe import analyze
    from .util import load_json, save_json
    mc.mkdir(parents=True, exist_ok=True)
    log(f"transcribing the dubbed take ({LANGS[lang][0]}, free)")
    tr = analyze(dubbed, mc / "transcript.json", model=model, script=script, language=lang)
    pl = plan(tr, mc / "plan.json", PlanParams())
    gen = mc / "gen" / "reshoot"
    gen.mkdir(parents=True, exist_ok=True)
    clips_path = gen / "clips.json"
    clips = load_json(clips_path) if clips_path.exists() else {}
    wfd = workflows_dir()
    wf = json.loads((wfd / "crossview_api.json").read_text())
    b = load_bindings(wfd / "crossview_bindings.json")
    chunks = {c["id"]: c for c in pl["chunks"]}
    for (cid, cam), variant in insert_ordinals(pl).items():
        if cid in clips and (gen / clips[cid]["path"]).exists() and clips[cid]["src_start"] == chunks[cid]["start"]:
            continue
        preset_name = DEFAULT_CAMS.get(cam, "right90")
        _, phrase = angle_prompt(preset_name, variant)
        log(f"{cid}: re-shooting CAM {cam} ({phrase})")
        rec = run_reshoot_job(dubbed, chunks[cid]["start"], preset_name, variant, gen / f"{cid}_{cam}.mp4", wf, b,
                              key=key, seed=seed)
        if rec["status"] != "success":
            raise RuntimeError(f"{cid}: {rec['status']} {(rec.get('error') or '')[:300]}")
        clips[cid] = {"path": f"{cid}_{cam}.mp4", "angle": cam, "src_start": chunks[cid]["start"], "synced": True,
                      "preset": preset_name, "camera": phrase, "seed": rec["seed"]}
        save_json(clips, clips_path)
    if seams:
        from .util import probe as _probe
        durs = {cid: _probe(gen / c["path"])["duration"] for cid, c in clips.items()}
        for ch in hide_seams(pl, clips, durs, seams, tr["words"]):
            log(f"seam {ch['seam']:.2f}s hidden: {ch['how']}")
        save_json(pl, mc / "plan.json")
    log("assembling the multicam edit (free)")
    out = {}
    for lab, name in ((False, "multicam.mp4"), (True, "multicam_labeled.mp4")):
        assemble(pl, mc / name, clips_json=clips_path, video=dubbed, labels=lab, shake=True, transcript=tr,
                 align_cache=mc / "align_cache", preset=preset, a_label="lip-dub (AI)", color_match=color_match)
        out[name] = mc / name
    return out


def hide_seams(plan: dict, clips: dict, clip_dur: dict, seams: list[float], words: list[dict],
               pre: float = 0.1, post: float = 0.5, min_a: float = 0.4) -> list[dict]:
    """Put a camera cut over each LipDub window seam (the first frames of a window still carry the real
    mouth pose, a visible jump on CAM A). Prefer stretching a neighbouring insert, else cut in a short
    insert from any re-shoot that covers the seam. Edits plan["shots"] in place; returns what changed."""
    changes = []

    def covers(cid, a, b):
        c = clips.get(cid)
        return bool(c) and c["src_start"] <= a + 1e-3 and c["src_start"] + clip_dur.get(cid, 0) - 0.05 >= b

    for t in seams:
        shots = plan["shots"]
        k = next((i for i, x in enumerate(shots) if x["start"] < t < x["end"]), None)
        if k is None or shots[k]["cam"] != "A":
            continue
        a = shots[k]
        cut = round(t - pre, 4)
        nxt = shots[k + 1] if k + 1 < len(shots) else None
        prv = shots[k - 1] if k > 0 else None
        if nxt and nxt["cam"] != "A" and nxt["start"] - t <= 1.6 and covers(nxt.get("chunk"), cut, nxt["end"]) \
                and cut - a["start"] >= min_a:
            changes.append({"seam": t, "how": f"insert {nxt['cam']} starts at {cut:.2f}s (was {nxt['start']:.2f})"})
            nxt["start"] = a["end"] = cut
            continue
        end = next((w["end"] for w in words if w["end"] >= t + post), t + post)
        if prv and prv["cam"] != "A" and t - prv["end"] <= 1.0 and covers(prv.get("chunk"), prv["start"], end) \
                and a["end"] - end >= min_a:
            changes.append({"seam": t, "how": f"insert {prv['cam']} ends at {end:.2f}s (was {prv['end']:.2f})"})
            prv["end"] = a["start"] = end
            continue
        for cid, c in clips.items():  # any re-shoot that spans the seam: cut it in
            cut = round(max(t - pre, c["src_start"] + 0.04), 4)  # its first frame, if it starts after t - pre
            end2 = min(end, c["src_start"] + clip_dur.get(cid, 0) - 0.05)
            if cut <= t - 0.03 and covers(cid, cut, t + post) and cut - a["start"] >= min_a and a["end"] - end2 >= min_a:
                new = {k2: v for k2, v in a.items() if k2 not in ("words", "align", "note")}
                new.update({"cam": c["angle"], "chunk": cid, "start": cut, "end": round(end2, 4), "seam_cover": True})
                tail = dict(a, start=round(end2, 4))
                a["end"] = cut
                shots[k + 1:k + 1] = [new, tail]
                changes.append({"seam": t, "how": f"cut in {c['angle']} ({cid}) {cut:.2f}-{end2:.2f}s"})
                break
    for i, x in enumerate(plan["shots"]):
        x["i"] = i
    return changes
