"""Side-by-side demo of one Wide insert: CrossView raw | app render | proposed fix (shake off + pull-back),
plus a per-frame horizontal-motion trace (motion.py). Used for the 2026-10-28 "Wide shakes" investigation.

Usage: make_demo.py RAW_CLIP.mp4 FINAL.mp4 SEG_START SEG_END [OUT_DIR]
  RAW_CLIP  the generated CrossView clip of the insert (time-locked to the source, clip_start == SEG_START)
  FINAL     the app's rendered edit that contains the insert
Writes OUT_DIR/_sbs.mp4 (3-up video) and OUT_DIR/wide_strip.jpg (frames + motion trace). Needs opencv-python."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from motion import analyze  # noqa: E402

if len(sys.argv) < 5:
    raise SystemExit(__doc__)
RAW, FINAL, seg0, seg1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
OUT = sys.argv[5] if len(sys.argv) > 5 else "."
W, H, FPS = 360, 640, 30
def frames(path, t0, t1, fps_out=FPS):
    cap = cv2.VideoCapture(path); fin = cap.get(cv2.CAP_PROP_FPS); allf = []
    while True:
        ok, f = cap.read()
        if not ok: break
        allf.append(f)
    n = int(round((t1 - t0) * fps_out))
    return [allf[min(len(allf) - 1, int(round((t0 + i / fps_out) * fin)))] for i in range(n)]
raw = frames(RAW, seg0, seg1)
app = frames(FINAL, seg0, seg1)
n = min(len(raw), len(app))
def pull(f, i, n, z0=1.15):  # proposed: deterministic smooth pull-back, sub-pixel, ease-out
    u = i / max(1, n - 1); e = 1 - (1 - u) ** 2; z = z0 + (1 - z0) * e
    h, w = f.shape[:2]; M = cv2.getRotationMatrix2D((w / 2, h / 2), 0, z)
    return cv2.warpAffine(f, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
def lab(img, t):
    cv2.rectangle(img, (0, 0), (W, 34), (0, 0, 0), -1)
    cv2.putText(img, t, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA); return img
vw = cv2.VideoWriter(os.path.join(OUT, '_sbs.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), FPS, (W * 3, H))
strip = []
for i in range(n):
    a = lab(cv2.resize(raw[i], (W, H)), 'CrossView output (raw)')
    b = lab(cv2.resize(app[i], (W, H)), 'App render (shake on)')
    c = lab(cv2.resize(pull(raw[i], i, n), (W, H)), 'Fix: shake off + pull-back')
    row = np.hstack([a, b, c]); vw.write(row)
    if i % 8 == 0: strip.append(row)
vw.release()
# motion trace panel: per-frame horizontal motion of the wide insert
_, o_raw, _ = analyze(RAW, seg0, seg1)
_, o_app, _ = analyze(FINAL, seg0 + 0.03, seg1)
P = np.full((300, W * 3, 3), 255, np.uint8)
cv2.putText(P, 'horizontal camera motion per frame (px, 1080-wide), same 1.5 s wide insert', (10, 24), 0, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
cv2.line(P, (40, 160), (W * 3 - 10, 160), (180, 180, 180), 1)
for o, col, name, y in [(o_raw, (0, 160, 0), 'CrossView raw (24 fps): ~0 px, locked off, no zoom', 270), (o_app, (0, 0, 220), 'App render: back-and-forth 0/2 px steps = synthetic shake', 292)]:
    t = np.array([r[0] for r in o]); d = np.array([r[1] for r in o]) * (1080 / (704 if o is o_raw else 1080))
    xs = (40 + (t - seg0) / (seg1 - seg0) * (W * 3 - 60)).astype(int); ys = (160 - d * 25).astype(int)
    cv2.polylines(P, [np.stack([xs, ys], 1)], False, col, 2, cv2.LINE_AA)
    cv2.putText(P, name, (40, y), 0, 0.55, col, 1, cv2.LINE_AA)
cv2.imwrite(os.path.join(OUT, 'wide_strip.jpg'), np.vstack([np.vstack(strip[:4]), P]), [cv2.IMWRITE_JPEG_QUALITY, 85])
print(n, 'frames')
