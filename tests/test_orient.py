"""Orientation guards (live bug 2026-10-28: a phone partner photo + a still that came back rotated 90 degrees
was used automatically for B's single). Synthetic images only, no Comfy calls."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from multicam import orient as O  # noqa: E402
from multicam import scene as S  # noqa: E402


def phone_jpeg(path, orientation=6, size=(400, 225)):
    """Landscape pixels + EXIF Orientation, like a Samsung/iPhone portrait photo."""
    im = Image.new("RGB", size, (200, 30, 30))
    im.paste((30, 30, 200), (0, 0, size[0] // 4, size[1]))  # blue band on the left edge of the stored pixels
    ex = Image.Exif()
    ex[0x0112] = orientation
    im.save(path, "JPEG", exif=ex.tobytes())
    return Path(path)


def test_normalize_upload_bakes_exif_orientation(tmp_path):
    p = phone_jpeg(tmp_path / "partner.jpg", 6)
    assert O.exif_orientation(p) == 6 and O.image_size(p) == (225, 400)
    out = O.normalize_upload(p)
    with Image.open(out) as im:
        assert im.size == (225, 400)  # upright portrait pixels
        assert int(im.getexif().get(0x0112, 1) or 1) == 1
        # orientation 6 = rotate 90 CW for display: the stored left edge (blue) ends up at the top
        assert im.getpixel((112, 10))[2] > 150 and im.getpixel((112, 390))[0] > 150
    assert O.normalize_upload(out) == out  # idempotent, no re-encode


def test_normalize_upload_orientation_8(tmp_path):
    out = O.normalize_upload(phone_jpeg(tmp_path / "p.jpg", 8))
    with Image.open(out) as im:
        assert im.size == (225, 400) and im.getpixel((112, 390))[2] > 150  # rotated the other way


def test_check_still_rejects_orientation_mismatch(tmp_path, monkeypatch):
    src = phone_jpeg(tmp_path / "src.jpg", 6)  # displays portrait
    land = tmp_path / "land.png"
    Image.new("RGB", (1392, 752)).save(land)
    monkeypatch.setattr(O, "face_roll", lambda p: None)
    ok, why = O.check_still(land, src)
    assert not ok and "landscape" in why
    port = tmp_path / "port.png"
    Image.new("RGB", (752, 1392)).save(port)
    assert O.check_still(port, src)[0]


def test_check_still_rejects_rolled_subject(tmp_path, monkeypatch):
    src, still = tmp_path / "src.png", tmp_path / "still.png"
    Image.new("RGB", (752, 1392)).save(src)
    Image.new("RGB", (752, 1392)).save(still)
    rolls = {str(src): 0, str(still): 90}
    monkeypatch.setattr(O, "face_roll", lambda p: rolls[str(p)])
    assert O.check_still(still, src) == (False, "subject rotated 90 degrees")
    rolls[str(still)] = None  # face lost entirely while the source shows an upright one
    assert not O.check_still(still, src)[0]
    rolls[str(still)] = 0
    assert O.check_still(still, src) == (True, "ok")
    rolls[str(src)] = None  # no face in the source: only the aspect check applies
    rolls[str(still)] = 90
    assert O.check_still(still, src)[0]


def test_face_roll_on_rotated_scores(monkeypatch):
    monkeypatch.setattr(O, "face_scores", lambda p: {0: 0, 90: 6889, 270: 2304})  # e1.png of the live bug
    assert O.face_roll("x") == 90
    monkeypatch.setattr(O, "face_scores", lambda p: {0: 14884, 90: 0, 270: 0})
    assert O.face_roll("x") == 0
    monkeypatch.setattr(O, "face_scores", lambda p: {0: 0, 90: 0, 270: 0})
    assert O.face_roll("x") is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_video_frames_are_autorotated(tmp_path):
    """Phone clips store landscape pixels + a displaymatrix; frame grabs must come out upright."""
    v = tmp_path / "rot.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "plain.mp4")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(tmp_path / "plain.mp4"),
                    "-c", "copy", str(v)], check=True)
    from multicam.reshoot import display_size
    assert display_size(v) == (180, 320)
    f = S._frame(v, 0.5, tmp_path / "f.png")
    with Image.open(f) as im:
        assert im.size == (180, 320)
    from multicam.frames import best_face_frame
    best_face_frame(v, tmp_path / "b.png")
    with Image.open(tmp_path / "b.png") as im:
        assert im.size == (180, 320)


# ------------------------------------------------------------------ scene still picker
def _scene(tmp_path, checks):
    sd = tmp_path / "stills"
    sd.mkdir()
    stills = {}
    for i, role in enumerate(["single", "ots", "close", "single_2", "ots_2", "close_2", "single_3", "ots_3"], 1):
        p = sd / f"e{i}.png"
        Image.new("RGB", (752, 1392)).save(p)
        stills[role] = str(p)
    orig = {}
    for shot in ("single", "close"):
        p = sd / f"original_{shot}.jpg"
        Image.new("RGB", (720, 1280)).save(p)
        orig[shot] = str(p)
    return {"partner": "photo", "person": "man", "stills": stills, "originals": orig, "b_clips": {}, "lines": [],
            "still_checks": {k: {"ok": v, "reason": "ok" if v else "subject rotated 90 degrees"} for k, v in checks.items()}}


LIVE = {"e1": False, "e2": True, "e3": True, "e4": False, "e5": True, "e6": True, "e7": False, "e8": True}


def test_original_photo_is_default_and_rotated_never_auto(tmp_path):
    sc = _scene(tmp_path, LIVE)
    picks = S.default_picks(sc)
    assert picks == {"single": "original_single", "ots": "e2", "close": "original_close"}
    cand = S.candidates(sc)
    assert [o["name"] for o in cand["single"]] == ["original_single", "e1", "e4", "e7"]
    assert cand["single"][0]["original"] and not cand["single"][1]["ok"]
    sc["originals"] = {}  # e.g. a fictional character: no original photo
    assert "single" not in S.default_picks(sc)
    assert Path(S.pick_path(sc, "single")).name in ("e2.png", "e3.png")  # falls back to a good still, never e1


def test_set_pick_marks_choice_and_drops_stale_clips(tmp_path):
    sc = _scene(tmp_path, LIVE)
    sc["picks"] = S.default_picks(sc)
    sc["b_clips"] = {"0": {"shot": "single", "still": "original_single.jpg"}, "react": {"shot": "ots", "still": "e2.png"}}
    dropped = S.set_pick(sc, "single", "e4")  # an explicit pick of a flagged still is allowed (user's call)
    assert dropped == ["0"] and "react" in sc["b_clips"]
    assert Path(S.pick_path(sc, "single")).name == "e4.png"
    assert S.default_picks(sc, keep=sc["picks"])["single"] == "e4"
    with pytest.raises(ValueError):
        S.set_pick(sc, "single", "e2")  # e2 is an over-the-shoulder still


def test_photo_prompts_do_not_force_a_gender():
    sc = {"partner": "photo", "person": "man", "look": "night", "a_look": "you", "lines": []}
    P = S.still_prompts(sc)
    assert not any(" man" in t for _, t, _ in P)
    assert P[0][1].startswith("<sks> ") and P[0][2] == S.SINGLE_ANGLE_LORA
    assert "same man" not in S.b_visual(sc, "single")


def test_make_originals_crops_portrait(tmp_path):
    p = phone_jpeg(tmp_path / "partner.jpg", 6, size=(1600, 900))
    sc = {"partner": "photo", "photo": str(p)}
    got = S.make_originals(sc, tmp_path)
    assert "single" in got  # no face in a flat test image -> no close-up crop
    with Image.open(got["single"]) as im:
        w, h = im.size
        assert h > w and abs(w / h - 9 / 16) < 0.01


def test_web_picker_and_two_stage_generate(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from multicam.server import App, create_app
    A = App(tmp_path / "data")
    client = TestClient(create_app(A))
    sid = "abc123"
    d = A.data / "scenes" / sid
    d.mkdir(parents=True)
    sc = _scene(d, LIVE)
    for k, v in list(sc["stills"].items()):
        sc["stills"][k] = str(Path(v).resolve())
    tr = {"duration": 5.0, "sentences": [{"i": 0, "start": 0, "end": 2, "text": "Hi."},
                                          {"i": 1, "start": 2.5, "end": 4.5, "text": "Bye."}]}
    (d / "transcript.json").write_text(json.dumps(tr))
    sc.update({"version": 1, "source": str(d / "source.mp4"), "transcript": str(d / "transcript.json"),
               "lines": [{"after": 0, "text": "Wait. Really?", "shot": "single"}], "a_clips": {}, "jobs": [],
               "voice": "af_heart", "look": "night", "photo": None})
    sc["picks"] = S.default_picks(sc)
    (d / "scene.json").write_text(json.dumps(sc))
    (d / "web.json").write_text(json.dumps({"id": sid, "created": time.time(), "partner": "photo", "log": []}))
    r = client.get(f"/api/scene/{sid}").json()
    single = r["stills"]["single"]
    assert [o["name"] for o in single if o["chosen"]] == ["original_single"]
    assert [o["ok"] for o in single] == [True, False, False, False]
    assert r["estimate"]["stage"] == "clips"
    r = client.post(f"/api/scene/{sid}/pick", json={"shot": "single", "still": "e7"}).json()
    assert [o["name"] for o in r["stills"]["single"] if o["chosen"]] == ["e7"]
    assert client.post(f"/api/scene/{sid}/pick", json={"shot": "single", "still": "nope"}).status_code == 400
    s2 = json.loads((d / "scene.json").read_text())
    s2["stills"] = {}
    (d / "scene.json").write_text(json.dumps(s2))
    r = client.get(f"/api/scene/{sid}").json()
    assert r["estimate"]["stage"] == "stills" and r["estimate"]["n_jobs"] == 1  # stills first, then pick
    assert [o["name"] for o in r["stills"]["single"]] == ["original_single"]  # free original shown before any job
