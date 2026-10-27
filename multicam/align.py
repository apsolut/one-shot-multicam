"""`multicam align`: lip-sync alignment of generated inserts to the real audio.

Re-shoot clips (clips.json "synced": true) are already time-locked to the source and skip this.

The generated clip speaks the chunk's words at its own pace, so the naive mapping
clip_time = source_time - src_start puts the wrong mouth shapes on screen. We transcribe the
clip's own audio (faster-whisper word timestamps), match its words to the real words of the
insert, and choose an in-point (and, if needed, a video-only speed) so the generated word
onsets land on the real word onsets:

    clip_time(t) = g_first + speed * (t - r_first)

r_first / r_last = real start of the first / last matched insert word,
g_first / g_last = start of the same words in the generated clip.
speed = (g_last - g_first) / (r_last - r_first), applied only if it differs from 1 by more than
`speed_tol` (default 8 %), clamped to [0.85, 1.15]. Audio is never touched.
"""
from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from .transcribe import norm, whisper_words
from .util import load_json, qt, save_json


def clip_words(path: str, cache_dir: Path, model: str = "small") -> list[dict]:
    p = Path(path)
    st = p.stat()
    cache = cache_dir / f"{p.stem}.words.json"
    if cache.exists():
        c = load_json(cache)
        if c.get("size") == st.st_size and c.get("mtime") == int(st.st_mtime) and c.get("model") == model:
            return c["words"]
    words = whisper_words(path, model=model)
    save_json({"clip": str(p), "size": st.st_size, "mtime": int(st.st_mtime), "model": model,
               "text": " ".join(w["text"] for w in words), "words": words}, cache)
    return words


def match_words(real: list[dict], gen: list[dict]) -> dict[int, int]:
    """Map index in `real` -> index in `gen` (equal tokens, plus 1:1 substitutions like Claude/cloud)."""
    a, b = [norm(w["text"]) for w in real], [norm(w["text"]) for w in gen]
    m = {}
    for op, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                m[i1 + k] = j1 + k
    return m


def align_shot(shot: dict, tr: dict, chunk: dict, clip: dict, gen: list[dict], speed_tol=0.08,
               speed_min=0.85, speed_max=1.15, min_match=0.6, min_len=0.8) -> dict:
    words = tr["words"]
    chunk_words = words[chunk["w0"]: chunk["w1"] + 1]
    m = match_words(chunk_words, gen)
    ins_idx = [i for i in range(chunk["w0"], chunk["w1"] + 1)
               if shot["start"] <= (words[i]["start"] + words[i]["end"]) / 2 < shot["end"]]
    exact = [i for i in ins_idx if (i - chunk["w0"]) in m
             and norm(words[i]["text"]) == norm(gen[m[i - chunk["w0"]]]["text"])]
    pairs = [(i, m[i - chunk["w0"]]) for i in ins_idx if (i - chunk["w0"]) in m]
    naive_in = shot["start"] - clip["src_start"]
    res = {"naive_clip_in": round(naive_in, 3), "insert_words": [words[i]["text"] for i in ins_idx],
           "matched": len(pairs), "exact": len(exact), "of": len(ins_idx)}
    if not ins_idx or len(exact) < max(1, round(min_match * len(ins_idx))):
        res.update(status="mismatch", reason="generated clip does not contain the insert's words")
        return res
    (ri0, gi0), (ri1, gi1) = pairs[0], pairs[-1]
    # anchors: start of the first matched word and end of the last matched word (span)
    r_first, g_first = words[ri0]["start"], gen[gi0]["start"]
    r_last, g_last = words[ri1]["end"], gen[gi1]["end"]
    raw_speed = (g_last - g_first) / (r_last - r_first) if r_last - r_first > 0.05 else 1.0
    if len(pairs) < 2:  # one word is too little evidence for a pace estimate: offset only
        speed = 1.0
    else:
        speed = 1.0 if abs(raw_speed - 1.0) <= speed_tol else min(speed_max, max(speed_min, raw_speed))
    if speed != raw_speed and speed != 1.0:
        # speed was clamped: split the residual error by anchoring the span midpoint
        rm, gm = (r_first + r_last) / 2, (g_first + g_last) / 2
        clip_in = gm + speed * (shot["start"] - rm)
    else:
        clip_in = g_first + speed * (shot["start"] - r_first)
    start, end = shot["start"], shot["end"]
    clip_dur = clip["info"]["duration"] - 0.02
    notes = []
    if clip_in < 0:  # generated speech starts too early in the clip: enter later
        start = start + (-clip_in) / speed
        clip_in = 0.0
        notes.append(f"start moved to {start:.3f}")
    clip_out = clip_in + speed * (end - start)
    if clip_out > clip_dur:  # clip runs out: exit earlier (the next A shot gets longer)
        end = start + (clip_dur - clip_in) / speed
        clip_out = clip_dur
        notes.append(f"end moved to {end:.3f}")
    res.update(speed=round(speed, 4), raw_speed=round(raw_speed, 4),
               clip_in=round(clip_in, 3), clip_out=round(clip_out, 3),
               offset=round(clip_in - naive_in, 3),
               anchors=[{"word": words[ri0]["text"], "real_start": r_first, "gen_start": g_first},
                        {"word": words[ri1]["text"], "real_end": r_last, "gen_end": g_last}])
    if notes:
        # still has to cover the insert's spoken words and keep the minimum insert length
        if end - start < min_len or start > words[ri0]["start"] + 0.05 or end < words[ri1]["end"] - 0.05:
            res.update(status="out_of_range", reason="aligned window does not fit in the clip; " + "; ".join(notes))
            return res
        res.update(status="ok", trimmed={"start": round(start, 3), "end": round(end, 3)}, notes=notes)
    else:
        res["status"] = "ok"
    return res


def align_plan(plan: dict, tr: dict, clips: dict, cache_dir: Path, model="small", **kw) -> list[dict]:
    """Annotate plan shots in place with `align`, `offset`, `speed`. Returns a report."""
    chunks = {c["id"]: c for c in plan["chunks"]}
    report, trims = [], []
    for s in plan["shots"]:
        if s["cam"] == "A" or s.get("chunk") not in clips:
            continue
        c = clips[s["chunk"]]
        ch = chunks[s["chunk"]]
        if c.get("synced"):
            # re-shoot clips are time-locked to the source (real audio frozen in): identity mapping
            r = {"status": "ok", "method": "synced", "offset": 0.0, "speed": 1.0, "raw_speed": 1.0,
                 "naive_clip_in": round(s["start"] - c["src_start"], 3), "clip_in": round(s["start"] - c["src_start"], 3),
                 "matched": 0, "exact": 0, "of": 0}
        elif plan.get("mode") == "music":
            # words only where lyrics exist AND the generated clip demonstrably sings them; else onsets
            r = None
            if s.get("words") and ch.get("w0") is not None:
                gen = clip_words(c["path"], cache_dir, model=model)
                c["words"] = " ".join(w["text"] for w in gen)
                r = align_shot(s, tr, ch, c, gen, **kw)
                r["method"] = "words"
                if r["status"] != "ok":
                    r = None
            if r is None:
                from .music import onset_align
                r = onset_align(s, c, plan["source"])
        else:
            gen = clip_words(c["path"], cache_dir, model=model)
            c["words"] = " ".join(w["text"] for w in gen)
            r = align_shot(s, tr, ch, c, gen, **kw)
            r["method"] = "words"
        s["align"] = r
        if r["status"] == "ok":
            s["offset"], s["speed"] = r["offset"], r["speed"]
            if r.get("trimmed"):
                trims.append((s, r, c["src_start"]))
        else:
            s["offset"], s["speed"] = 0.0, 1.0
        report.append({"shot": s["i"], "chunk": s["chunk"], "cam": s["cam"], "clip": Path(c["path"]).name, **r})
    fps = plan.get("fps", 30.0)
    for s, r, src0 in trims:  # move the insert's cut points; neighbours (A) absorb the change
        t = r["trimmed"]
        k = plan["shots"].index(s)
        new_s, new_e = qt(t["start"], fps), qt(t["end"], fps)
        if new_e - new_s > (r["clip_out"] - r["clip_in"]) / s["speed"] + 1e-6:
            new_e = round(new_e - 1.0 / fps, 4)  # quantization must not run past the clip
        s["offset"] = round(r["clip_in"] + s["speed"] * (new_s - t["start"]) - (new_s - src0), 4)
        if k > 0:
            plan["shots"][k - 1]["end"] = new_s
        if k + 1 < len(plan["shots"]):
            plan["shots"][k + 1]["start"] = new_e
        s["start"], s["end"] = new_s, new_e
    for sh in plan["shots"]:
        sh["duration"] = round(sh["end"] - sh["start"], 4)
    plan["cuts"] = [sh["start"] for sh in plan["shots"][1:]]
    return report


def save_clips_json(clips: dict, path: Path) -> None:
    out = {}
    for cid, c in clips.items():
        out[cid] = {k: v for k, v in c.items() if k not in ("info",)}
    save_json(out, path)
