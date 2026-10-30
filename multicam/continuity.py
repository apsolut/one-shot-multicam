"""Continuity rules shared by the Talk/Music/Dub assembler and the Scene editor.

1. Never show a pulled-back wide (CrossView "wide"/"aerial") while the person on screen is speaking: at that
   size the lips can't be read and the shot looks frozen. Wides are only for silence / listening.
2. Every other insert placed over speech must pass the cheap mouth-motion check (lipcheck): no face,
   mouth far smaller than in the real take, or articulation far below the real take -> replaced.
3. Replacement order: another speech-safe insert that covers the span (and passes the check), else the
   real take (CAM A).
4. One camera = one set: every insert on a camera uses the same camera phrase and seed (reshoot.camera_seed),
   so a camera never jumps between two hallucinated rooms within an edit.
"""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

WIDE_PRESETS = {"wide", "aerial"}
MIN_SPEECH_OVERLAP = 0.08   # s of a word inside a shot that counts as "speaking"


def is_wide(name: str | None) -> bool:
    n = (name or "").lower()
    return n in WIDE_PRESETS or n.startswith("wide") or "wide shot" in n


def speaking(words: list[dict], t0: float, t1: float, min_overlap: float = MIN_SPEECH_OVERLAP) -> bool:
    """True if any word overlaps [t0, t1] by more than min_overlap seconds."""
    return any(min(w["end"], t1) - max(w["start"], t0) > min_overlap for w in words or [])


def wide_cams(plan: dict, explicit: list[str] | None = None) -> set[str]:
    """Cameras that are wides in this plan: explicit list > plan params > angle names saying 'wide'."""
    if explicit is not None:
        return set(explicit)
    p = (plan.get("params") or {}).get("silent_only")
    if p is not None:
        return set(p)
    return {c for c, n in (plan.get("angle_names") or {}).items() if c != "A" and is_wide(n)}


@lru_cache(maxsize=256)
def _check(clip: str, mtime: float, c0: float, c1: float, src: str | None, s0: float | None) -> tuple[bool, str]:
    from .lipcheck import check_insert
    r = check_insert(clip, c0, c1, src, s0)
    return r["ok"], r["reason"]


def lip_ok(clip: str, c0: float, c1: float, source: str | None = None, s0: float | None = None) -> tuple[bool, str]:
    """Cached mouth-motion verdict for clip time [c0, c1] vs the real take at s0. Missing OpenCV -> pass."""
    from .lipcheck import available
    if not available():
        return True, "lipcheck unavailable (opencv missing)"
    try:
        return _check(str(clip), Path(clip).stat().st_mtime, round(c0, 3), round(c1, 3),
                      str(source) if source else None, round(s0, 3) if s0 is not None else None)
    except Exception as e:  # noqa: BLE001  (unreadable clip -> don't block the render on the check itself)
        return True, f"lipcheck error: {e}"


def _clip_dur(c: dict) -> float:
    return float((c.get("info") or {}).get("duration") or 5.04)


def guard_shots(plan: dict, clips: dict, words: list[dict], video: str | None = None,
                silent_only: list[str] | None = None, check: bool = True, log=print) -> tuple[dict, list[dict]]:
    """-> (plan copy with unsafe inserts replaced, report). clips = assemble.load_clips() output."""
    plan = copy.deepcopy(plan)
    wides = wide_cams(plan, silent_only)
    # Music: sung spans (lyric lines, or audible stretches when no lyrics) are speech for the wide rule only;
    # the mouth check still needs real words (a dance clip with no lyrics doesn't have to show singing)
    sung = [{"start": a, "end": b} for a, b in plan.get("vocal_spans") or []]
    report = []

    def safe(cam: str, c: dict, t0: float, t1: float, offset: float = 0.0) -> tuple[bool, str]:
        if cam in wides or is_wide(c.get("preset")):
            return False, "wide over speech" if speaking(words, t0, t1) else "wide over singing"
        if not check or not speaking(words, t0, t1):
            return True, "ok"
        c0 = t0 - c["src_start"] + offset
        return lip_ok(c["path"], c0, c0 + (t1 - t0), video, t0)

    for s in plan["shots"]:
        if s["cam"] == "A" or not s.get("chunk"):
            continue
        t0, t1 = s["start"], s["end"]
        if not (speaking(words, t0, t1) or speaking(sung, t0, t1)):
            continue  # silence / listening / instrumental: any angle is fine, wides included
        c = clips.get(s["chunk"])
        if not c:
            continue  # assemble falls back to A on its own
        ok, why = safe(s["cam"], c, t0, t1, float(s.get("offset", 0.0)))
        if ok:
            continue
        alt = None
        for cid, c2 in clips.items():
            cam2 = c2.get("angle")
            if cid == s["chunk"] or not cam2 or cam2 == "A":
                continue
            if not (c2["src_start"] + 0.05 <= t0 and t1 <= c2["src_start"] + _clip_dur(c2) - 0.1):
                continue
            if safe(cam2, c2, t0, t1)[0]:
                alt = (cid, cam2)
                break
        rec = {"shot": s.get("i"), "start": t0, "end": t1, "cam": s["cam"], "chunk": s["chunk"], "reason": why,
               "words": s.get("words", "")}
        if alt:
            s["guard"] = {"from": f"{s['cam']}:{s['chunk']}", "reason": why}
            s["chunk"], s["cam"], s["offset"] = alt[0], alt[1], 0.0
            s["angle_name"] = (plan.get("angle_names") or {}).get(alt[1], alt[1])
            s.pop("align", None)
            rec["to"] = f"CAM {alt[1]} ({alt[0]})"
        else:
            s["guard"] = {"from": f"{s['cam']}:{s['chunk']}", "reason": why}
            s["cam"] = "A"
            s.pop("chunk", None)
            s.pop("align", None)
            rec["to"] = "CAM A (real)"
        log(f"continuity: {t0:.2f}-{t1:.2f}s CAM {rec['cam']} ({rec['reason']}) -> {rec['to']}")
        report.append(rec)
    return plan, report


@lru_cache(maxsize=32)
def guess_posture(video: str, t: float = 1.0) -> str:
    """'seated' | 'standing' from the source frame. A talking-head framing (face >= 8% of the frame height:
    desk/chair selfie, legs out of frame) is read as seated; a small face high up in a full-body frame
    as standing. Cheap and conservative: anything unclear -> 'seated' (the common case for a talking take)."""
    from .lipcheck import face_landmarks, read_frames
    fr = read_frames(video, t, t + 0.5, fps=4.0)
    for f in fr:
        lm = face_landmarks(f)
        if lm:
            H = f.shape[0]
            _, y, _, h = lm["box"]
            if h / H < 0.08 and (y + h / 2) / H < 0.35:
                return "standing"
            return "seated"
    return "seated"
