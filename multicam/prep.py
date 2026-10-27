"""`multicam prep`: per-chunk audio slice, first frame, ID LoRA prompt, and jobs.json."""
from __future__ import annotations

from pathlib import Path

from .util import need, probe, run, save_json

VISUAL = {
    "profile": (
        "Side profile camera angle of the same person, same outfit, same room and lighting, "
        "speaking naturally toward someone just off-camera, natural head movement and lip sync, "
        "small hand gestures, slight handheld camera sway, shallow depth of field, photorealistic, "
        "vertical smartphone video."
    ),
    "wide": (
        "Wide shot from a few meters back of the same person, same outfit, same room and lighting, "
        "upper body and surroundings visible, talking energetically to camera, natural head movement "
        "and lip sync, slight handheld camera sway, photorealistic, vertical smartphone video."
    ),
    "side": (
        "Three-quarter side camera angle of the same person, same outfit, same room and lighting, "
        "speaking naturally, natural head movement and lip sync, slight handheld camera sway, "
        "photorealistic, vertical smartphone video."
    ),
    "lowangle": (
        "Low-angle camera looking up at the same person, same outfit, same room and lighting, speaking "
        "with energy, natural head movement and lip sync, slight handheld camera sway, photorealistic, "
        "vertical smartphone video."
    ),
    "closeup": (
        "Tight close-up of the same person's face, same outfit, same room and lighting, speaking "
        "expressively, natural head movement, accurate lip sync, slight handheld camera sway, "
        "photorealistic skin detail, vertical smartphone video."
    ),
}
SOUNDS = "Energetic male voice, close to the microphone, quiet room, no music."

MUSIC_BASE = ("the same person, same outfit, same place and lighting, dancing energetically and "
              "lip-syncing to music, photorealistic")
MUSIC_VISUAL = {
    "side": "Three-quarter side view of " + MUSIC_BASE + ", slight handheld sway.",
    "profile": "Side profile view of " + MUSIC_BASE + ", slight handheld sway.",
    "closeup": "Tight close-up on the face of " + MUSIC_BASE + ", expressive mouth movement.",
    "wide": "Wide shot showing " + MUSIC_BASE + ".",
}
MUSIC_SOUNDS = "loud electronic dance music, energetic"

GEN = {"width": 720, "height": 1280, "fps": 24, "seconds": 5, "frames": 121}  # LTX-2.3 ID LoRA settings


def build_prompt(angle_name: str, words: str) -> str:
    visual = VISUAL.get(angle_name, VISUAL["profile"])
    return f"[VISUAL]: {visual}\n[SPEECH]: {words}\n[SOUNDS]: {SOUNDS}\n"


def build_music_prompt(angle_name: str, words: str, extra: str | None = None) -> str:
    visual = MUSIC_VISUAL.get(angle_name, MUSIC_VISUAL["side"])
    if extra:
        visual = visual.rstrip(".") + f", {extra}."
    return f"[VISUAL]: {visual}\n[SPEECH]: {words or 'ah ah ah'}\n[SOUNDS]: {MUSIC_SOUNDS}\n"


def prep(plan: dict, out_dir: str | Path, video: str | Path | None = None) -> dict:
    need("ffmpeg")
    video = Path(video or plan["source"]).resolve()
    info = probe(video)
    out = Path(out_dir).resolve()
    for sub in ("audio", "frames", "prompts", "stills", "clips"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    jobs = []
    for ch in plan["chunks"]:
        if not ch.get("generate"):
            continue
        cid, angle = ch["id"], ch["angle"]
        angle_name = plan["angle_names"].get(angle, angle)
        dur = round(ch["end"] - ch["start"], 3)
        wav = out / "audio" / f"{cid}.wav"
        png = out / "frames" / f"{cid}.png"
        txt = out / "prompts" / f"{cid}.txt"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ch['start']:.3f}", "-t", f"{dur:.3f}", "-i", video,
             "-vn", "-c:a", "pcm_s16le", "-ar", str(info.get("sample_rate", 44100)), "-ac", "2", wav])
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ch['start']:.3f}", "-i", video, "-frames:v", "1", png])
        if plan.get("mode") == "music":
            prompt = build_music_prompt(angle_name, ch.get("words") or "", plan.get("visual_extra"))
        else:
            prompt = build_prompt(angle_name, ch["words"])
        txt.write_text(prompt)
        jobs.append({
            "id": f"{cid}_{angle}",
            "chunk": cid, "angle": angle, "angle_name": angle_name,
            "start": ch["start"], "end": ch["end"], "duration": dur,
            "speech": ch.get("words") or "",
            "prompt": prompt, "prompt_file": str(txt),
            "audio": str(wav), "first_frame": str(png),
            # step 1 output (Qwen-Image-Edit 2511 multiple angles), drop the still here:
            "angle_still": str(out / "stills" / f"{cid}_{angle}.png"),
            # step 2 output (LTX-2.3 ID LoRA), drop the mp4 here; `assemble --clips-dir` picks it up:
            "expected_clip": str(out / "clips" / f"{cid}_{angle}.mp4"),
            "qwen": {"template": "Qwen 1 Click Multiple Character Angles", "angle": angle_name},
            "ltx": {"template": "LTX-2.3: ID LoRA", **GEN},
        })
    manifest = {"source": str(video), "plan": plan.get("_path"), "generation": GEN, "jobs": jobs}
    save_json(manifest, out / "jobs.json")
    return manifest
