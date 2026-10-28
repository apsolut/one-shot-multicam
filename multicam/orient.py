"""Image orientation guards: EXIF-normalise uploads, detect stills that came back rolled 90 degrees, and make
"use my original photo" stills. Pillow + OpenCV (both optional at import time; checks degrade to "unknown").

Why: phone photos store landscape pixels + an EXIF Orientation tag, and Qwen-Image-Edit sometimes returns a
still whose subject is rolled 90 degrees inside an upright canvas (seen with off-format Multiple-Angles LoRA
prompts like "Rotate the camera 45 degrees to the left"). Such a still must never be used automatically.
"""
from __future__ import annotations

from pathlib import Path

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp")
ROLLS = (0, 90, 270)  # 180 is not checked: the Haar profile cascade gives false 180 hits, Qwen does not flip


def exif_orientation(path: str | Path) -> int:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001
        return 1


def normalize_upload(path: str | Path) -> Path:
    """Bake the EXIF Orientation into the pixels (PIL ImageOps.exif_transpose), in place; returns the path.
    Re-encodes only when the tag says the image is rotated/mirrored. Other formats are converted to PNG/JPEG."""
    from PIL import Image, ImageOps
    path = Path(path)
    with Image.open(path) as im:
        im.load()
        o = int(im.getexif().get(0x0112, 1) or 1)
        fmt = (im.format or "").upper()
        if o == 1 and fmt in ("JPEG", "PNG", "WEBP"):
            return path
        up = ImageOps.exif_transpose(im)
    if up.mode not in ("RGB", "L"):
        up = up.convert("RGB")
    if fmt == "JPEG" or path.suffix.lower() in (".jpg", ".jpeg"):
        out = path.with_suffix(".jpg")
        up.save(out, "JPEG", quality=95)  # no exif written -> no stale Orientation tag
    else:
        out = path.with_suffix(".png")
        up.save(out, "PNG")
    if out != path:
        path.unlink(missing_ok=True)
    return out


def image_size(path: str | Path) -> tuple[int, int]:
    """Display size (EXIF orientation applied)."""
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        o = int(im.getexif().get(0x0112, 1) or 1)
    return (h, w) if o in (5, 6, 7, 8) else (w, h)


def is_portrait(wh: tuple[int, int]) -> bool:
    return wh[1] >= wh[0]


def _gray(path, max_side=640):
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        g = ImageOps.exif_transpose(im).convert("L")
    s = max_side / max(g.size)
    if s < 1:
        g = g.resize((max(1, int(g.width * s)), max(1, int(g.height * s))))
    return np.array(g), s


def _cascades():
    import cv2
    d = cv2.data.haarcascades
    return cv2.CascadeClassifier(d + "haarcascade_frontalface_default.xml"), cv2.CascadeClassifier(d + "haarcascade_profileface.xml")


def _faces(g):
    """Largest face box (x, y, w, h) from frontal + profile (both directions) cascades, or None."""
    import cv2
    fc, pc = _cascades()
    best = None
    for c, img, flip in ((fc, g, False), (pc, g, False), (pc, cv2.flip(g, 1), True)):
        for (x, y, w, h) in c.detectMultiScale(img, 1.1, 5, minSize=(40, 40)):
            if flip:
                x = g.shape[1] - x - w
            if best is None or w * h > best[2] * best[3]:
                best = (int(x), int(y), int(w), int(h))
    return best


def face_scores(path: str | Path) -> dict[int, int] | None:
    """{roll: face area} for the image turned by 0/90/270 degrees clockwise; None if OpenCV/Pillow is missing."""
    try:
        import cv2
        g, _ = _gray(path)
    except Exception:  # noqa: BLE001
        return None
    rot = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    out = {}
    for k in ROLLS:
        f = _faces(g if rot[k] is None else cv2.rotate(g, rot[k]))
        out[k] = f[2] * f[3] if f else 0
    return out


def face_roll(path: str | Path) -> int | None:
    """0 = upright face, 90/270 = the face is only found after turning the image (subject lies sideways),
    None = unknown (no detector, or no face at any of the three rolls)."""
    sc = face_scores(path)
    if not sc or not any(sc.values()):
        return None
    if sc[0] and sc[0] >= max(sc[90], sc[270]):
        return 0
    return max((90, 270), key=lambda k: sc[k])


def check_still(still: str | Path, source: str | Path, source_roll: int | None = None) -> tuple[bool, str]:
    """Is a generated still safe to use for a source? -> (ok, reason). Rejects an orientation (portrait vs
    landscape) mismatch, and a sideways subject when the source shows an upright face."""
    try:
        sw, ss = image_size(still), image_size(source)
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable ({e})"
    if is_portrait(sw) != is_portrait(ss):
        return False, f"{'portrait' if is_portrait(sw) else 'landscape'} still for a {'portrait' if is_portrait(ss) else 'landscape'} source"
    if source_roll is None:
        source_roll = face_roll(source)
    if source_roll == 0:
        r = face_roll(still)
        if r in (90, 270):
            return False, f"subject rotated {r} degrees"
        if r is None:
            return False, "no upright face found"
    return True, "ok"


def face_crop(src: str | Path, out: str | Path, aspect: float = 9 / 16, zoom: float = 1.0,
              min_w: int = 360, max_h: int = 1536) -> Path | None:
    """Write an upright crop of `src` with width/height = aspect, centred on the largest face.
    zoom=1: the widest crop that fits; zoom>1: a close-up whose height is `zoom` face heights.
    Returns None when the crop would be narrower than min_w pixels (too low-res for that framing)."""
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        up = ImageOps.exif_transpose(im).convert("RGB")
    W, H = up.size
    cx, cy, fh = W / 2, H / 2, None
    try:
        g, s = _gray(src)
        f = _faces(g)
        if f:
            x, y, w, h = (v / s for v in f)
            cx, cy, fh = x + w / 2, y + h / 2, h
    except Exception:  # noqa: BLE001
        pass
    cw, ch = (W, W / aspect) if W / H <= aspect else (H * aspect, H)  # widest crop that fits
    if zoom > 1:
        if fh is None:
            return None  # a close-up needs a face to aim at
        ch2 = min(ch, fh * zoom)
        cw, ch = ch2 * aspect, ch2
    if cw < min_w:
        return None
    # keep the face in the upper third for singles, centred for close-ups
    ty = cy - (ch * (0.38 if zoom <= 1 else 0.45))
    x0 = min(max(0.0, cx - cw / 2), W - cw)
    y0 = min(max(0.0, ty), H - ch)
    crop = up.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch)))
    if crop.height > max_h:  # LTX resizes to 1536 on the long side anyway: keep uploads small
        crop = crop.resize((int(crop.width * max_h / crop.height), max_h), Image.LANCZOS)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out)
    return out
