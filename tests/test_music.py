"""Music mode (BETA): the beat planner must never be a silent no-op. Synthetic media only (ffmpeg lavfi), no Comfy.
Before the fix: a clip with no trackable beat (quiet room, a cappella, silence) planned 0 inserts and the UI showed
"$0.00 · every insert already has a clip" with no Generate button; lyric times like 0:03 or 3,2 crashed analyze;
a clip without an audio track failed with a raw ffmpeg CalledProcessError."""
import shutil
import subprocess
from pathlib import Path

import pytest

if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    pytest.skip("needs ffmpeg", allow_module_level=True)
pytest.importorskip("librosa")
pytest.importorskip("cv2")

from multicam.music import MusicParams, build_music_plan, lyric_words, parse_time  # noqa: E402

BEAT = "aevalsrc=0.9*sin(2*PI*60*t)*exp(-25*mod(t\\,0.5))+0.3*sin(2*PI*220*t)*lt(mod(t\\,0.25)\\,0.03):s=22050:d={d}"


def clip(tmp: Path, name: str, d: float, audio: str | None) -> Path:
    out = tmp / name
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=180x320:r=15:d={d}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio.format(d=d), "-shortest", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def inserts(plan):
    return [s for s in plan["shots"] if s["cam"] != "A"]


def test_parse_time_formats():
    assert parse_time("8") == 8.0
    assert parse_time(" 3.25 ") == 3.25
    assert parse_time("3,2") == pytest.approx(3.2)
    assert parse_time("0:03") == 3.0
    assert parse_time("1:02.5") == 62.5
    for bad in ("", "abc", "1:2:3:4", "Verse 1"):
        with pytest.raises(ValueError):
            parse_time(bad)


def test_lyric_words_skips_bad_lines_instead_of_crashing():
    bad = []
    w = lyric_words(["0:03-0:05=hello there", "1,5=la", "Chorus=oh oh", "7.0-6.0=backwards", "9.0="], 0.3, bad=bad)
    assert [x["text"] for x in w] == ["la", "hello", "there"]  # sorted by time
    assert w[0]["start"] == 1.5 and w[1]["start"] == 3.0 and w[2]["end"] == 5.0
    assert bad == ["Chorus=oh oh", "7.0-6.0=backwards", "9.0="]
    assert [x["i"] for x in w] == [0, 1, 2]
    assert {x["phrase"] for x in w} == {0, 1}  # phrases numbered over the lines that parsed


def test_beat_track_clip_gets_beat_cuts(tmp_path):
    plan, _ = build_music_plan(clip(tmp_path, "beat.mp4", 10, BEAT), MusicParams())
    assert plan["beat_source"] == "tracked" and 100 <= plan["tempo"] <= 140
    ins = inserts(plan)
    assert len(ins) >= 2
    beats = plan["beats"]
    for s in ins:  # every insert starts and ends on a tracked beat (frame-quantized)
        assert min(abs(s["start"] - b) for b in beats) < 0.05 and min(abs(s["end"] - b) for b in beats) < 0.05
        assert 1.0 - 0.05 <= s["end"] - s["start"] <= 2.0 + 0.05
    assert plan["note"] == ""


def test_silent_clip_falls_back_to_grid_not_zero_inserts(tmp_path):
    plan, _ = build_music_plan(clip(tmp_path, "quiet.mp4", 10, "anullsrc=r=22050:cl=mono:d={d}"), MusicParams())
    assert plan["beat_source"] == "grid"
    assert len(inserts(plan)) >= 2 and plan["chunks"]
    assert "No clear beat" in plan["note"]


def test_short_clip_explains_why_there_are_no_inserts(tmp_path):
    plan, _ = build_music_plan(clip(tmp_path, "short.mp4", 3, BEAT), MusicParams())
    assert inserts(plan) == []
    assert "Music needs at least" in plan["note"]


def test_clip_without_audio_track_says_so(tmp_path):
    with pytest.raises(ValueError, match="no sound"):
        build_music_plan(clip(tmp_path, "mute.mp4", 6, None), MusicParams())


def test_bad_lyrics_reported_in_plan_note(tmp_path):
    plan, tr = build_music_plan(clip(tmp_path, "beat.mp4", 8, BEAT), MusicParams(),
                                lyrics=["0:02-0:03=la la", "Verse one=hey"])
    assert tr["text"] == "la la"
    assert "Skipped 1 lyric line" in plan["note"]


def test_server_exposes_plan_note(tmp_path):
    pytest.importorskip("fastapi")
    from multicam.server import App
    src = clip(tmp_path, "quiet.mp4", 10, "anullsrc=r=22050:cl=mono:d={d}")
    A = App(tmp_path / "data")
    with open(src, "rb") as fh:
        p = A.create("quiet.mp4", fh, "music", None, None, None)
    A.analyze(p)
    st = A.public(p)
    assert st["steps"]["plan"]["status"] == "done" and "No clear beat" in st["steps"]["plan"]["msg"]
    assert "No clear beat" in st["plan"]["note"]
    assert any(s["cam"] != "A" for s in st["plan"]["shots"])
    assert st["estimate"]["jobs"] >= 2
