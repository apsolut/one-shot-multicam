"""Shake is off by default; Wide-preset inserts get a smooth eased pull-back (perspective, sub-pixel, cubic)."""
import inspect
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from multicam import assemble as A

ROOT = Path(__file__).resolve().parent.parent


def _plan():
    return {"source": "a.mp4", "chunks": [{"id": "c01", "start": 1.0, "end": 2.0}, {"id": "c02", "start": 3.0, "end": 4.0}],
            "shots": [{"i": 0, "cam": "A", "chunk": None, "start": 0.0, "end": 1.0},
                      {"i": 1, "cam": "C", "chunk": "c01", "start": 1.0, "end": 2.0},
                      {"i": 2, "cam": "A", "chunk": None, "start": 2.0, "end": 3.0},
                      {"i": 3, "cam": "B", "chunk": "c02", "start": 3.0, "end": 4.0},
                      {"i": 4, "cam": "A", "chunk": None, "start": 4.0, "end": 5.0}]}


def _clips():
    return {"c01": {"path": "/x/wide.mp4", "angle": "C", "src_start": 1.0, "preset": "wide", "info": {"duration": 3.0}},
            "c02": {"path": "/x/side.mp4", "angle": "B", "src_start": 3.0, "preset": "right90", "info": {"duration": 3.0}}}


def _filters(monkeypatch, **kw):
    cmds = []
    monkeypatch.setattr(A, "probe", lambda p: {"width": 720, "height": 1280, "fps": 30.0, "duration": 5.0, "audio_codec": "aac"})
    monkeypatch.setattr(A, "run", lambda cmd: cmds.append(cmd))
    monkeypatch.setattr(A, "need", lambda *_: None)
    A.assemble(_plan(), "/tmp/out.mp4", clips=_clips(), video="a.mp4", align=False, continuity=False, **kw)
    fc = cmds[0][cmds[0].index("-filter_complex") + 1].split(";")
    return {int(m.group(1)): c for c in fc if (m := re.search(r"\[v(\d+)\]$", c))}


def test_shake_off_by_default_everywhere():
    assert inspect.signature(A.assemble).parameters["shake"].default is False
    srv = (ROOT / "multicam" / "server.py").read_text()
    assert "def render(self, p: Project, shake: bool = False):" in srv
    assert 'A.render(p, shake=bool(body.get("shake", False)))' in srv
    assert "shake=True" not in (ROOT / "multicam" / "dub.py").read_text()
    assert "shake=not a.no_shake" not in (ROOT / "multicam" / "cli.py").read_text()


def test_preset_reaches_the_shot_and_server_passes_it():
    shots = A.resolve_shots(_plan(), _clips(), 30.0)
    assert [s.get("preset") for s in shots if s["src"] == "generated"] == ["wide", "right90"]
    assert '"preset": c.get("still")' in (ROOT / "multicam" / "server.py").read_text()


def test_pullback_only_on_wide_and_no_shake(monkeypatch):
    v = _filters(monkeypatch)
    assert "perspective=" in v[1] and "interpolation=cubic" in v[1] and "eval=frame" in v[1]
    assert "perspective=" not in v[3] and "sin(" not in v[3] and "sin(" not in v[1]
    assert all("perspective=" not in v[i] for i in (0, 2, 4))
    # pull-back can be switched off; shake stays opt-in
    v = _filters(monkeypatch, pullback=False)
    assert "perspective=" not in v[1]
    v = _filters(monkeypatch, shake=True)
    assert "sin(" in v[3]


def test_pullback_filter_is_eased_and_clamped():
    f = A.pullback_filter(45, 1.14)
    assert "cos(PI*min(in/44\\,1))" in f and "1.1400" in f
    assert A.pullback_filter(1).count("min(in/1\\,1)") == 8  # never divide by zero on a 1-frame insert


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_pullback_render_is_monotonic_and_keeps_size(tmp_path):
    # a white square on black: its rendered width must shrink frame by frame from ~1.14x to 1.0x, never grow
    src = tmp_path / "sq.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=180x320:r=30:d=1",
                    "-vf", "drawbox=x=50:y=120:w=80:h=80:color=white:t=fill", "-pix_fmt", "yuv420p", str(src)], check=True)
    n = 30
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", A.pullback_filter(n, 1.14) + ",format=gray",
                          "-f", "rawvideo", "-"], check=True, capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 320, 180)
    assert fr.shape[0] == n
    widths = [(f[160] > 128).sum() for f in fr.astype(int)]
    assert widths[0] >= 89 and abs(widths[-1] - 80) <= 1
    assert all(b <= a + 0.5 for a, b in zip(widths, widths[1:]))  # monotonic, no back-and-forth


def test_wide_hint_says_pulls_back():
    app = (ROOT / "multicam" / "web" / "app.js").read_text()
    assert 'hint: "Wide: pulls back, shows the room"' in app
