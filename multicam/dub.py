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


def stitch(video: str | Path, wins: list[dict], clips: dict[str, str], out: str | Path) -> Path:
    """Dubbed CAM A: each window's LipDub clip trimmed to the window, concatenated, with its generated
    speech. Windows without a clip fall back to the real take (video and audio)."""
    need("ffmpeg")
    info = probe(video)
    fps = round(info.get("fps", 30.0), 3)
    w, h = info["width"], info["height"]
    parts, filt = [], []
    for i, win in enumerate(wins):
        d = win["end"] - win["start"]
        c = clips.get(win["id"])
        if c:
            parts += ["-i", str(c)]
            k = sum(1 for p in parts if p == "-i") - 1
            filt.append(f"[{k}:v]trim=duration={d:.4f},setpts=PTS-STARTPTS,fps={fps:g},"
                        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p[v{i}];"
                        f"[{k}:a]atrim=duration={d:.4f},asetpts=PTS-STARTPTS,aresample=48000,apad=whole_dur={d:.4f}[a{i}]")
        else:
            parts += ["-ss", f"{win['start']:.4f}", "-t", f"{d:.4f}", "-i", str(video)]
            k = sum(1 for p in parts if p == "-i") - 1
            filt.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={fps:g},setsar=1,format=yuv420p[v{i}];"
                        f"[{k}:a]asetpts=PTS-STARTPTS,aresample=48000,apad=whole_dur={d:.4f}[a{i}]")
    n = len(wins)
    filt.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    run(["ffmpeg", "-y", "-v", "error", *parts, "-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", f"{fps:g}",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    return Path(out)
