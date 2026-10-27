"""Offline tests for Scene mode logic (no Comfy, no ffmpeg): python -m pytest tests/ (or python tests/test_scene.py)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from multicam import scene as S  # noqa: E402

TR = {"duration": 17.5, "sentences": [
    {"i": 0, "start": 0.0, "end": 2.98, "text": "I used to lose hours debugging and rewriting code."},
    {"i": 1, "start": 3.74, "end": 4.96, "text": "Then I tried Claude Code."},
    {"i": 2, "start": 5.7, "end": 7.4, "text": "My workflow transformed overnight."},
    {"i": 3, "start": 8.36, "end": 12.16, "text": "Now I ship features three times faster."},
    {"i": 4, "start": 12.8, "end": 17.12, "text": "If you're still coding the old way, you're leaving a lot behind."}]}


def test_parse_lines():
    ls = S.parse_lines(["1:Wait. Really?", "3@ots:Who reviews it?", "Prove it."], 5)
    assert ls == [{"after": 1, "text": "Wait. Really?"}, {"after": 3, "text": "Who reviews it?", "shot": "ots"},
                  {"after": 4, "text": "Prove it."}]
    assert S.parse_lines(["9:too far"], 5)[0]["after"] == 4


def test_turns_alternate_and_bounds():
    sc = {"lines": S.parse_lines(["1:a", "2:b", "4:c"], 5), "edit": {"tight": False}}
    T = S.turns(sc, TR)
    assert [t["who"] for t in T] == ["A", "B", "A", "B", "A", "B"]
    a = [t for t in T if t["who"] == "A"]
    assert a[0]["sents"] == [0, 1] and a[2]["sents"] == [3, 4]
    for t in a:  # pads never cross into the neighbouring sentence
        assert 0 <= t["start"] < t["end"] <= TR["duration"]
    assert a[1]["start"] >= TR["sentences"][1]["end"] and a[1]["end"] <= TR["sentences"][3]["start"]


def test_tight_splits_pauses_and_skips():
    sc = {"lines": S.parse_lines(["1:a", "4:c"], 5), "edit": {"tight": True, "skip": [2]}}
    T = S.turns(sc, TR)
    a = [t["sents"] for t in T if t["who"] == "A"]
    assert a == [[0], [1], [3], [4]]  # 0.76 s pause split, sentence 2 cut, 0.64 s pause split
    assert [t["who"] for t in T] == ["A", "A", "B", "A", "A", "B"]
    assert all(2 not in t.get("sents", []) for t in T)
    sc["lines"] = S.parse_lines(["2:after a skipped one"], 5)
    T = S.turns(sc, TR)
    assert T[2] == {"who": "B", "line": 0} and T[1]["sents"] == [1]


def test_match_line_drops_padding():
    w = [{"text": t, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, t in
         enumerate(["You", "let", "an", "AI", "write", "your", "code,", "sign..."])]
    got = S._match_line(w, "You let an AI write your code?")
    assert got[-1]["text"] == "code," and len(got) == 7
    assert S._match_line(w, "") == w


def test_stills_prompts_and_estimate():
    sc = {"partner": "clone", "person": "man", "look": "night", "a_look": "a man with headphones", "lines": [],
          "stills": {}, "b_clips": {}, "a_clips": {}}
    P = S.still_prompts(sc)
    assert len(P) == 8 and [r for r, _, _ in P[:3]] == ["single", "ots", "close"]
    assert "headphones" in P[1][1] and "leather jacket" in P[0][1]
    assert S.estimate(sc)["n_jobs"] == 1  # stills only; no react clip without lines
    sc["lines"] = [{"after": 1, "text": "hi"}, {"after": 2, "text": "yo"}]
    e = S.estimate(sc)
    assert e["jobs"] == {"stills": 1, "line": 2, "react": 1, "reshoot": 0}
    sc["partner"] = "character"
    assert S.estimate(sc)["jobs"]["stills"] == 2


def test_prompts_have_exact_speech():
    sc = {"partner": "photo", "person": "woman", "voice": "af_heart", "look": "night"}
    p = S.line_prompt(sc, "ots", "Okay, but who reviews all that code?")
    assert "[SPEECH]: Okay, but who reviews all that code?\n" in p and p.startswith("[VISUAL]:")
    assert "off-screen to the right" in p and "Female" in p
    assert S.line_seconds("Hi.") == 3.0 and S.line_seconds(" ".join(["w"] * 30)) == 5.0


def test_suggest_fallback():
    got = S.suggest_lines(TR, "clone", max_lines=2)
    assert len(got) == 2 and all(0 <= g["after"] < 5 and g["text"] for g in got)


def test_workflow_bindings_exist():
    wf = json.loads((S.WF_DIR / "scene_edit_api.json").read_text())
    b = json.loads((S.WF_DIR / "scene_edit_bindings.json").read_text())
    for k, pairs in b.items():
        if k.startswith("_"):
            continue
        for node, inp in pairs:
            assert node in wf and inp in wf[node]["inputs"], (k, node, inp)
    assert len(b["_outputs"]) == 8


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
