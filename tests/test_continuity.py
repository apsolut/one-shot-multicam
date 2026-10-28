"""Continuity rules (review 2026-10-28): no wide / frozen insert over speech, one set per camera,
Scene partner seated in the same room. Offline: no Comfy calls, no media from the repo."""
import json
import subprocess

import pytest

from multicam import continuity as C
from multicam import lipcheck as L
from multicam.plan import PlanParams, build_plan


def _tr(n_sent=6):
    words, t = [], 0.0
    sents = ["I used to lose hours debugging and rewriting code.", "Then I tried Claude Code and it changed things.",
             "Now I ship features three times faster than before.", "With better quality than ever in my life.",
             "If you still code the old way you lose a lot.", "You are leaving productivity behind every day."]
    for sent in sents[:n_sent]:
        for w in sent.split():
            words.append({"text": w, "start": round(t, 3), "end": round(t + 0.28, 3)}); t += 0.33
        t += 0.5
    from multicam.transcribe import group
    for i, w in enumerate(words):
        w["i"] = i
    sentences, phrases = group(words)
    return {"source": "take.mp4", "duration": round(t, 3), "words": words, "sentences": sentences, "phrases": phrases}


# ------------------------------------------------------------------ speech detection
def test_speaking_needs_real_overlap():
    w = [{"start": 1.0, "end": 1.3}]
    assert C.speaking(w, 0.9, 1.5)
    assert not C.speaking(w, 1.25, 2.0)          # 0.05 s tail of a word is not "speaking"
    assert not C.speaking(w, 1.4, 3.0)


def test_is_wide():
    assert C.is_wide("wide") and C.is_wide("aerial") and C.is_wide("Wide shot from the left")
    assert not C.is_wide("right90") and not C.is_wide("profile") and not C.is_wide(None)


# ------------------------------------------------------------------ planner
def test_planner_never_puts_wide_over_speech():
    tr = _tr()
    pl = build_plan(tr, PlanParams())            # default: C = wide -> silent only
    ins = [s for s in pl["shots"] if s["cam"] != "A"]
    assert ins, "the take should still get inserts"
    for s in ins:
        if C.speaking(tr["words"], s["start"], s["end"]):
            assert s["cam"] not in pl["params"]["silent_only"], s
    assert {s["cam"] for s in ins} == {"B"}
    P = PlanParams(); P.silent_only = []        # a lip-readable C (e.g. closeup) alternates again
    assert {s["cam"] for s in build_plan(tr, P)["shots"] if s["cam"] != "A"} == {"B", "C"}


def test_pin_to_wide_over_speech_is_ignored(capsys):
    tr = _tr()
    pl0 = build_plan(tr, PlanParams())
    cid = next(s["chunk"] for s in pl0["shots"] if s["cam"] != "A")
    pl = build_plan(tr, PlanParams(), pins=[f"{cid}=C"])
    assert all(s["cam"] != "C" for s in pl["shots"])
    assert "ignored" in capsys.readouterr().out


# ------------------------------------------------------------------ assembler guard
def _plan_and_clips():
    tr = _tr(3)
    P = PlanParams(); P.silent_only = []
    pl = build_plan(tr, P)
    shots = [s for s in pl["shots"] if s["cam"] != "A"]
    clips = {}
    for s in shots:
        ch = next(c for c in pl["chunks"] if c["id"] == s["chunk"])
        clips[s["chunk"]] = {"path": f"/nonexistent/{s['chunk']}_{s['cam']}.mp4", "angle": s["cam"],
                             "src_start": ch["start"], "preset": "wide" if s["cam"] == "C" else "right90",
                             "info": {"duration": 5.04}}
    return tr, pl, clips


def test_guard_replaces_wide_over_speech_with_A_or_covering_B():
    tr, pl, clips = _plan_and_clips()
    wide = [s for s in pl["shots"] if s["cam"] == "C"]
    assert wide
    out, rep = C.guard_shots(pl, clips, tr["words"], check=False, log=lambda *a: None)
    assert all(s["cam"] != "C" for s in out["shots"])
    assert len(rep) == len(wide) and all(r["reason"] == "wide over speech" for r in rep)
    for r in rep:  # replacement: a B clip that covers the span, else the real take
        assert r["to"].startswith("CAM B") or r["to"] == "CAM A (real)"
    assert pl["shots"] != out["shots"] and any(s["cam"] == "C" for s in pl["shots"])  # input not mutated


def test_guard_keeps_wide_over_silence():
    tr, pl, clips = _plan_and_clips()
    s = next(s for s in pl["shots"] if s["cam"] == "C")
    silent = [w for w in tr["words"] if not (s["start"] - 0.5 < w["start"] < s["end"] + 0.5)]
    out, rep = C.guard_shots(pl, clips, silent, check=False, log=lambda *a: None)
    assert not rep and [x["cam"] for x in out["shots"]] == [x["cam"] for x in pl["shots"]]


def test_guard_drops_frozen_profile(monkeypatch):
    tr, pl, clips = _plan_and_clips()
    monkeypatch.setattr(C, "lip_ok", lambda *a, **k: (False, "mouth frozen (articulation 0.004)"))
    out, rep = C.guard_shots(pl, clips, tr["words"], video="take.mp4", log=lambda *a: None)
    assert all(s["cam"] == "A" for s in out["shots"])
    assert any("frozen" in r["reason"] for r in rep)


# ------------------------------------------------------------------ lipcheck thresholds (calibration pinned)
REF = {"face": True, "mouth_w": 0.26, "articulation": 0.10}


@pytest.mark.parametrize("ins,ok", [
    ({"face": True, "mouth_w": 0.117, "articulation": 0.09}, False),   # dub CAM C wide 5.9 s (0.44x)
    ({"face": True, "mouth_w": 0.075, "articulation": 0.08}, False),   # dub CAM C wide 13.6 s (0.28x)
    ({"face": True, "mouth_w": 0.151, "articulation": 0.12}, True),    # scene A side (0.58x)
    ({"face": True, "mouth_w": 0.209, "articulation": 0.07}, True),    # dub B profile
    ({"face": True, "mouth_w": 0.20, "articulation": 0.01}, False),    # mouth doesn't move at all
    ({"face": True, "mouth_w": 0.20, "articulation": 0.03}, False),    # far stiller than the real take
    ({"face": False, "mouth_w": 0.0, "articulation": 0.0}, False),     # no face where A has one
])
def test_lipcheck_verdict(ins, ok):
    assert L.verdict(ins, REF)[0] is ok


def test_lipcheck_without_reference_uses_absolute_floor():
    assert L.verdict({"face": True, "mouth_w": 0.098, "articulation": 0.35})[0]      # B single medium shot
    assert not L.verdict({"face": True, "mouth_w": 0.05, "articulation": 0.35})[0]


def test_lipcheck_runs_on_a_faceless_clip(tmp_path):
    pytest.importorskip("cv2")
    v = tmp_path / "bars.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=24:duration=2",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    r = L.check_insert(v, 0.2, 1.4, v, 0.2)
    assert r["insert"]["frames"] >= 10 and not r["insert"]["face"]
    assert r["ok"] and "no face in the real take" in r["reason"]   # nothing to compare: don't block


# ------------------------------------------------------------------ one set per camera
def test_every_insert_on_a_camera_shares_variant_and_seed():
    from multicam.reshoot import camera_seed, insert_ordinals
    P = PlanParams(); P.silent_only = []
    pl = build_plan(_tr(), P)
    ords = insert_ordinals(pl)
    assert len(ords) >= 3 and set(ords.values()) == {0}
    assert sorted(set(insert_ordinals(pl, consistent=False).values()))[-1] >= 1   # old behaviour still available
    assert camera_seed("proj1", "C") == camera_seed("proj1", "C") != camera_seed("proj1", "B")


# ------------------------------------------------------------------ Scene: partner posture + set
def _sc(**kw):
    return {"partner": "clone", "person": "man", "look": "night", "a_look": "a bearded man", "voice": "self",
            "source": "/nonexistent.mp4", "posture": "seated", **kw}


def test_scene_partner_prompts_are_seated_same_room():
    from multicam import scene as S
    sc = _sc()
    for role, prompt, _ in S.still_prompts(sc):
        assert "seated, at the same height" in prompt and "same room/lighting as the source frame" in prompt, role
        assert "city lights" not in prompt and "Change the lighting and background" not in prompt
    for txt in (S.line_prompt(sc, "single", "Hi?"), S.react_prompt(sc, "ots")):
        assert "seated at the same height as the other speaker" in txt
        assert "same room/lighting as the source frame" in txt and "city lights" not in txt


def test_scene_posture_defaults_from_source_and_old_look_mode():
    from multicam import scene as S
    sc = _sc(posture=None)
    assert S.a_posture(sc) == "seated"                     # unreadable source -> seated (common case)
    assert "standing" in S.still_prompts(_sc(posture="standing"))[0][1]
    old = S.still_prompts(_sc(set="look"))[0][1]           # explicit "look" set keeps its own room...
    assert "city lights" in old and "seated" in old         # ...but still mirrors A's posture


def test_scene_a_reshoots_are_never_wide(tmp_path):
    from multicam import scene as S
    tr = _tr()
    (tmp_path / "t.json").write_text(json.dumps(tr))
    sc = _sc(transcript=str(tmp_path / "t.json"), lines=[{"after": 1, "text": "Really?", "shot": "single"}],
             a_clips={}, b_clips={}, edit={})
    wants = S.a_wants(sc)
    assert wants and all(p == "right90" for _, _, p in wants)
