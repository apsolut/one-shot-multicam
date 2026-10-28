"""Small helpers: ffmpeg/ffprobe wrappers, JSON IO, time quantization."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise SystemExit(f"error: `{binary}` not found on PATH")
    return path


def run(cmd: list[str], quiet: bool = True) -> subprocess.CompletedProcess:
    if not quiet or os.environ.get("MULTICAM_VERBOSE"):
        print("+", " ".join(str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(map(str, cmd))}\n{proc.stderr[-4000:]}")
    return proc


def stream_rotation(stream: dict) -> int:
    """Display rotation of an ffprobe video stream in degrees, normalized to 0/90/180/270
    (display-matrix side data wins over the legacy `rotate` tag)."""
    rot = 0.0
    try:
        rot = float((stream.get("tags") or {}).get("rotate", 0) or 0)
    except (TypeError, ValueError):
        pass
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = float(sd["rotation"] or 0)
            except (TypeError, ValueError):
                pass
    return int(round(rot / 90.0)) * 90 % 360


def probe(path: str | Path) -> dict:
    """Duration, upright (display) width/height, fps, audio info; `rotation` when the stream carries one."""
    need("ffprobe")
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    res = {"duration": float(info["format"]["duration"])}
    if v:
        num, den = v.get("r_frame_rate", "30/1").split("/")
        w, h = int(v["width"]), int(v["height"])
        rot = stream_rotation(v)
        if rot % 180 == 90:  # phone clips: landscape pixels + a 90-degree display matrix; ffmpeg autorotates
            w, h = h, w      # on decode, so everything downstream must see the upright (display) size
        res.update(width=w, height=h, fps=float(num) / float(den or 1))
        if rot:
            res["rotation"] = rot
    if a:
        res.update(sample_rate=int(a["sample_rate"]), channels=int(a.get("channels", 2)), audio_codec=a["codec_name"])
    return res


def qt(t: float, fps: float = 30.0) -> float:
    """Quantize a time to the nearest frame boundary."""
    return round(round(t * fps) / fps, 4)


def load_json(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)


def save_json(obj, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
