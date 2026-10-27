"""multicam CLI."""
from __future__ import annotations

import argparse
import os
import json
from pathlib import Path

from . import __version__
from .util import load_json, save_json

DEMO_SCRIPT = ("I used to lose hours debugging and rewriting code. Then I tried Claude Code. "
               "My workflow transformed overnight. Now I ship features three times faster, with better "
               "quality than ever. If you're still coding the old way, you're leaving a lot of productivity behind.")


def _plan_params(a):
    from .plan import PlanParams
    P = PlanParams()
    for k in ("max_chunk", "lead", "breath", "min_insert", "max_insert", "min_a", "head_a", "tail_a"):
        v = getattr(a, k, None)
        if v is not None:
            setattr(P, k, v)
    if getattr(a, "angles", None):
        P.angles = [x.strip().upper() for x in a.angles.split(",")]
    P.all_chunks = bool(getattr(a, "all_chunks", False))
    return P


def cmd_analyze(a):
    from .transcribe import analyze
    script = Path(a.script_file).read_text() if a.script_file else a.script
    out = Path(a.workdir) / a.out
    doc = analyze(a.video, out, model=a.model, script=script, initial_prompt=a.initial_prompt, language=a.language)
    print(f"wrote {out}: {len(doc['words'])} words, {len(doc['sentences'])} sentences, {len(doc['phrases'])} phrases")
    for s in doc["sentences"]:
        print(f"  [{s['start']:6.2f}-{s['end']:6.2f}] {s['text']}")


def cmd_plan(a):
    if a.music:
        return cmd_plan_music(a)
    from .plan import plan
    tr = load_json(Path(a.workdir) / a.transcript)
    out = Path(a.workdir) / a.out
    doc = plan(tr, out, _plan_params(a), pins=a.pin)
    print(f"wrote {out}")
    print_plan(doc)


def cmd_plan_music(a):
    from .music import MusicParams, music_plan
    if not a.video:
        raise SystemExit("--music needs --video")
    P = MusicParams()
    if a.inserts:
        P.n_inserts = a.inserts
    for k in ("min_insert", "max_insert", "min_a", "head_a", "tail_a"):
        v = getattr(a, k, None)
        if v is not None:
            setattr(P, k, v)
    if a.angles:
        P.angles = [x.strip().upper() for x in a.angles.split(",")]
    names = dict(x.split("=", 1) for x in (a.angle_name or []))
    names = {k.upper(): v for k, v in names.items()}
    plan, tr = music_plan(a.video, Path(a.workdir), P, lyrics=a.lyric, pins=a.pin, angle_names=names)
    if a.visual_extra:
        plan["visual_extra"] = a.visual_extra
        save_json(plan, Path(a.workdir) / "plan.json")
    print(f"music plan: tempo {plan['tempo']} BPM, {len(plan['beats'])} beats -> {Path(a.workdir) / 'plan.json'}")
    print_plan(plan)


def print_plan(doc):
    print("chunks:")
    for c in doc["chunks"]:
        flag = f"-> gen {c['angle']}" if c["generate"] else ""
        print(f"  {c['id']} [{c['start']:6.2f}-{c['end']:6.2f}] {flag:9s} {c['words'] or '(no lyrics)'}")
    print("shots:")
    for s in doc["shots"]:
        extra = f" ({s['chunk']}, {s['enter']} -> {s['exit']})" if s["cam"] != "A" else ""
        if "weak_face" in s:
            extra += f" face-hidden {s['weak_face']:.0%}"
        print(f"  {s['i']:2d} CAM {s['cam']} [{s['start']:6.3f}-{s['end']:6.3f}] {s['duration']:5.2f}s{extra}  \"{s['words']}\"")


def cmd_prep(a):
    from .prep import prep
    plan = load_json(Path(a.workdir) / a.plan)
    m = prep(plan, Path(a.workdir) / a.out, video=a.video)
    print(f"wrote {len(m['jobs'])} jobs -> {Path(a.workdir) / a.out / 'jobs.json'}")


def cmd_assemble(a):
    from .assemble import assemble
    plan = load_json(Path(a.workdir) / a.plan)
    tr_path = Path(a.workdir) / a.transcript
    tr = load_json(tr_path) if tr_path.exists() else None
    shots = assemble(plan, a.out, clips_json=a.clips, clips_dir=a.clips_dir, video=a.video, labels=a.labels,
                     shake=a.shake, shake_px=a.shake_px, cut_lead_frames=a.cut_lead_frames,
                     align=not a.no_align, transcript=tr, align_cache=Path(a.workdir) / "align_cache",
                     max_long=a.max_long, preset=a.preset)
    for s in shots:
        print_shot(s)
    print(f"wrote {a.out}")


def print_shot(s):
    src = (f"{Path(s['clip']).name} @{s['clip_start']:.2f}s x{s.get('speed', 1.0):.3f}"
           if s["src"] == "generated" else "selfie")
    fb = f" (fallback from {s['fallback_from']})" if s.get("fallback_from") else ""
    print(f"  CAM {s['cam']} {s['start']:6.3f}-{s['end']:6.3f} {s['frames']:4d}f  {src}{fb}")


def print_align(report):
    for r in report:
        line = (f"  shot {r['shot']} {r['chunk']} CAM {r['cam']} {r['clip']}: {r['status']}  "
                f"words {r['exact']}/{r['of']} exact  naive_in {r['naive_clip_in']:.2f}s")
        if "clip_in" in r:
            line += (f" -> clip_in {r['clip_in']:.2f}s (offset {r['offset']:+.2f}s), speed {r['speed']:.3f}"
                     f" (raw {r['raw_speed']:.3f})")
        if r.get("reason"):
            line += f"  [{r['reason']}]"
        print(line)


def cmd_align(a):
    from .align import align_plan, save_clips_json
    from .assemble import load_clips
    plan_path = Path(a.workdir) / a.plan
    plan = load_json(plan_path)
    tr = load_json(Path(a.workdir) / a.transcript)
    clips = load_clips(plan, a.clips, a.clips_dir)
    report = align_plan(plan, tr, clips, Path(a.workdir) / "align_cache", model=a.model)
    out = Path(a.workdir) / (a.out or a.plan)
    save_json(plan, out)
    save_clips_json({cid: {**c, "align": next((r for r in report if r["chunk"] == cid), None)} for cid, c in clips.items()},
                    Path(a.workdir) / "clips_aligned.json")
    print_align(report)
    print(f"wrote {out} and {Path(a.workdir) / 'clips_aligned.json'}")


def cmd_sync_check(a):
    from .assemble import load_clips
    from .synccheck import sync_check
    plan = load_json(Path(a.workdir) / a.plan)
    clips = load_clips(plan, a.clips, a.clips_dir)
    if any(s["cam"] != "A" and "align" not in s for s in plan["shots"]) and not a.no_align:
        from .align import align_plan
        align_plan(plan, load_json(Path(a.workdir) / a.transcript), clips, Path(a.workdir) / "align_cache")
    sync_check(plan, clips, a.out, video=a.video, pad=a.pad)
    print(f"wrote {a.out}")


def cmd_demo(a):
    from .assemble import assemble
    from .plan import plan
    from .prep import prep
    from .transcribe import analyze
    root = Path(a.root).resolve()
    work = root / "work"
    video = root / "selfie.mp4"
    print("== analyze")
    tr = analyze(video, work / "transcript.json", model=a.model, script=None if a.no_script else DEMO_SCRIPT)
    for s in tr["sentences"]:
        print(f"  [{s['start']:6.2f}-{s['end']:6.2f}] {s['text']}")
    print("== plan (pin the 12-17 s chunk to B = profile, where the real generated clip lives)")
    p = plan(tr, work / "plan.json", _plan_params(a), pins=["@14.5=B"])
    print_plan(p)
    print("== prep")
    m = prep(p, work / "gen", video=video)
    print(f"  {len(m['jobs'])} jobs -> {work / 'gen' / 'jobs.json'}")
    ch = next(c for c in p["chunks"] if c["start"] <= 14.5 < c["end"])
    clips = {ch["id"]: {"path": str(root / "clips" / "run7_idlora_profile.mp4"), "angle": "B", "src_start": 12.0}}
    save_json(clips, work / "clips.json")
    print(f"== assemble ({ch['id']} <- run7_idlora_profile.mp4, src_start=12.0)")
    from .align import align_plan
    from .assemble import load_clips
    gen_dir = work / "gen" / "clips"
    clips = load_clips(p, str(work / "clips.json"), str(gen_dir) if gen_dir.exists() else None)
    print("== align")
    print_align(align_plan(p, tr, clips, work / "align_cache"))
    save_json(p, work / "plan.json")
    name = a.name
    for out, labels in ((root / f"{name}.mp4", False), (root / f"{name}_labeled.mp4", True)):
        shots = assemble(p, out, clips=clips, video=video, labels=labels,
                         shake=not a.no_shake, cut_lead_frames=a.cut_lead_frames, align=not a.no_align)
        print(f"  wrote {out}")
    for s in shots:
        print_shot(s)


def cmd_dub(a):
    """Dub mode: translate (Gemma 3 12B), review, then LipDub each sentence window -> dub_<lang>.mp4."""
    from .comfy import load_bindings
    from .dub import LANGS, fit_report, plan_windows, run_dub_job, stitch, translate
    from .server import workflows_dir
    from .transcribe import analyze
    from .util import probe
    work = Path(a.workdir)
    work.mkdir(parents=True, exist_ok=True)
    video = Path(a.video)
    if a.lang not in LANGS:
        raise SystemExit(f"--lang: pick one of {', '.join(LANGS)}")
    tr_path = work / "transcript.json"
    tr = load_json(tr_path) if tr_path.exists() else analyze(video, tr_path)
    dp_path = work / f"dub_{a.lang}.json"
    dp = load_json(dp_path) if dp_path.exists() else {}
    wins = dp.get("windows") or plan_windows(tr, probe(video)["duration"], a.max_window)
    if not all(w.get("translation") for w in wins):
        if a.dry_run:
            print("(dry run: would translate with Gemma 3 12B on Comfy Cloud)")
        else:
            for w, t in zip(wins, translate([w["text"] for w in wins], a.lang)):
                w.setdefault("translation", t)
    for w in wins:
        if w.get("translation"):
            w["fit"] = fit_report(w["text"], w["translation"])
    dp.update({"lang": a.lang, "source": str(video.resolve()), "windows": wins})
    save_json(dp, dp_path)
    for w in wins:
        warn = "" if (w.get("fit") or {}).get("ok", True) else f"  ⚠ {w['fit']['hint']} (x{w['fit']['ratio']})"
        print(f"{w['id']} {w['start']:6.2f}-{w['end']:6.2f}s  EN: {w['text']}\n{'':20}{a.lang.upper()}: "
              f"{w.get('translation', '?')}{warn}")
    if a.translate_only or a.dry_run:
        print(f"review/edit the translations in {dp_path}, then run again without --translate-only")
        return
    wfd = workflows_dir()
    wf = json.loads((wfd / "dub_api.json").read_text())
    b = load_bindings(wfd / "dub_bindings.json")
    out = work / f"dub_{a.lang}"
    out.mkdir(exist_ok=True)
    clips = dp.setdefault("clips", {})
    for w in wins:
        if (a.only and w["id"] not in a.only) or (w["id"] in clips and not a.only):
            continue
        rec = run_dub_job(video, w, w["translation"], a.lang, out / f"{w['id']}.mp4", wf, b, seed=a.seed,
                          speaker=a.speaker, on_status=lambda r: print(f"  {r['window']} {r['status']}", flush=True))
        if rec.get("output"):
            clips[w["id"]] = str(Path(rec["output"]).relative_to(work))
        print(f"{w['id']}: {rec['status']} {rec.get('elapsed_s')}s {rec.get('error') or ''}")
        save_json(dp, dp_path)
    final = work / f"dub_{a.lang}.mp4"
    stitch(video, wins, {k: str(work / v) for k, v in clips.items()}, final)
    print(f"wrote {final}  (now run the multicam pipeline on it: analyze/plan/reshoot/assemble with --video {final})")


def cmd_reshoot(a):
    """Re-shoot engine: one CrossView job per insert chunk; writes <out>/clips.json for `assemble --clips`."""
    from .reshoot import ANGLES, DEFAULT_CAMS, angle_prompt, insert_ordinals, run_reshoot_job
    from .comfy import ComfyError, load_bindings
    from .server import workflows_dir
    work = Path(a.workdir)
    plan = load_json(work / a.plan)
    video = Path(a.video or plan["source"])
    cams = dict(DEFAULT_CAMS)
    for item in a.cam or []:
        k, v = item.split("=", 1)
        if v not in ANGLES:
            raise SystemExit(f"--cam {item}: unknown preset; pick one of {', '.join(ANGLES)}")
        cams[k.upper()] = v
    out = work / a.out
    out.mkdir(parents=True, exist_ok=True)
    clips_path = out / "clips.json"
    clips = load_json(clips_path) if clips_path.exists() else {}
    ords = insert_ordinals(plan)
    chunks = {c["id"]: c for c in plan["chunks"]}
    todo = [(cid, cam) for (cid, cam) in ords if not a.only or cid in a.only]
    wfd = workflows_dir()
    wf = json.loads((wfd / "crossview_api.json").read_text())
    b = load_bindings(wfd / "crossview_bindings.json")
    for n, (cid, cam) in enumerate(todo):
        if a.max_submissions is not None and n >= a.max_submissions:
            print("max submissions reached; stopping"); break
        preset = cams.get(cam, "right90")
        variant = ords[(cid, cam)] + a.variant_shift
        prompt, phrase = angle_prompt(preset, variant)
        ch = chunks[cid]
        dest = out / f"{cid}_{cam}.mp4"
        if a.dry_run:
            print(f"{cid} CAM {cam} [{ch['start']:.2f}s +5.04s] {ANGLES[preset][0]}: {prompt}")
            continue
        try:
            rec = run_reshoot_job(video, ch["start"], preset, variant, dest, wf, b, seed=a.seed,
                                  on_status=lambda r: print(f"  {cid}_{cam}: {r['status']}", flush=True))
        except ComfyError as e:
            raise SystemExit(str(e))
        print(f"{cid}_{cam}: {rec['status']} ({rec.get('elapsed_s')}s, '{phrase}') -> {rec.get('output') or rec.get('error')}")
        if rec["status"] != "success":
            print("stopping after a failed job (cost guard)"); break
        clips[cid] = {"path": dest.name, "angle": cam, "src_start": ch["start"], "synced": True,
                      "preset": preset, "camera": phrase, "seed": rec["seed"]}
        save_json(clips, clips_path)
    if not a.dry_run:
        print(f"wrote {clips_path}; next: multicam -C {a.workdir} assemble --clips {clips_path} --video {video}")


def cmd_comfy_submit(a):
    from .comfy import cmd_submit
    override = {}
    for item in a.image or []:
        k, v = item.split("=", 1)
        override[k] = str(Path(v).resolve())
    cmd_submit(a.jobs, a.workflow, a.bindings, only=a.only, dry_run=a.dry_run, wait_done=a.wait, seed=a.seed,
               image_override=override, max_submissions=a.max_submissions, log_path=a.log)


def _int(v):
    return int(v) if v not in (None, "") else None


def _float(v):
    return float(v) if v not in (None, "") else None


def cmd_serve(a):
    if a.public:  # sane defaults for a public URL; explicit flags/env win
        a.max_jobs = 15 if a.max_jobs is None else a.max_jobs
        a.ip_jobs_per_day = 6 if a.ip_jobs_per_day is None else a.ip_jobs_per_day
        a.ip_projects_per_hour = 4 if a.ip_projects_per_hour is None else a.ip_projects_per_hour
        a.max_upload_s = 30.0 if a.max_upload_s is None else a.max_upload_s
        a.max_upload_mb = 60.0 if a.max_upload_mb == 500.0 else a.max_upload_mb
        a.ttl_minutes = 60.0 if a.ttl_minutes is None else a.ttl_minutes
        a.max_jobs_per_run = min(a.max_jobs_per_run, 6)
    if a.hosted_key_env and a.hosted_access == "open" and a.max_jobs is None:
        raise SystemExit("hosted mode needs a job cap: pass --max-jobs N (or --public)")
    from .server import serve
    serve(host=a.host, port=a.port, data_dir=a.data_dir, hosted_key_env=a.hosted_key_env, max_jobs=a.max_jobs,
          max_jobs_per_run=a.max_jobs_per_run, repo_url=a.repo_url, whisper_model=a.model,
          max_upload_mb=a.max_upload_mb, max_upload_s=a.max_upload_s, ttl_minutes=a.ttl_minutes,
          ip_jobs_per_day=a.ip_jobs_per_day, ip_projects_per_hour=a.ip_projects_per_hour, proxy_hops=a.proxy_hops,
          hosted_floor_usd=a.hosted_floor_usd, engine=a.engine, public=a.public, hosted_access=a.hosted_access,
          judge_max_jobs=a.judge_max_jobs)


def _add_plan_args(p):
    for k, h in (("max-chunk", "max generation chunk length (s, default 5)"), ("lead", "pre-roll before first word (0.5)"),
                 ("breath", "unused head of each generated chunk (0.5)"), ("min-insert", "1.0"), ("max-insert", "2.0"),
                 ("min-a", "min A between inserts (1.5)"), ("head-a", "min A at start (1.0)"), ("tail-a", "min A at end (1.0)")):
        p.add_argument(f"--{k}", type=float, help=h)
    p.add_argument("--angles", help="insert angles to alternate, e.g. B,C or B,C,D (default B,C)")
    p.add_argument("--all-chunks", action="store_true", help="generate every chunk, not only those with inserts")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="multicam", description="Multicam from one phone shot")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-C", "--workdir", default=".", help="working directory for json/artifacts (default .)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze", help="transcribe with word timestamps -> transcript.json")
    p.add_argument("video"); p.add_argument("--model", default="small", help="faster-whisper model (tiny/base/small)")
    p.add_argument("--script", help="known exact script to align onto the timings")
    p.add_argument("--script-file"); p.add_argument("--initial-prompt"); p.add_argument("--out", default="transcript.json")
    p.add_argument("--language", default="en", help="spoken language (e.g. de for a Dub-mode take)")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("plan", help="generation chunks + A/B/C cut list -> plan.json")
    p.add_argument("--transcript", default="transcript.json"); p.add_argument("--out", default="plan.json")
    p.add_argument("--pin", action="append", help="force an angle: c04=B or @14.5=B (chunk at time)")
    p.add_argument("--music", action="store_true", help="beat mode: cut on beats, cover hidden-face sections")
    p.add_argument("--video", help="source video (music mode)")
    p.add_argument("--lyric", action="append", help="music mode: '8.0=Cause I work' or '8.0-9.6=...' (repeatable)")
    p.add_argument("--inserts", type=int, help="music mode: number of inserts (default 3)")
    p.add_argument("--angle-name", action="append", help="music mode: B=side, C=closeup ... (prompt template per cam)")
    p.add_argument("--visual-extra", help="music mode: appended to every [VISUAL] (e.g. outfit details)")
    _add_plan_args(p); p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("prep", help="audio slices, first frames, prompts, jobs.json")
    p.add_argument("--plan", default="plan.json"); p.add_argument("--video"); p.add_argument("--out", default="gen")
    p.set_defaults(fn=cmd_prep)

    p = sub.add_parser("assemble", help="render final.mp4 over the real audio")
    p.add_argument("--plan", default="plan.json"); p.add_argument("--video")
    p.add_argument("--clips", help="clips.json mapping chunk -> {path, angle, src_start}")
    p.add_argument("--clips-dir", help="folder with <chunk>_<angle>.mp4 files")
    p.add_argument("--out", default="final.mp4")
    p.add_argument("--labels", action="store_true", help="burn in CAM A/B/C labels")
    p.add_argument("--transcript", default="transcript.json", help="real-audio transcript (for auto-align)")
    p.add_argument("--no-align", action="store_true", help="disable lip-sync auto-align (naive chunk mapping)")
    p.add_argument("--shake", action="store_true", help="subtle synthetic handheld shake on inserts")
    p.add_argument("--shake-px", type=float, default=10.0)
    p.add_argument("--cut-lead-frames", type=int, default=0, help="video-only: cut N frames before the word (audio untouched)")
    p.add_argument("--max-long", type=int, help="cap the long side, e.g. 1280 for a fast 720p render")
    p.add_argument("--preset", default="medium", help="x264 preset (veryfast ≈4x faster than medium)")
    p.set_defaults(fn=cmd_assemble)

    p = sub.add_parser("align", help="lip-sync align generated inserts to the real words (writes offset/speed)")
    p.add_argument("--plan", default="plan.json"); p.add_argument("--transcript", default="transcript.json")
    p.add_argument("--clips"); p.add_argument("--clips-dir"); p.add_argument("--out", help="default: overwrite --plan")
    p.add_argument("--model", default="small")
    p.set_defaults(fn=cmd_align)

    p = sub.add_parser("sync-check", help="side-by-side real A | aligned insert per insert, real audio")
    p.add_argument("--plan", default="plan.json"); p.add_argument("--transcript", default="transcript.json")
    p.add_argument("--clips"); p.add_argument("--clips-dir"); p.add_argument("--video")
    p.add_argument("--pad", type=float, default=0.5); p.add_argument("--no-align", action="store_true")
    p.add_argument("--out", default="sync_check.mp4")
    p.set_defaults(fn=cmd_sync_check)

    p = sub.add_parser("demo", help="analyze+plan+prep on selfie.mp4, assemble with run7 profile clip")
    p.add_argument("--root", default="."); p.add_argument("--model", default="small")
    p.add_argument("--no-script", action="store_true", help="use raw ASR words instead of the known script")
    p.add_argument("--no-shake", action="store_true"); p.add_argument("--cut-lead-frames", type=int, default=0)
    p.add_argument("--no-align", action="store_true"); p.add_argument("--name", default="demo_v5", help="output basename")
    _add_plan_args(p); p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("dub", help="Dub mode: the same take in another language with your face + voice "
                                   "(Gemma 3 translation, LTX-2.3 LipDub IC-LoRA)")
    p.add_argument("--video", required=True)
    p.add_argument("--lang", default="de", help="de, es, fr, ru, hr (experimental), it, pt, en")
    p.add_argument("--translate-only", action="store_true", help="stop after translating, to review dub_<lang>.json")
    p.add_argument("--max-window", type=float, default=10.0, help="longest dub window in seconds")
    p.add_argument("--speaker", default="A man", help='how the prompt describes the speaker, e.g. "A woman"')
    p.add_argument("--only", nargs="*", help="re-dub only these windows (w01 ...)")
    p.add_argument("--seed", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_dub)

    p = sub.add_parser("reshoot", help="re-shoot each insert from a new virtual camera (LTX-2.3 CrossView IC-LoRA, "
                                       "follows your real motion + lips; needs $COMFY_API_KEY)")
    p.add_argument("--plan", default="plan.json"); p.add_argument("--video"); p.add_argument("--out", default="gen/reshoot")
    p.add_argument("--cam", action="append", help="camera preset per angle, e.g. B=right90 C=wide "
                   "(right90 left90 right45 left45 wide closeup lowangle aerial)")
    p.add_argument("--only", nargs="*", help="chunk ids"); p.add_argument("--seed", type=int)
    p.add_argument("--variant-shift", type=int, default=0, help="pick the next camera variant (like a re-roll)")
    p.add_argument("--max-submissions", type=int); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_reshoot)

    p = sub.add_parser("serve", help="run the local web app (FastAPI + UI) on http://127.0.0.1:8787")
    E = os.environ.get
    p.add_argument("--host", default=E("MULTICAM_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(E("PORT", E("MULTICAM_PORT", "8787"))))
    p.add_argument("--data-dir", default=E("MULTICAM_DATA_DIR", "~/.multicam/projects"))
    p.add_argument("--model", default=E("MULTICAM_WHISPER_MODEL", "small"), help="faster-whisper model")
    p.add_argument("--repo-url", default=E("MULTICAM_REPO_URL", "https://github.com/apsolut/one-shot-multicam"))
    g = p.add_argument_group("public hosted demo (all also settable via MULTICAM_* env vars)")
    g.add_argument("--hosted-key-env", default=E("MULTICAM_HOSTED_KEY_ENV"),
                   help="env var holding YOUR Comfy key, used when a visitor pastes none (enables hosted mode)")
    g.add_argument("--hosted-access", choices=["open", "judge"], default=E("MULTICAM_HOSTED_ACCESS") or "open",
                   help="open: visitors without a key use the hosted key (capped). judge: only with the private "
                        "judge code in env MULTICAM_JUDGE_CODE; everyone else brings their own key")
    g.add_argument("--judge-max-jobs", type=int, default=_int(E("MULTICAM_JUDGE_MAX_JOBS")) or 10,
                   help="total jobs the judge code may spend on the hosted key (default 10)")
    g.add_argument("--max-jobs", type=int, default=_int(E("MULTICAM_MAX_JOBS")), help="total job cap for the hosted key (persisted)")
    g.add_argument("--ip-jobs-per-day", type=int, default=_int(E("MULTICAM_IP_JOBS_PER_DAY")))
    g.add_argument("--ip-projects-per-hour", type=int, default=_int(E("MULTICAM_IP_PROJECTS_PER_HOUR")))
    g.add_argument("--max-upload-s", type=float, default=_float(E("MULTICAM_MAX_UPLOAD_S")), help="max video length")
    g.add_argument("--max-upload-mb", type=float, default=_float(E("MULTICAM_MAX_UPLOAD_MB")) or 500.0)
    g.add_argument("--ttl-minutes", type=float, default=_float(E("MULTICAM_TTL_MINUTES")), help="auto-delete projects")
    g.add_argument("--proxy-hops", type=int, default=_int(E("MULTICAM_PROXY_HOPS")) or 0,
                   help="trusted reverse proxies in front (for X-Forwarded-For)")
    g.add_argument("--hosted-floor-usd", type=float, default=_float(E("MULTICAM_HOSTED_FLOOR_USD")),
                   help="also stop when the hosted key's real Comfy balance would drop below this (restart-proof)")
    p.add_argument("--engine", choices=("reshoot", "idlora"), default=E("MULTICAM_ENGINE", "reshoot"),
                   help="default engine for new projects: reshoot (CrossView, follows your real motion) or idlora (classic)")
    g.add_argument("--public", action="store_true", default=E("MULTICAM_PUBLIC") == "1",
                   help="public-demo defaults: 15 jobs, 6 jobs/IP/day, 4 uploads/IP/hour, 30 s / 60 MB uploads, 60 min TTL")
    p.add_argument("--max-jobs-per-run", type=int, default=int(E("MULTICAM_MAX_JOBS_PER_RUN", "8")))
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("comfy", help="Comfy Cloud helpers")
    csub = p.add_subparsers(dest="comfy_cmd", required=True)
    q = csub.add_parser("submit", help="submit jobs.json entries to Comfy Cloud /api/prompt (needs $COMFY_API_KEY)")
    q.add_argument("--jobs", required=True); q.add_argument("--workflow", required=True, help="API-format workflow json")
    q.add_argument("--bindings", required=True, help='{"prompt":["node","input"],"image":[..],"audio":[..],"seed":[..]}')
    q.add_argument("--only", nargs="*"); q.add_argument("--dry-run", action="store_true")
    q.add_argument("--wait", action="store_true", help="poll and download the mp4 to expected_clip")
    q.add_argument("--seed", type=int)
    q.add_argument("--image", action="append", help="input still per angle or chunk, e.g. B=cam_profile.png or c01=x.png")
    q.add_argument("--max-submissions", type=int, help="hard cap on jobs submitted in this run")
    q.set_defaults(fn=cmd_comfy_submit)
    q.add_argument("--log", help="submissions log (default: next to jobs.json)")
    r = csub.add_parser("run", help="run any API-format workflow once and download all outputs (e.g. Qwen angles)")
    r.add_argument("--workflow", required=True); r.add_argument("--bindings", required=True)
    r.add_argument("--set", action="append", help="key=value; file paths are uploaded (e.g. image=frame.png)")
    r.add_argument("--out-dir", required=True); r.add_argument("--dry-run", action="store_true"); r.add_argument("--log")
    r.set_defaults(fn=lambda a: __import__("multicam.comfy", fromlist=["cmd_run"]).cmd_run(
        a.workflow, a.bindings, a.set, a.out_dir, dry_run=a.dry_run, log_path=a.log))

    from .scene_cli import add_parser as add_scene_parser
    add_scene_parser(sub)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
