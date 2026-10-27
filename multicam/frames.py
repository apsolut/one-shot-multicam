"""Frame helpers: pick a clean, sharp, front-facing frame for the angle stills."""
from __future__ import annotations

from pathlib import Path

from .util import probe, run


def best_face_frame(video: str | Path, out: str | Path, t0: float | None = None, t1: float | None = None,
                    step_fps: float = 6.0) -> float:
    """Write the sharpest frame with a detected frontal face (and two eyes) to `out`; return its time."""
    video, out = str(video), Path(out)
    info = probe(video)
    t0 = 0.0 if t0 is None else t0
    t1 = info["duration"] if t1 is None else t1
    best_t, best_score = None, -1.0
    try:
        import cv2
        face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        eyes = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml")
        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / step_fps)))
        i = 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            t = i / fps
            i += 1
            if t < t0 or t > t1 or (i - 1) % step:
                continue
            h, w = f.shape[:2]
            s = 720.0 / max(h, w)
            g = cv2.cvtColor(cv2.resize(f, (int(w * s), int(h * s))), cv2.COLOR_BGR2GRAY)
            fs = face.detectMultiScale(g, 1.1, 6, minSize=(60, 60))
            if not len(fs):
                continue
            x, y, fw, fh = max(fs, key=lambda r: r[2] * r[3])
            roi = g[y:y + fh, x:x + fw]
            ne = len(eyes.detectMultiScale(roi[: fh * 2 // 3], 1.1, 5))
            sharp = cv2.Laplacian(roi, cv2.CV_64F).var()
            score = sharp * (1.0 if ne >= 2 else 0.3) * (fw * fh) ** 0.5
            if score > best_score:
                best_t, best_score = t, score
    except Exception as e:  # noqa: BLE001
        print(f"warn: face search unavailable ({e}); using a fixed frame")
    if best_t is None:
        best_t = t0 + 0.25 * (t1 - t0)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{best_t:.3f}", "-i", video, "-frames:v", "1", str(out)])
    return round(best_t, 3)


def poster(video: str | Path, out: str | Path, t: float = 1.0, width: int = 720) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(out)])
    return Path(out)
