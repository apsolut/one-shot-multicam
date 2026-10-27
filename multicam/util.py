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


def probe(path: str | Path) -> dict:
    need("ffprobe")
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    res = {"duration": float(info["format"]["duration"])}
    if v:
        num, den = v.get("r_frame_rate", "30/1").split("/")
        res.update(width=int(v["width"]), height=int(v["height"]), fps=float(num) / float(den or 1))
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
