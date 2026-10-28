"""How wide is the uploaded take? Face height vs frame height (YuNet / Haar from lipcheck) plus orientation.

Free and local (a few frames, CPU). The result biases the default re-shoot cameras and drives a one-line notice:
a take that is already wide gets close-ups + side angles instead of another wide; a tight selfie is told that
subtle angles (3/4) change little and that wide / side give a visible difference."""
from __future__ import annotations

from pathlib import Path

WIDE_FACE = 0.17    # face box < 17% of frame height: already a wide / medium-wide shot
TIGHT_FACE = 0.34   # face box > 34% of frame height: a tight selfie

NOTES = {
    "landscape": "Wide clip detected: close-ups will crop in and 3/4 angles move less.",
    "wide": "Your clip is already wide, so we suggest close-ups and side angles instead of another wide.",
    "tight": "Tight selfie: subtle angles like 3/4 change little. Wide and side give a visible difference.",
}
# re-shoot presets (server.ANGLES keys) per framing; "medium"/"unknown" keep the normal defaults
SUGGEST_RESHOOT = {"landscape": {"B": "closeup", "C": "right90"}, "wide": {"B": "closeup", "C": "right90"},
                   "tight": {"B": "right90", "C": "wide"}}
SUGGEST_STILLS = {"landscape": {"B": "closeup", "C": "lowangle"}, "wide": {"B": "closeup", "C": "lowangle"}}


def classify(width: float, height: float, face_h: float | None) -> dict:
    """Pure: frame size + face box height (px, or None when no face was found) -> framing dict."""
    landscape = width > height * 1.05
    frac = (face_h / height) if (face_h and height) else None
    if landscape:
        shot = "landscape"
    elif frac is None:
        shot = "unknown"
    elif frac < WIDE_FACE:
        shot = "wide"
    elif frac > TIGHT_FACE:
        shot = "tight"
    else:
        shot = "medium"
    return {"orientation": "landscape" if landscape else "portrait", "face_frac": None if frac is None else round(frac, 3),
            "shot": shot, "note": NOTES.get(shot, "")}


def suggest(framing: dict | None, engine: str) -> dict | None:
    shot = (framing or {}).get("shot")
    return (SUGGEST_RESHOOT if engine == "reshoot" else SUGGEST_STILLS).get(shot)


def analyze(path: str | Path, duration: float | None = None, n: int = 3) -> dict:
    """Median face height over n frames spread through the take. Never raises: {"shot": "unknown"} on any failure."""
    try:
        from .lipcheck import _cv2, face_landmarks
        cv2 = _cv2()
        cap = cv2.VideoCapture(str(path))
        dur = duration or (cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, cap.get(cv2.CAP_PROP_FPS) or 25.0))
        hs, W, H = [], 0, 0
        for i in range(n):
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000.0 * dur * (i + 1) / (n + 1))
            ok, fr = cap.read()
            if not ok:
                continue
            H, W = fr.shape[:2]
            lm = face_landmarks(fr)
            if lm:
                hs.append(lm["box"][3])
        cap.release()
        if not W:
            return {"orientation": None, "face_frac": None, "shot": "unknown", "note": ""}
        hs.sort()
        return classify(W, H, hs[len(hs) // 2] if hs else None)
    except Exception:  # noqa: BLE001 - framing is a hint, never a blocker
        return {"orientation": None, "face_frac": None, "shot": "unknown", "note": ""}
