"""Re-shoot engine: render the REAL take of a chunk from a new virtual camera (no still, no new speech).

LTX-2.3 22B + the CrossView IC-LoRA (Cseti/LTX2.3-22B_IC-LoRA-CrossView-Prompt, Apache-2.0) take the source
segment as the in-context reference video and re-render it from the camera described in the prompt.
The real audio is frozen into the audio latent (LTXVSetAudioRefTokens), so the clip is time-locked to the
source: clip t == source (chunk.start + t). The insert therefore follows the real head/body motion and
mouth shapes frame for frame, and alignment is the identity (offset 0, speed 1) — no whisper retime.

The CrossView LoRA was trained on a fixed vocabulary:
    crossview. new camera angle: {azimuth}, {elevation}, {distance}.
Each camera preset below lists several variants. Every insert on a camera uses the SAME variant and seed
(one camera = one virtual set: two different phrasings/seeds hallucinate two different rooms, review
2026-10-28); a re-roll moves that shot to the next variant and a fresh seed.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Callable

from .util import need, probe, run

# preset key (same keys as the Qwen still names, so cam_stills works for both engines) -> label, variants
ANGLES: dict[str, tuple[str, list[str]]] = {
    "right90": ("Side right", ["far to the right, same height, closer", "to the right, lower, closer",
                               "far to the right, higher, closer"]),
    "left90": ("Side left", ["far to the left, same height, closer", "to the left, lower, closer",
                             "far to the left, higher, closer"]),
    "right45": ("3/4 right", ["to the right, same height, closer", "slightly to the right, lower, closer",
                              "to the right, higher, same distance"]),
    "left45": ("3/4 left", ["to the left, same height, closer", "slightly to the left, lower, closer",
                            "to the left, higher, same distance"]),
    "wide": ("Wide", ["slightly to the left, higher, further", "slightly to the right, same height, further",
                      "same angle, higher, further"]),
    "closeup": ("Close-up", ["slightly to the right, same height, closer", "slightly to the left, lower, closer",
                             "same angle, same height, closer"]),
    "lowangle": ("Low angle", ["slightly to the left, lower, closer", "slightly to the right, lower, same distance",
                               "same angle, lower, closer"]),
    "aerial": ("High angle", ["same angle, higher, further", "slightly to the right, higher, same distance",
                              "slightly to the left, higher, further"]),
}
DEFAULT_CAMS = {"B": "right90", "C": "wide"}
GEN = {"fps": 24, "frames": 121, "long": 1280, "short": 704}  # 5.04 s, 2 stages (half res -> x2 upscaler)


def angle_prompt(preset: str, variant: int = 0) -> tuple[str, str]:
    """-> (prompt, camera phrase)."""
    _, variants = ANGLES.get(preset, ANGLES["right90"])
    phrase = variants[variant % len(variants)]
    return f"crossview. new camera angle: {phrase}.", phrase


def display_size(video: str | Path) -> tuple[int, int]:
    """Decoded (display) width/height: phone clips often store landscape pixels + a 90-degree rotation."""
    from .util import run as _run
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-print_format", "json", "-show_streams", str(video)]).stdout
    st = json.loads(out)["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    rot = int(float(st.get("tags", {}).get("rotate", 0) or 0))
    for sd in st.get("side_data_list", []) or []:
        rot = int(float(sd.get("rotation", rot) or 0))
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


def gen_size(width: int, height: int) -> tuple[int, int]:
    return (GEN["short"], GEN["long"]) if height >= width else (GEN["long"], GEN["short"])


def make_ref(video: str | Path, start: float, out: str | Path, width: int, height: int,
             fps: int = GEN["fps"], frames: int = GEN["frames"]) -> Path:
    """Cut the reference: `frames` frames from `start`, scaled/cropped to width x height, real audio.
    Pads with the last frame / silence if the take ends early, so the clip is always full length."""
    need("ffmpeg")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = frames / fps
    vf = (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
          f"tpad=stop_mode=clone:stop_duration={dur:.3f},setsar=1")
    has_audio = "sample_rate" in probe(video)
    src_a = "[0:a]" if has_audio else "[1:a]"
    extra = [] if has_audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-i", str(video), *extra,
         "-filter_complex", f"[0:v]{vf}[v];{src_a}apad=whole_dur={dur:.3f},aresample=48000[a]",
         "-map", "[v]", "-map", "[a]", "-frames:v", str(frames), "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])
    return out


def run_reshoot_job(video: str | Path, start: float, preset: str, variant: int, out_path: Path,
                    workflow: dict, bindings: dict, key=None, seed=None, work_dir: Path | None = None,
                    on_status: Callable[[dict], None] | None = None) -> dict:
    """Cut + upload the reference, submit one CrossView re-shoot, wait, download. Record has no key in it."""
    from .comfy import download, find_videos, job_detail, patch_workflow, submit, upload, wait
    out_path = Path(out_path)
    w, h = gen_size(*display_size(video))
    ref = make_ref(video, start, (work_dir or out_path.parent) / f"{out_path.stem}_ref.mp4", w, h)
    prompt, phrase = angle_prompt(preset, variant)
    seed = seed if seed is not None else random.randint(0, 2**48)
    rec = {"engine": "reshoot", "preset": preset, "camera": phrase, "seed": seed, "status": "uploading",
           "started": time.time()}
    emit = (lambda: on_status(dict(rec))) if on_status else (lambda: None)
    emit()
    values = {"video": upload(str(ref), key), "prompt": prompt, "seed": seed, "lat_w": w // 2, "lat_h": h // 2}
    rec["prompt_id"] = submit(patch_workflow(workflow, bindings, values), key)
    rec["status"] = "submitted"; emit()

    def st_cb(st):
        rec["status"] = st; emit()
    st = wait(rec["prompt_id"], key, on_status=st_cb)
    rec["elapsed_s"] = round(time.time() - rec["started"], 1)
    if st == "success":
        vids = find_videos(job_detail(rec["prompt_id"], key).get("outputs"))
        if not vids:
            rec["status"], rec["error"] = "error", "no video in outputs"
        else:
            rec["output"] = str(download(vids[0], out_path, key))
    else:
        rec["error"] = json.dumps(job_detail(rec["prompt_id"], key).get("execution_error"))[:1500]
    emit()
    return rec


def camera_seed(scope: str, cam: str) -> int:
    """Deterministic seed per (project, camera): every insert on that camera shares it (consistent set)."""
    import hashlib
    return int(hashlib.sha256(f"{scope}|{cam}".encode()).hexdigest()[:12], 16)


def insert_ordinals(plan: dict, consistent: bool = True) -> dict[tuple[str, str], int]:
    """(chunk, cam) -> camera variant for that insert. consistent (default): 0 for every insert, so all uses of
    a camera share one camera phrase; consistent=False: the n-th insert on a camera gets variant n (old)."""
    seen: dict[str, int] = {}
    out = {}
    for s in plan["shots"]:
        if s["cam"] == "A" or not s.get("chunk") or (s["chunk"], s["cam"]) in out:
            continue
        out[(s["chunk"], s["cam"])] = 0 if consistent else seen.get(s["cam"], 0)
        seen[s["cam"]] = seen.get(s["cam"], 0) + 1
    return out
