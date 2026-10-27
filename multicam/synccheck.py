"""`multicam sync-check`: side-by-side (real A | aligned insert) per insert, real audio, padded windows."""
from __future__ import annotations

from pathlib import Path

from .assemble import FONT_CANDIDATES, resolve_shots
from .util import need, run


def sync_check(plan: dict, clips: dict, out: str | Path, video=None, pad=0.5, fps=30.0, half_w=540, half_h=960):
    need("ffmpeg")
    video = str(Path(video or plan["source"]).resolve())
    font = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
    shots = [s for s in resolve_shots(plan, clips, fps) if s["src"] == "generated"]
    if not shots:
        raise SystemExit("no generated inserts to check")
    inputs, fparts, segs = [video], [], []
    for n, s in enumerate(shots):
        inputs.append(s["clip"])
        k = len(inputs) - 1
        t0 = max(0.0, s["start"] - pad)
        t1 = min(plan["duration"], s["end"] + pad)
        dur = t1 - t0
        nfr = int(round(dur * fps))
        sp = float(s.get("speed", 1.0))
        c0 = s["clip_start"] - sp * (s["start"] - t0)
        front = max(0.0, -c0)
        al = s.get("align") or {}
        cover = f"scale={half_w}:{half_h}:force_original_aspect_ratio=increase,crop={half_w}:{half_h},setsar=1"
        fparts.append(f"[0:v]trim=start={t0:.4f}:end={t1:.4f},setpts=PTS-STARTPTS,fps={fps:g},{cover},"
                      f"tpad=stop_mode=clone:stop={nfr},trim=end_frame={nfr}[L{n}]")
        fparts.append(f"[{k}:v]trim=start={max(0.0, c0):.4f},setpts=(PTS-STARTPTS)/{sp:.5f},fps={fps:g},{cover},"
                      f"tpad=start_mode=clone:start_duration={front / sp:.4f}:stop_mode=clone:stop={nfr},"
                      f"trim=end_frame={nfr}[R{n}]")
        txt = (f"shot {s['i']} CAM {s['cam']} {s['start']:.2f}-{s['end']:.2f}s  "
               f"offset {float(s.get('offset', 0)):+.2f}s  speed {sp:.2f}")
        draw = ""
        if font:
            draw = (f",drawtext=fontfile='{font}':text='{txt}':x=20:y=20:fontsize=30:fontcolor=white:"
                    f"box=1:boxcolor=black@0.6:boxborderw=10,"
                    f"drawtext=fontfile='{font}':text='INSERT':x=w-200:y=h-60:fontsize=34:fontcolor=yellow:"
                    f"enable='between(t,{s['start'] - t0:.3f},{s['end'] - t0:.3f})'")
        fparts.append(f"[L{n}][R{n}]hstack=inputs=2{draw},format=yuv420p[V{n}]")
        fparts.append(f"[0:a]atrim=start={t0:.4f}:end={t0 + nfr / fps:.4f},asetpts=PTS-STARTPTS[A{n}]")
        segs.append(f"[V{n}][A{n}]")
    fparts.append("".join(segs) + f"concat=n={len(segs)}:v=1:a=1[v][a]")
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(fparts), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)]
    run(cmd)
    return shots
