"""Framing hint: face size vs frame + orientation -> shot class, notice and camera bias (pure, no Comfy)."""
from multicam.framing import NOTES, analyze, classify, suggest


def test_classify_portrait_by_face_height():
    assert classify(720, 1280, 100)["shot"] == "wide"        # 8% of the height: already wide
    assert classify(720, 1280, 330)["shot"] == "medium"
    assert classify(720, 1280, 600)["shot"] == "tight"       # 47%: tight selfie
    assert classify(720, 1280, None)["shot"] == "unknown"


def test_landscape_wins_and_notes():
    f = classify(1920, 1080, 500)
    assert f["orientation"] == "landscape" and f["shot"] == "landscape"
    assert f["note"] == NOTES["landscape"] and "3/4" in f["note"]
    assert "close-ups and side angles" in classify(720, 1280, 90)["note"]
    assert classify(720, 1280, 330)["note"] == ""


def test_suggestions_never_add_a_second_wide_to_a_wide_take():
    for shot in ("wide", "landscape"):
        for eng in ("reshoot", "idlora"):
            cams = suggest({"shot": shot}, eng)
            assert cams and "wide" not in cams.values() and cams["B"] == "closeup"
    assert suggest({"shot": "tight"}, "reshoot") == {"B": "right90", "C": "wide"}
    assert suggest({"shot": "medium"}, "reshoot") is None and suggest(None, "reshoot") is None


def test_analyze_never_raises(tmp_path):
    bad = tmp_path / "x.mp4"; bad.write_bytes(b"not a video")
    assert analyze(bad)["shot"] == "unknown"
