"""Cheap "frozen while speaking" check for inserts (CPU only, no Comfy job, ~0.5 s per insert).

An insert shown while the on-screen person is speaking has to show readable lip motion. Per insert we sample
the clip at 12 fps, find the face in each frame (OpenCV YuNet: box + mouth-corner landmarks; Haar cascades
as a fallback), and measure:

* mouth size: mouth-corner distance / frame width, relative to the real take over the same span. A pulled-back
  wide shrinks the mouth to 0.28-0.45x the real take's (~40 px of a 704 px frame); lip motion that small can't
  be read on a phone, so the shot reads as "mouth closed, body frozen" even when the pixels move a little.
  Side/profile re-shoots keep >= 0.58x. Without a reference (generated B lines) an absolute floor applies.
* articulation: std over time of the darkness inside a landmark-aligned mouth crop (opening/closing),
  compared with the real take over the same source span. Far below the real take = frozen.
* no face found while the real take has one = unusable over speech.

Calibrated on the review clips of 2026-10-28: every flagged wide failed, every accepted B/side insert passed
(tests/test_lipcheck.py pins the thresholds with synthetic numbers).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

MIN_MOUTH_REL = 0.5     # insert mouth width / real-take mouth width below this -> pulled back too far (a wide)
MIN_MOUTH_W = 0.08      # absolute floor (mouth width / frame width) when there is no real take to compare
FROZEN_RATIO = 0.45     # articulation / real-take articulation below this -> frozen
FROZEN_ABS = 0.02       # articulation below this -> frozen whatever the reference says
SAMPLE_FPS = 12.0
DETECT_W = 480
YUNET = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"


def _cv2():
    import cv2  # opencv-python-headless (web / music extras)
    return cv2


@lru_cache(maxsize=1)
def _yunet():
    cv2 = _cv2()
    if not YUNET.exists() or not hasattr(cv2, "FaceDetectorYN"):
        return None
    try:
        return cv2.FaceDetectorYN.create(str(YUNET), "", (DETECT_W, DETECT_W), 0.6, 0.3, 50)
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=1)
def _haar():
    cv2 = _cv2()
    d = cv2.data.haarcascades
    return (cv2.CascadeClassifier(d + "haarcascade_frontalface_default.xml"),
            cv2.CascadeClassifier(d + "haarcascade_profileface.xml"))


def read_frames(path: str | Path, t0: float, t1: float, fps: float = SAMPLE_FPS) -> list:
    """BGR frames sampled every 1/fps s between t0 and t1 (clip time)."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t0) * 1000.0)
    out, t_next = [], max(0.0, t0)
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t > t1:
            break
        if t + 0.02 < t_next:
            continue
        out.append(fr)
        t_next += 1.0 / fps
    cap.release()
    return out


def face_landmarks(frame) -> dict | None:
    """-> {"box": (x, y, w, h), "mouth": ((xr, yr), (xl, yl)) | None} in frame pixels."""
    cv2 = _cv2()
    H, W = frame.shape[:2]
    k = DETECT_W / W
    small = cv2.resize(frame, (DETECT_W, int(round(H * k))))
    det = _yunet()
    if det is not None:
        det.setInputSize((small.shape[1], small.shape[0]))
        _, faces = det.detect(small)
        if faces is not None and len(faces):
            r = max(faces, key=lambda f: f[2] * f[3]) / k
            return {"box": tuple(float(v) for v in r[:4]),
                    "mouth": ((float(r[10]), float(r[11])), (float(r[12]), float(r[13])))}
    g = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    front, prof = _haar()
    for d, flip in ((front, False), (prof, False), (prof, True)):
        img = cv2.flip(g, 1) if flip else g
        r = d.detectMultiScale(img, scaleFactor=1.1, minNeighbors=5, minSize=(18, 18))
        if len(r):
            x, y, w, h = max(r, key=lambda b: b[2] * b[3])
            if flip:
                x = img.shape[1] - x - w
            return {"box": (x / k, y / k, w / k, h / k), "mouth": None}
    return None


def _mouth_crop(frame, lm):
    """Landmark-aligned grey mouth crop (48x40), or None."""
    cv2 = _cv2()
    import numpy as np
    H, W = frame.shape[:2]
    x, y, w, h = lm["box"]
    if lm["mouth"]:
        (xr, yr), (xl, yl) = lm["mouth"]
        cx, cy = (xr + xl) / 2, (yr + yl) / 2
        d = max(float(np.hypot(xl - xr, yl - yr)), 0.35 * w)
    else:
        cx, cy, d = x + w / 2, y + 0.8 * h, 0.45 * w
    x0, x1, y0, y1 = int(cx - 0.8 * d), int(cx + 0.8 * d), int(cy - 0.55 * d), int(cy + 0.75 * d)
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H or x1 - x0 < 4 or y1 - y0 < 4:
        return None
    g = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (48, 40)).astype(np.float32)


def mouth_motion(path: str | Path, t0: float, t1: float, fps: float = SAMPLE_FPS) -> dict:
    """-> {"face": bool, "mouth_w": mouth width / frame width, "articulation": float, "frames": n}."""
    import numpy as np
    frames = read_frames(path, t0, t1, fps)
    crops, widths = [], []
    for fr in frames:
        lm = face_landmarks(fr)
        if lm is None:
            continue
        if lm["mouth"]:
            (xr, yr), (xl, yl) = lm["mouth"]
            widths.append(float(np.hypot(xl - xr, yl - yr)) / fr.shape[1])
        else:
            widths.append(0.4 * lm["box"][2] / fr.shape[1])
        c = _mouth_crop(fr, lm)
        if c is not None:
            crops.append((c - c.mean()) / (c.std() + 1.0))
    seen = len(crops) >= max(3, len(frames) // 3)
    art = float(np.std([-c[12:32, 14:34].mean() for c in crops])) if seen else 0.0
    return {"face": seen, "mouth_w": round(float(np.median(widths)), 4) if widths else 0.0,
            "articulation": round(art, 4), "frames": len(frames)}


def verdict(insert: dict, reference: dict | None = None) -> tuple[bool, str]:
    """-> (ok_over_speech, reason)."""
    if not insert["face"]:
        if reference is not None and not reference.get("face"):
            return True, "no face in the real take either"
        return False, "no face found"
    ref_ok = bool(reference and reference.get("face") and reference.get("mouth_w"))
    if ref_ok:
        rel = insert["mouth_w"] / reference["mouth_w"]
        if rel < MIN_MOUTH_REL:
            return False, f"pulled back too far to read the lips (mouth {rel:.2f}x the real take's)"
    elif insert["mouth_w"] < MIN_MOUTH_W:
        return False, f"mouth too small to read ({insert['mouth_w']:.3f} of the frame width)"
    if insert["articulation"] < FROZEN_ABS:
        return False, f"mouth frozen (articulation {insert['articulation']:.3f})"
    if ref_ok and reference["articulation"] > FROZEN_ABS:
        r = insert["articulation"] / reference["articulation"]
        if r < FROZEN_RATIO:
            return False, f"mouth far stiller than the real take ({r:.2f}x)"
    return True, "ok"


def check_insert(clip: str | Path, clip_t0: float, clip_t1: float, source: str | Path | None = None,
                 src_t0: float | None = None) -> dict:
    """Measure an insert (clip time) against the real take over the same source span."""
    ins = mouth_motion(clip, clip_t0, clip_t1)
    ref = mouth_motion(source, src_t0, src_t0 + (clip_t1 - clip_t0)) if source is not None and src_t0 is not None else None
    ok, why = verdict(ins, ref)
    return {"insert": ins, "reference": ref, "ok": ok, "frozen": not ok, "reason": why}


def available() -> bool:
    try:
        _cv2()
        return True
    except Exception:  # noqa: BLE001
        return False
