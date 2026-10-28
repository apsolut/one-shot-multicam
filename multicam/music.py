"""Music mode: beat-cut multicam for dance / lip-sync clips with no (reliable) speech.

* beats from librosa beat tracking on the real track
* face visibility per frame (OpenCV Haar frontal-face detector) -> "weak" sections where the
  real face is turned away or covered; inserts are placed there when possible
* lyrics are given by the user (`--lyric 8.0=Cause I work`), because Whisper hallucinates on
  music; inserts with lyric words can be word-aligned, everything else is onset/beat aligned.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .plan import ANGLE_NAMES, assign_angles, resolve_pins
from .transcribe import decode_audio
from .util import probe, qt, save_json


@dataclass
class MusicParams:
    n_inserts: int = 3
    min_insert: float = 1.0
    max_insert: float = 2.0
    target_insert: float = 1.5
    min_a: float = 2.0
    head_a: float = 1.5
    tail_a: float = 1.5
    chunk: float = 5.0
    lead_in: float = 1.0      # insert starts >= this far into its generated chunk (breath/onset zone)
    lyric_word_dur: float = 0.3
    face_fps: float = 8.0
    angles: list = field(default_factory=lambda: ["B", "C"])


def beat_times(video, sr: int = 22050) -> tuple[float, np.ndarray]:
    import librosa
    y = decode_audio(video, sr)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    return float(np.atleast_1d(tempo)[0]), np.asarray(beats, dtype=float)


def onset_env(path, sr: int = 22050, hop: int = 512, ss: float | None = None, t: float | None = None):
    import librosa
    import subprocess
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{ss:.3f}", "-t", f"{t:.3f}"] if ss is not None else []) + \
          ["-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"]
    y = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.int16).astype(np.float32) / 32768
    return librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop), hop / sr


def face_visibility(video, step_fps: float = 8.0) -> list[tuple[float, float]]:
    """[(t, score)] with score 1 = clear frontal face, 0 = none (turned away / occluded)."""
    try:
        import cv2
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except Exception as e:  # noqa: BLE001
        print(f"warn: OpenCV face detector unavailable ({e}); treating all frames as visible")
        return []
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / step_fps)))
    out, i = [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if i % step == 0:
            h, w = f.shape[:2]
            s = 640.0 / max(h, w)
            g = cv2.cvtColor(cv2.resize(f, (int(w * s), int(h * s))), cv2.COLOR_BGR2GRAY)
            faces = casc.detectMultiScale(g, 1.1, 5, minSize=(40, 40))
            out.append((i / fps, 1.0 if len(faces) else 0.0))
        i += 1
    return out


def parse_time(t: str) -> float:
    """'8', '8.5', '8,5', '0:08', '1:02.5' -> seconds. Raises ValueError on anything else."""
    t = t.strip().replace(",", ".")
    if not t:
        raise ValueError("empty time")
    parts = t.split(":")
    if len(parts) > 3:
        raise ValueError(f"bad time {t!r}")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    if secs < 0:
        raise ValueError(f"negative time {t!r}")
    return secs


def lyric_words(lyrics: list[str], word_dur: float, bad: list | None = None) -> list[dict]:
    """'8.0=Cause I work', '8.0-9.6=Cause I work' or '0:08-0:09.6=...' -> timed words (evenly spread).
    Lines that don't parse are skipped (and appended to `bad` when given) instead of failing the whole plan."""
    words = []
    pi = 0
    for item in lyrics or []:
        try:
            t, text = item.split("=", 1)
            toks = text.split()
            if not toks:
                raise ValueError("no words")
            if "-" in t.strip()[1:]:  # a range (a leading '-' would be a negative time)
                a, b = t.strip().split("-", 1)
                t0, t1 = parse_time(a), parse_time(b)
                if t1 <= t0:
                    raise ValueError("end before start")
            else:
                t0 = parse_time(t); t1 = t0 + word_dur * len(toks)
        except ValueError:
            if bad is not None:
                bad.append(item)
            continue
        for k, tok in enumerate(toks):
            s = t0 + (t1 - t0) * k / len(toks)
            words.append({"text": tok, "start": round(s, 3), "end": round(s + (t1 - t0) / len(toks), 3),
                          "source": "lyrics", "phrase": pi})
        pi += 1
    words.sort(key=lambda w: w["start"])
    for i, w in enumerate(words):
        w["i"] = i
    return words


def beat_grid(dur: float, period: float = 0.5) -> np.ndarray:
    """Even 120 BPM grid: the fallback when no beat can be tracked (silence, a quiet a cappella take, speech)."""
    return np.round(np.arange(period, max(period, dur), period), 3)


def min_duration(P: "MusicParams") -> float:
    """Shortest clip that fits one insert between the opening and closing real (CAM A) shots."""
    return P.head_a + P.min_insert + P.tail_a


def insert_candidates(beats, dur: float, P: "MusicParams", visibility) -> list[tuple]:
    """(score, start, end, weak_face) for every beat-to-beat span of an allowed insert length."""
    cands = []
    for i, b0 in enumerate(beats):
        if b0 < P.head_a:
            continue
        for b1 in beats[i + 1:]:
            d = b1 - b0
            if d < P.min_insert:
                continue
            if d > P.max_insert or b1 > dur - P.tail_a:
                break
            weak = 1.0 - visibility(b0, b1)
            cands.append((weak - 0.1 * abs(d - P.target_insert), float(b0), float(b1), weak))
    return cands


def build_music_plan(video, P: MusicParams, lyrics=None, pins=None, angle_names=None) -> tuple[dict, dict]:
    video = str(Path(video).resolve())
    info = probe(video)
    dur, fps = info["duration"], info.get("fps", 30.0)
    if "sample_rate" not in info:
        raise ValueError("this clip has no sound. Music mode cuts on the beat of the song in your clip: upload a "
                         "clip where the music (or your singing) is audible, or use Talk mode")
    tempo, beats = beat_times(video)
    vis = face_visibility(video, P.face_fps)
    vt = np.array([v[0] for v in vis]) if vis else np.array([])
    vs = np.array([v[1] for v in vis]) if vis else np.array([])
    bad_lyrics: list[str] = []
    words = lyric_words(lyrics, P.lyric_word_dur, bad=bad_lyrics)
    notes = []

    def visibility(s, e):
        if not len(vt):
            return 1.0
        m = (vt >= s) & (vt < e)
        return float(vs[m].mean()) if m.any() else 1.0

    beat_source = "tracked"
    cands = insert_candidates(beats, dur, P, visibility)
    if not cands and dur >= min_duration(P):
        # no usable beat (silence, a cappella, speech): never a silent no-op, cut on an even grid instead
        beats, beat_source, tempo = beat_grid(dur), "grid", 120.0
        cands = insert_candidates(beats, dur, P, visibility)
        notes.append("No clear beat found in the audio, so cuts are on an even 120 BPM grid. "
                     "A clip with the song audible gives beat-matched cuts.")
    if dur < min_duration(P):
        notes.append(f"The clip is {dur:.1f} s; Music needs at least {min_duration(P) + 1:.0f} s to fit an AI "
                     "insert between the opening and closing shots of your real take.")
    if bad_lyrics:
        notes.append(f"Skipped {len(bad_lyrics)} lyric line(s) I couldn't read (use 3.2-5.0=words or 0:03-0:05=words): "
                     + "; ".join(x[:40] for x in bad_lyrics[:3]))
    cands.sort(reverse=True)
    chosen = []
    for score, s, e, weak in cands:
        if len(chosen) >= P.n_inserts:
            break
        if all(e + P.min_a <= c[1] or s >= c[2] + P.min_a for c in chosen):
            chosen.append((score, s, e, weak))
    chosen.sort(key=lambda c: c[1])

    inserts, chunks = [], []
    for k, (score, s, e, weak) in enumerate(chosen):
        cid = f"m{k + 1:02d}"
        ws = min(max(0.0, s - P.lead_in), max(0.0, dur - P.chunk))
        we = min(dur, ws + P.chunk)
        # whole lyric phrases that are mostly inside the window (never cut a line in half)
        keep = set()
        for pi in {w["phrase"] for w in words}:
            pw = [w for w in words if w["phrase"] == pi]
            p0, p1 = pw[0]["start"], pw[-1]["end"]
            if min(p1, we) - max(p0, ws) >= 0.8 * (p1 - p0):
                keep.add(pi)
        cw = [w for w in words if w["phrase"] in keep]
        iw = [w["text"] for w in words if s <= (w["start"] + w["end"]) / 2 < e]
        chunks.append({"id": cid, "start": round(ws, 3), "end": round(we, 3), "words": " ".join(w["text"] for w in cw),
                       "lyrics": bool(cw), "w0": cw[0]["i"] if cw else None, "w1": cw[-1]["i"] if cw else None,
                       "angle": None, "generate": True})
        inserts.append({"chunk": cid, "start": qt(s, fps), "end": qt(e, fps), "words": " ".join(iw),
                        "weak_face": round(weak, 2), "enter": "beat", "exit": "beat"})
    pin_map = resolve_pins(pins, chunks)
    assign_angles(inserts, P.angles, pin_map)
    names = dict(ANGLE_NAMES, **(angle_names or {}))
    for ins, ch in zip(inserts, chunks):
        ch["angle"] = ins["angle"]
    shots, t = [], 0.0
    for ins in inserts:
        if ins["start"] > t:
            shots.append({"cam": "A", "start": t, "end": ins["start"]})
        shots.append({"cam": ins["angle"], "start": ins["start"], "end": ins["end"], "chunk": ins["chunk"],
                      "offset": 0.0, "words": ins["words"], "enter": "beat", "exit": "beat",
                      "weak_face": ins["weak_face"]})
        t = ins["end"]
    if t < qt(dur, fps):
        shots.append({"cam": "A", "start": t, "end": qt(dur, fps)})
    for k, s in enumerate(shots):
        s["i"] = k
        s["angle_name"] = names.get(s["cam"], s["cam"])
        s["duration"] = round(s["end"] - s["start"], 4)
        s.setdefault("words", "")
    plan = {"mode": "music", "source": video, "duration": dur, "fps": fps, "tempo": round(tempo, 2),
            "beats": [round(float(b), 3) for b in beats], "beat_source": beat_source,
            "note": " ".join(notes), "params": asdict(P), "pins": pin_map, "angle_names": names,
            "face_visibility": [[round(t, 3), v] for t, v in vis],
            "chunks": chunks, "shots": shots, "cuts": [s["start"] for s in shots[1:]]}
    transcript = {"source": video, "duration": dur, "fps": fps, "mode": "music", "model": "lyrics (user-supplied)",
                  "text": " ".join(w["text"] for w in words), "words": words, "sentences": [], "phrases": []}
    return plan, transcript


def onset_align(shot: dict, clip: dict, video: str, max_lag: float = 0.5, min_corr: float = 0.2) -> dict:
    """Offset-only alignment: slide the generated clip's onset envelope against the real one."""
    src0 = clip["src_start"]
    naive_in = shot["start"] - src0
    real, dt = onset_env(video, ss=src0, t=clip["info"]["duration"])
    gen, _ = onset_env(clip["path"])
    n = min(len(real), len(gen))
    i0, i1 = int(naive_in / dt), int((naive_in + shot["end"] - shot["start"]) / dt)
    i0p, i1p = max(0, i0 - 8), min(n, i1 + 8)  # a little context around the insert
    r = real[i0p:i1p]
    best = (0.0, -1.0)
    for lag in range(-int(max_lag / dt), int(max_lag / dt) + 1):
        a, b = i0p + lag, i1p + lag
        if a < 0 or b > len(gen):
            continue
        g = gen[a:b]
        if r.std() < 1e-6 or g.std() < 1e-6:
            continue
        c = float(np.corrcoef(r, g)[0, 1])
        if c > best[1]:
            best = (lag * dt, c)
    lag, corr = best
    if corr < min_corr:
        lag = 0.0
    clip_in = naive_in + lag
    dur = shot["end"] - shot["start"]
    if clip_in < 0 or clip_in + dur > clip["info"]["duration"] - 0.02:
        lag, clip_in = 0.0, naive_in
    return {"status": "ok", "method": "onsets", "naive_clip_in": round(naive_in, 3), "clip_in": round(clip_in, 3),
            "clip_out": round(clip_in + dur, 3), "offset": round(lag, 3), "speed": 1.0, "raw_speed": 1.0,
            "corr": round(corr, 3), "matched": 0, "exact": 0, "of": 0}


def music_plan(video, out_dir, P: MusicParams, lyrics=None, pins=None, angle_names=None):
    plan, tr = build_music_plan(video, P, lyrics, pins, angle_names)
    out_dir = Path(out_dir)
    save_json(plan, out_dir / "plan.json")
    save_json(tr, out_dir / "transcript.json")
    return plan, tr
