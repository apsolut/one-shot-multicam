"""Live bug 2026-10-28 (project 15d4dc07...): a phone portrait take stored as 1920x1080 HEVC + a 90-degree
display matrix was rendered as a 1280x720 landscape crop of the upright 1080x1920 frames, so the top and
bottom ~34% of the picture were cut off. probe() must report the upright size. Synthetic media only."""
import shutil
import subprocess
from pathlib import Path

import pytest

if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    pytest.skip("needs ffmpeg", allow_module_level=True)

from multicam.assemble import assemble  # noqa: E402
from multicam.reshoot import display_size  # noqa: E402
from multicam.util import probe, stream_rotation  # noqa: E402

W, H, DUR = 320, 180, 2.0  # stored (coded) landscape pixels, like the phone file


def phone_portrait(tmp: Path, rotation: int = 90) -> Path:
    """Landscape pixels with a red left band and blue right band + a display rotation (=> portrait)."""
    raw = tmp / "raw.mp4"
    vf = (f"drawbox=x=0:y=0:w={W // 4}:h={H}:color=red:t=fill,"
          f"drawbox=x={W - W // 4}:y=0:w={W // 4}:h={H}:color=blue:t=fill")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=gray:s={W}x{H}:r=30:d={DUR}",
                    "-f", "lavfi", "-i", f"sine=f=440:sample_rate=48000:d={DUR}", "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(raw)], check=True)
    out = tmp / "phone.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-display_rotation", str(rotation), "-i", str(raw),
                    "-c", "copy", str(out)], check=True)
    return out


def test_stream_rotation_normalizes():
    assert stream_rotation({}) == 0
    assert stream_rotation({"tags": {"rotate": "90"}}) == 90
    assert stream_rotation({"side_data_list": [{"rotation": -90}]}) == 270
    assert stream_rotation({"tags": {"rotate": "90"}, "side_data_list": [{"rotation": 180}]}) == 180
    assert stream_rotation({"side_data_list": [{"side_data_type": "x"}]}) == 0


@pytest.mark.parametrize("rot", [90, -90])
def test_probe_reports_upright_size(tmp_path, rot):
    v = phone_portrait(tmp_path, rot)
    info = probe(v)
    assert (info["width"], info["height"]) == (H, W)  # portrait, as ffmpeg decodes it
    assert info["rotation"] in (90, 270)
    assert display_size(v) == (H, W)


def test_probe_plain_video_unchanged(tmp_path):
    raw = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=gray:s={W}x{H}:r=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw)], check=True)
    info = probe(raw)
    assert (info["width"], info["height"]) == (W, H) and "rotation" not in info


def _frame(path: Path, t: float, w: int, h: int) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout


def test_render_of_rotated_take_keeps_whole_frame_and_length(tmp_path):
    src = phone_portrait(tmp_path, 90)
    plan = {"source": str(src), "duration": DUR,
            "shots": [{"i": 0, "cam": "A", "start": 0.0, "end": DUR, "chunk": None}], "chunks": [], "angle_names": {"A": "real"}}
    out = tmp_path / "final.mp4"
    # max_long like the hosted machine; must scale the portrait frame, never crop it into landscape
    assemble(plan, out, video=src, clips={}, align=False, continuity=False, max_long=W, preset="ultrafast")
    o = probe(out)
    assert o["height"] > o["width"], f"rendered {o['width']}x{o['height']} landscape from a portrait take"
    assert (o["width"], o["height"]) == (H, W)
    assert abs(o["duration"] - DUR) < 0.05  # never ends before the source
    # display-rotated 90: the stored left (red) / right (blue) bands become the top/bottom of the upright frame;
    # a landscape centre crop would have thrown both away
    w, h = o["width"], o["height"]
    px = _frame(out, 0.5, w, h)
    def rgb(x, y):
        i = (y * w + x) * 3
        return px[i], px[i + 1], px[i + 2]
    top, bottom, mid = rgb(w // 2, 5), rgb(w // 2, h - 6), rgb(w // 2, h // 2)
    colored = [c for c in (top, bottom) if max(c) - min(c) > 80]
    assert len(colored) == 2, (top, bottom)  # both bands survived
    assert max(mid) - min(mid) < 40  # gray middle
