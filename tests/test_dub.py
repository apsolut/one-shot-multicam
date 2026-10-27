"""Offline tests for Dub mode helpers (no Comfy calls)."""
from multicam import dub as D


def _tr():
    words, t = [], 0.0
    for sent in ("I used to lose hours debugging code.", "Then I tried it.", "Now I ship faster than ever before today."):
        for w in sent.split():
            words.append({"text": w, "start": t, "end": t + 0.3}); t += 0.35
        t += 0.6
    from multicam.transcribe import group
    for i, w in enumerate(words):
        w["i"] = i
    sentences, phrases = group(words)
    return {"words": words, "sentences": sentences, "phrases": phrases}


def test_windows_cover_take_and_respect_max():
    tr = _tr()
    wins = D.plan_windows(tr, 9.0, max_s=4.0)
    assert wins[0]["start"] == 0.0 and abs(wins[-1]["end"] - 9.0) < 1e-6
    for a, b in zip(wins, wins[1:]):
        assert abs(a["end"] - b["start"]) < 1e-6  # contiguous: stitching never drops frames
    assert all(D.window_frames(w["end"] - w["start"]) % 8 == 1 for w in wins)


def test_parse_numbered_and_fallback():
    assert D.parse_numbered("1. Hallo.\n2) Welt!\n", 2) == ["Hallo.", "Welt!"]
    out = D.parse_numbered("1. Nur eins", 2)
    assert out[0] == "Nur eins" and not out[1]


def test_fit_and_estimate():
    assert D.fit_report("Then I tried Claude Code.", "Dann nahm ich Claude Code.")["ok"]
    assert not D.fit_report("Hi there.", "Guten Morgen, wie geht es Ihnen heute an diesem schönen Tag?")["ok"]
    e = D.estimate([{"start": 0, "end": 8.1}, {"start": 8.1, "end": 17.6}], 4)
    assert e["jobs"] == 2 + 2 + 4 and 0.3 < e["usd"] < 0.6


def test_dub_prompt_quotes():
    p = D.dub_prompt('He said "go"', "de")
    assert "German" in p and '"go"' not in p


def _plan(shots):
    return {"shots": [dict(x, i=i) for i, x in enumerate(shots)]}


def test_hide_seams_stretches_next_insert():
    plan = _plan([{"cam": "A", "start": 7.1, "end": 9.2}, {"cam": "B", "start": 9.2, "end": 10.8, "chunk": "c03"}])
    ch = D.hide_seams(plan, {"c03": {"src_start": 8.0, "angle": "B"}}, {"c03": 5.04}, [8.11], [])
    assert ch and plan["shots"][1]["start"] == plan["shots"][0]["end"] == 8.01


def test_hide_seams_cuts_in_when_no_neighbour():
    words = [{"text": "x", "start": 8.5, "end": 8.74}]
    plan = _plan([{"cam": "A", "start": 7.2, "end": 11.3}, {"cam": "B", "start": 11.3, "end": 12.7, "chunk": "c03"}])
    ch = D.hide_seams(plan, {"c03": {"src_start": 8.02, "angle": "B"}}, {"c03": 5.04}, [8.11], words)
    cams = [(s["cam"], s["start"], s["end"]) for s in plan["shots"]]
    assert ch and cams[1] == ("B", 8.06, 8.74) and cams[0][2] == 8.06 and cams[2][1] == 8.74
