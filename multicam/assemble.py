"""`multicam assemble`: render the A/B/C cut over the untouched real audio track."""
from __future__ import annotations

import json
from pathlib import Path

from .util import need, probe, qt, run

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def load_clips(plan: dict, clips_json: str | None, clips_dir: str | None) -> dict:
    """Return {chunk_id: {"path", "angle", "src_start"}}.

    clips.json: {"c04": {"path": "x.mp4", "angle": "B", "src_start": 12.0}} or {"c04": "x.mp4"}.
    clips_dir:  files named <chunk>_<angle>.mp4 (as written by `prep`) or <chunk>.mp4.
    src_start = the source time that the clip's first frame corresponds to (default: chunk start).
    """
    chunks = {c["id"]: c for c in plan["chunks"]}
    clips: dict = {}
    if clips_dir:
        for c in plan["chunks"]:
            for name in (f"{c['id']}_{c.get('angle')}.mp4", f"{c['id']}.mp4"):
                p = Path(clips_dir) / name
                if p.exists():
                    clips[c["id"]] = {"path": str(p.resolve()), "angle": c.get("angle")}
                    break
    if clips_json:
        base = Path(clips_json).resolve().parent
        for cid, v in json.loads(Path(clips_json).read_text()).items():
            v = {"path": v} if isinstance(v, str) else dict(v)
            p = Path(v["path"])
            v["path"] = str(p if p.is_absolute() else (base / p).resolve())
            clips[cid] = v
    for cid, v in clips.items():
        if cid not in chunks:
            raise SystemExit(f"clip mapping references unknown chunk {cid}")
        v.setdefault("src_start", chunks[cid]["start"])
        v["info"] = probe(v["path"])
    return clips


TRIM_TOL = 0.3     # s: max trim of an insert whose clip falls just short of the shot
MIN_INSERT = 0.6   # s: never trim an insert below this


def resolve_shots(plan: dict, clips: dict, fps: float) -> list[dict]:
    """Attach clips to inserts; fall back to A when a clip is missing or doesn't cover the shot."""
    out = []
    carry = None  # start override for the shot after a trimmed insert
    for s in plan["shots"]:
        s = dict(s)
        if carry is not None:
            s["start"], carry = carry, None
        s["src"] = "real"
        if s["cam"] != "A":
            c = clips.get(s.get("chunk"))
            al = s.get("align") or {}
            if c and al.get("status") not in (None, "ok"):
                print(f"warn: shot {s['i']} ({s['cam']} {s['start']}-{s['end']}) align {al['status']}: "
                      f"{al.get('reason', '')}; using A")
            elif c and (c.get("angle") in (None, s["cam"])):
                speed = float(s.get("speed", 1.0))
                cs = s["start"] - c["src_start"] + float(s.get("offset", 0.0))
                ce = cs + speed * (s["end"] - s["start"])
                dur = c["info"]["duration"]
                # clip slightly short of the shot (e.g. the plan moved a few frames after the clip was made):
                # trim the insert by up to TRIM_TOL s and let the neighbouring real shot take the gap
                if -TRIM_TOL <= cs < 0 and s["end"] - (s["start"] - cs / speed) >= MIN_INSERT and out:
                    s["start"] = round(s["start"] - cs / speed, 4)
                    out[-1]["end"] = s["start"]
                    s["trimmed"] = True
                    cs = 0.0
                    ce = speed * (s["end"] - s["start"])
                over = ce - dur
                if 1e-3 < over <= TRIM_TOL and (s["end"] - over / speed) - s["start"] >= MIN_INSERT:
                    s["end"] = round(s["end"] - over / speed - 1e-3, 4)
                    ce = cs + speed * (s["end"] - s["start"])
                    carry = s["end"]
                    s["trimmed"] = True
                if cs >= 0 and ce <= dur + 1e-3:
                    s.update(src="generated", clip=c["path"], clip_start=round(cs, 4), clip_end=round(ce, 4),
                             speed=speed)
                else:
                    print(f"warn: shot {s['i']} ({s['cam']} {s['start']}-{s['end']}) outside clip range; using A")
            if s["src"] == "real":
                s["fallback_from"] = s["cam"]
                s["cam"] = "A"
        out.append(s)
    merged = []  # merge adjacent A shots (after fallbacks)
    for s in out:
        if merged and s["cam"] == "A" and merged[-1]["cam"] == "A":
            merged[-1]["end"] = s["end"]
        else:
            merged.append(s)
    for s in merged:
        s["frames"] = int(round(qt(s["end"], fps) * fps)) - int(round(qt(s["start"], fps) * fps))
    return merged


def assemble(plan: dict, out: str | Path, clips_json=None, clips_dir=None, video=None,
             labels=False, shake=False, shake_px=10.0, cut_lead_frames=0,
             width=None, height=None, fps=None, crf=18, align=True, transcript=None,
             align_cache=None, clips=None, max_long: int | None = None, preset: str = "medium") -> list[dict]:
    need("ffmpeg")
    video = str(Path(video or plan["source"]).resolve())
    vinfo = probe(video)
    width = width or vinfo["width"]
    height = height or vinfo["height"]
    if max_long and max(width, height) > max_long:  # e.g. 1280 on a small hosted machine
        k = max_long / max(width, height)
        width, height = int(width * k) // 2 * 2, int(height * k) // 2 * 2
    fps = fps or round(vinfo.get("fps", 30.0), 3)
    clips = clips or load_clips(plan, clips_json, clips_dir)
    if align and any(s["cam"] != "A" and "align" not in s for s in plan["shots"]):
        if transcript is None:
            raise SystemExit("auto-align needs the transcript (pass --transcript or use --no-align)")
        from .align import align_plan
        plan = json.loads(json.dumps(plan))
        align_plan(plan, transcript, clips, Path(align_cache or Path(out).resolve().parent / "align_cache"))
    if not align:
        plan = json.loads(json.dumps(plan))
        for s in plan["shots"]:
            if s.pop("align", None) is not None:
                s["offset"] = 0.0
            s["speed"] = 1.0
    total_frames = int(round(vinfo["duration"] * fps))
    shots = resolve_shots(plan, clips, fps)
    # audio-safe hard cuts: shift video cut points earlier by N frames; audio is never touched
    if cut_lead_frames:
        for a, b in zip(shots, shots[1:]):
            a["frames"] -= cut_lead_frames
            b["frames"] += cut_lead_frames
            if b["src"] == "generated":
                b["clip_start"] = max(0.0, b["clip_start"] - b.get("speed", 1.0) * cut_lead_frames / fps)
            elif b["cam"] == "A":
                b["start"] = b["start"] - cut_lead_frames / fps
    shots[-1]["frames"] += total_frames - sum(s["frames"] for s in shots)

    font = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
    inputs = [video]
    clip_idx = {}
    for s in shots:
        if s["src"] == "generated" and s["clip"] not in clip_idx:
            clip_idx[s["clip"]] = len(inputs)
            inputs.append(s["clip"])

    uses: dict[int, int] = {}
    for s in shots:
        k = 0 if s["src"] == "real" else clip_idx[s["clip"]]
        uses[k] = uses.get(k, 0) + 1
    f = []
    labels_in = {}
    for k, n in uses.items():
        f.append(f"[{k}:v]split={n}" + "".join(f"[s{k}_{j}]" for j in range(n)))
        labels_in[k] = [f"[s{k}_{j}]" for j in range(n)]
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    seg_labels = []
    for i, s in enumerate(shots):
        n = s["frames"]
        if s["src"] == "real":
            src = labels_in[0].pop(0)
            t0 = max(0.0, s["start"])
            # bound the trim early so scale/crop only see this shot's frames (+ a little slack)
            chain = f"{src}trim=start={t0:.4f}:end={t0 + (n + 3) / fps:.4f},setpts=PTS-STARTPTS,fps={fps:g},{cover}"
        else:
            src = labels_in[clip_idx[s["clip"]]].pop(0)
            sp = float(s.get("speed", 1.0))
            c0 = s["clip_start"]
            chain = f"{src}trim=start={c0:.4f}:end={c0 + sp * (n + 3) / fps:.4f},setpts=(PTS-STARTPTS)/{sp:.5f},fps={fps:g}"
            if shake:
                sw, sh = int(width * 1.04) // 2 * 2, int(height * 1.04) // 2 * 2
                a = shake_px
                chain += (f",scale={sw}:{sh}:force_original_aspect_ratio=increase,"
                          f"crop={width}:{height}:"
                          f"x='(iw-ow)/2+{a:.1f}*sin(2*PI*t*0.53)+{a*0.45:.1f}*sin(2*PI*t*1.71+1.3)':"
                          f"y='(ih-oh)/2+{a*0.8:.1f}*sin(2*PI*t*0.41+0.7)+{a*0.35:.1f}*sin(2*PI*t*2.07)'")
            else:
                chain += f",{cover}"
        chain += f",setsar=1,format=yuv420p,tpad=stop_mode=clone:stop={n},trim=end_frame={n},setpts=PTS-STARTPTS"
        if labels and font:
            name = s.get("label_name") or plan.get("angle_names", {}).get(s["cam"], "")
            tag = f"CAM {s['cam']}" + (f"  {name} (AI)" if s["src"] == "generated" else "  real")
            fs = max(24, int(height * 56 / 1920))
            chain += (f",drawtext=fontfile='{font}':text='{tag}':x={fs}:y={int(fs * 1.7)}:fontsize={fs}:fontcolor=white:"
                      f"box=1:boxcolor=black@0.55:boxborderw={fs // 3}")
        chain += f"[v{i}]"
        f.append(chain)
        seg_labels.append(f"[v{i}]")
    f.append("".join(seg_labels) + f"concat=n={len(seg_labels)}:v=1:a=0[vout]")

    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in inputs:
        cmd += ["-i", p]
    acodec = ["-c:a", "copy"] if vinfo.get("audio_codec") == "aac" else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-filter_complex", ";".join(f), "-map", "[vout]", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-r", f"{fps:g}", "-pix_fmt", "yuv420p",
            *acodec, "-movflags", "+faststart", str(out)]
    run(cmd)
    return shots
