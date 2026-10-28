"""Web endpoints for Scene mode ("talk to your other self"), mounted by `multicam serve` under /api/scene.

Kept apart from the talk/music project code: scene projects live in <data>/scenes/<id>/ (scene.json from
multicam.scene + web.json with status). Keys follow the same rules as the rest of the app (X-Comfy-Key per
request, or the operator key in hosted/judge mode with the same budget guards); they are never stored.
"""

import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote

from . import scene as S
from .util import load_json, save_json

MAX_LINES = 4


class _Shim:
    """Duck-typed Project for App.hosted_run: counts real Comfy submissions from scene.json."""

    def __init__(self, work: Path):
        self.work = work

    @property
    def state(self):
        try:
            return {"prompt_ids": [j["prompt_id"] for j in S.load(self.work)["jobs"] if j.get("prompt_id")]}
        except Exception:  # noqa: BLE001
            return {"prompt_ids": []}


def mount(api, A, key_or_403, charge_or_403):
    from fastapi import File, Form, Header, HTTPException, Request, UploadFile
    from fastapi.responses import FileResponse

    root = A.data / "scenes"
    root.mkdir(parents=True, exist_ok=True)
    locks: dict[str, threading.Lock] = {}

    def wpath(sid):
        if not sid.isalnum() or not (root / sid / "web.json").exists():
            raise HTTPException(404, "scene not found")
        return root / sid

    def web(sid) -> dict:
        return load_json(wpath(sid) / "web.json")

    def set_web(sid, **kw):
        d = root / sid
        with locks.setdefault(sid, threading.Lock()):
            w = load_json(d / "web.json")
            w.update(kw)
            save_json(w, d / "web.json")

    def log_fn(sid):
        def log(msg):
            w = web(sid)
            set_web(sid, log=(w.get("log", []) + [f"{time.strftime('%H:%M:%S')} {msg}"])[-40:])
        return log

    def url(sid, p):
        rel = Path(p).resolve().relative_to((root / sid).resolve())
        return f"/api/scene/{sid}/file/{quote(str(rel))}?t={int(Path(p).stat().st_mtime)}"

    def cleanup():
        if not A.ttl_minutes:
            return
        cutoff = time.time() - A.ttl_minutes * 60
        for d in root.iterdir():
            try:
                w = load_json(d / "web.json")
                ref = max(w.get("created", 0), (w.get("interrupted") or {}).get("t", 0))  # grace after a restart
                if ref < cutoff and not w.get("busy"):
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    def recover_orphans() -> list[str]:
        """Startup: no runner can be alive in a fresh process, so a busy scene is an orphan of a restart/deploy.
        Clear busy and say so (the buttons come back; finished clips are kept, so re-running skips them)."""
        out = []
        for d in sorted(root.iterdir()):
            try:
                w = load_json(d / "web.json")
                if w.get("busy"):
                    what = w["busy"]
                    w.update(busy=None, interrupted={"what": what, "t": time.time()},
                             error=f"interrupted by a server restart (an app update) during {what}. "
                                   "Finished clips are kept: run it again to continue")
                    w["log"] = (w.get("log", []) + [f"{time.strftime('%H:%M:%S')} interrupted by a server restart"])[-40:]
                    save_json(w, d / "web.json")
                    out.append(d.name)
                    print(f"[multicam] recovered orphaned scene {d.name}: was busy={what}", flush=True)
            except Exception:  # noqa: BLE001
                pass
        return out
    recover_orphans()

    def run_bg(sid, name, fn):
        if web(sid).get("busy"):
            raise HTTPException(409, f"busy: {web(sid)['busy']}")
        set_web(sid, busy=name, error=None)

        def go():
            try:
                fn()
            except BaseException as e:  # noqa: BLE001 - SystemExit from ffmpeg helpers too
                traceback.print_exc()
                set_web(sid, error=str(e)[:600])
            finally:
                set_web(sid, busy=None)
        A.pool.submit(go)

    def stills_stage(sc) -> bool:
        """Generate runs in two steps so the partner still is always chosen explicitly: 1) the stills job
        (1 Qwen job), then the user picks per framing, 2) A angles + B clips."""
        return not sc["stills"]

    def next_estimate(sc, w) -> dict:
        if stills_stage(sc):
            return {**S.estimate(sc, "stills", reshoot_a=False), "stage": "stills"}
        return {**S.estimate(sc, "all", reshoot_a=w.get("reshoot_a", True)), "stage": "clips"}

    def public(sid) -> dict:
        d = wpath(sid)
        w = web(sid)
        out = {"id": sid, "busy": w.get("busy"), "error": w.get("error"), "log": w.get("log", [])[-12:],
               "partner": w.get("partner"), "reshoot_a": w.get("reshoot_a", True)}
        if not (d / "scene.json").exists():
            return out
        sc = S.load(d)
        tr = load_json(sc["transcript"])
        out.update({
            "look": sc.get("look"), "person": sc.get("person"), "voice": sc.get("voice"), "a_look": sc.get("a_look"),
            "character": sc.get("character"),
            "sentences": [{"i": s["i"], "text": s["text"], "start": s["start"], "end": s["end"]} for s in tr["sentences"]],
            "lines": [{**ln, "shot": ln.get("shot") or S.b_shot_for(k), "clip": str(k) in sc["b_clips"]}
                      for k, ln in enumerate(sc["lines"])],
            "stills": {shot: [{"name": o["name"], "url": url(sid, o["path"]), "ok": o["ok"], "reason": o["reason"],
                               "original": o["original"], "chosen": o["name"] == (sc.get("picks") or {}).get(shot)}
                              for o in opts] for shot, opts in S.candidates(sc).items()},
            "picks": sc.get("picks") or {},
            "needs_pick": bool(sc["stills"]) and not sc.get("picked") and not sc["b_clips"],
            "estimate": next_estimate(sc, w),
            "a_angles": sorted({c["preset"] for c in sc["a_clips"].values()}),
            "jobs": [{k: j.get(k) for k in ("kind", "status", "elapsed_s")} for j in sc["jobs"]][-20:],
        })
        for name, lab in (("scene.mp4", "output"), ("scene_labeled.mp4", "output_labeled"), ("source.mp4", "source")):
            if (d / name).exists():
                out[lab] = url(sid, d / name)
        if sc.get("edl") and (d / "scene.mp4").exists():
            out["edl"] = [{k: s.get(k) for k in ("who", "cam", "t", "dur", "words")} for s in sc["edl"]]
        return out

    @api.post("/api/scene")
    def create(request: Request, file: UploadFile = File(...), consent: str = Form(""),
               partner: str = Form("clone"), look: str = Form("night"), person: str = Form("man"),
               voice: str = Form(""), a_look: str = Form(""), character: str = Form(""), script: str = Form(""),
               reshoot_a: str = Form("1"), photo: UploadFile | None = File(None), photo_consent: str = Form("")):
        yes = ("1", "true", "yes", "on")
        if consent.lower() not in yes:
            raise HTTPException(400, "please confirm this is your face or you have the person's permission")
        if partner not in S.PARTNER_MODES:
            raise HTTPException(400, f"partner must be one of {S.PARTNER_MODES}")
        if partner == "photo" and (photo is None or photo_consent.lower() not in yes):
            raise HTTPException(400, "a partner photo needs the box ticked: only photos of people who consented")
        if voice and voice not in S.VOICES and voice != "self":
            raise HTTPException(400, "unknown voice")
        try:
            A.check_project_quota(A.client_ip(request))
        except PermissionError as e:
            raise HTTPException(429, str(e))
        cleanup()
        from .server import UploadRejected, _limited_copy
        sid = secrets.token_hex(8)
        d = root / sid
        d.mkdir(parents=True)
        src = d / "source.mp4"
        try:
            _limited_copy(file.file, src, A.max_upload_mb)
            if A.max_upload_s:
                from .util import probe
                if probe(src)["duration"] > A.max_upload_s + 0.5:
                    raise UploadRejected(413, f"max {A.max_upload_s:.0f} s on this server")
            ph = None
            if partner == "photo":
                ph = d / f"partner{Path(photo.filename or 'x.png').suffix.lower() or '.png'}"
                _limited_copy(photo.file, ph, 20)
                from .orient import normalize_upload
                try:  # phone photos: sideways pixels + EXIF Orientation -> upright pixels, tag dropped
                    ph = normalize_upload(ph)
                except Exception as e:  # noqa: BLE001
                    raise UploadRejected(400, f"could not read the partner photo ({str(e)[:120]})")
        except UploadRejected as e:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(e.status, str(e))
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(400, f"upload failed: {str(e)[:200]}")
        A.check_project_quota(A.client_ip(request), record=True)
        save_json({"id": sid, "created": time.time(), "partner": partner, "reshoot_a": reshoot_a.lower() in yes,
                   "log": []}, d / "web.json")
        opts = dict(partner=partner, look=(look or "night")[:200], photo=str(ph) if ph else None,
                    character=character[:300] or None, voice=voice or None, person=(person or "man")[:20],
                    a_look=a_look[:200] or None, script=script[:4000] or None)

        def analyze():
            log_fn(sid)("transcribing your take (whisper, free)")
            with A.analyze_sem:
                S.init(src, d, model=A.whisper_model, **opts)
            log_fn(sid)("ready: write B's lines (or suggest), then Generate")
        run_bg(sid, "analyze", analyze)
        return public(sid)

    @api.get("/api/scene/{sid}")
    def get(sid: str):
        return public(sid)

    @api.post("/api/scene/{sid}/lines")
    async def lines(sid: str, request: Request):
        d = wpath(sid)
        body = await request.json()
        if web(sid).get("busy"):
            raise HTTPException(409, "busy")
        sc = S.load(d)
        n = len(load_json(sc["transcript"])["sentences"])
        if "lines" in body:
            new = []
            for ln in (body["lines"] or [])[:MAX_LINES]:
                text = str(ln.get("text", "")).strip()[:160]
                if not text:
                    continue
                item = {"after": max(0, min(int(ln.get("after", 0)), n - 1)), "text": text}
                if ln.get("shot") in S.B_SHOTS:
                    item["shot"] = ln["shot"]
                new.append(item)
            new.sort(key=lambda x: x["after"])
            old = {(o["text"], o.get("shot") or S.b_shot_for(k)): sc["b_clips"].get(str(k)) for k, o in enumerate(sc["lines"])}
            clips = {k: v for k, v in sc["b_clips"].items() if not k.isdigit()}
            for k, ln in enumerate(new):  # keep clips whose text + framing did not change
                c = old.get((ln["text"], ln.get("shot") or S.b_shot_for(k)))
                if c:
                    clips[str(k)] = c
            sc["lines"], sc["b_clips"] = new, clips
        for k in ("look", "person", "a_look", "character"):
            if k in body and isinstance(body[k], str) and body[k].strip() and body[k] != sc.get(k):
                sc[k] = body[k].strip()[:300]
                if k in ("look", "person", "a_look", "character"):
                    sc["stills"], sc["b_clips"] = {}, {}  # partner look changed: regenerate
                    sc["still_checks"], sc["picked"] = {}, False
                    sc["picks"] = S.default_picks(sc)  # keeps the original-photo defaults
        if body.get("posture") in S.POSTURES and body["posture"] != sc.get("posture"):  # partner mirrors A
            sc["posture"], sc["stills"], sc["b_clips"] = body["posture"], {}, {}
            sc["still_checks"], sc["picked"] = {}, False
            sc["picks"] = S.default_picks(sc)
        if body.get("voice") and body["voice"] != sc.get("voice") and (body["voice"] in S.VOICES or body["voice"] == "self"):
            sc["voice"] = body["voice"]
            sc["b_clips"] = {}
        if isinstance(body.get("edit"), dict):  # {tight, skip:[i], open:"react"|null}
            e = body["edit"]
            sc["edit"] = {"tight": bool(e.get("tight", True)), "open": "react" if e.get("open") == "react" else None,
                          "skip": [int(x) for x in (e.get("skip") or []) if 0 <= int(x) < n][:20]}
        if "reshoot_a" in body:
            set_web(sid, reshoot_a=bool(body["reshoot_a"]))
        S.save(d, sc)
        return public(sid)

    @api.post("/api/scene/{sid}/suggest")
    def suggest(sid: str):
        sc = S.load(wpath(sid))
        return {"lines": S.suggest_lines(load_json(sc["transcript"]), sc["partner"])}

    @api.post("/api/scene/{sid}/pick")
    async def pick(sid: str, request: Request):
        """{shot: single|ots|close, still: name from public().stills[shot]} -> that still is used for B's clips
        of this framing (clips made from another still are dropped and re-generated on the next Generate)."""
        d = wpath(sid)
        body = await request.json()
        if web(sid).get("busy"):
            raise HTTPException(409, "busy")
        sc = S.load(d)
        try:
            S.set_pick(sc, str(body.get("shot", "")), str(body.get("still", "")))
        except ValueError as e:
            raise HTTPException(400, str(e))
        sc["picked"] = True
        S.save(d, sc)
        return public(sid)

    @api.post("/api/scene/{sid}/generate")
    async def generate(sid: str, request: Request, x_comfy_key: str | None = Header(default=None),
                       x_judge_code: str | None = Header(default=None),
                       x_pay_mode: str | None = Header(default=None)):
        d = wpath(sid)
        body = await request.json() if (await request.body()) else {}
        sc = S.load(d)
        if not sc["lines"]:
            raise HTTPException(400, "write at least one line for B first")
        w = web(sid)
        if body.get("redo"):  # re-roll: drop B clips (and stills if asked) so they are generated again
            if body["redo"] == "stills":
                sc["stills"], sc["still_checks"], sc["picked"] = {}, {}, False
                sc["picks"] = S.default_picks(sc)
            sc["b_clips"] = {}
            S.save(d, sc)
        stage = "stills" if stills_stage(sc) else "clips"
        est = next_estimate(sc, w)
        if stage == "clips" and not sc.get("picked") and not sc["b_clips"]:
            sc["picked"] = True  # pressing Generate again under the picker = the shown picks are confirmed
            S.save(d, sc)
        if not body.get("confirm"):
            raise HTTPException(400, {"needs_confirm": True, **est})
        if est["n_jobs"] > max(A.max_jobs_per_run, 9):
            raise HTTPException(400, f"{est['n_jobs']} jobs exceeds the per-run cap")
        key, hosted = key_or_403(x_comfy_key, x_judge_code, x_pay_mode)
        log = log_fn(sid)

        def work():
            s = S.load(d)
            if stage == "stills":
                S.make_stills(s, d, key=key, log=log)
                log("stills ready: pick B's shot for each framing (your original photo is the default where it "
                    "works), then press Generate again")
                return
            if w.get("reshoot_a", True) and S.a_wants(s):
                S.make_a_clips(s, d, key=key, log=log)
            S.make_clips(s, d, key=key, log=log)
            log("rendering the scene (free)")
            S.render(S.load(d), d, labels=True, out=d / "scene_labeled.mp4", preset=A.render_preset)
            S.render(S.load(d), d, preset=A.render_preset)
            log("done")
        fn = charge_or_403(request, est["n_jobs"], hosted, _Shim(d), work) if est["n_jobs"] else work
        run_bg(sid, "generate", fn)
        return public(sid)

    @api.post("/api/scene/{sid}/render")
    async def render(sid: str, request: Request):
        d = wpath(sid)
        body = await request.json() if (await request.body()) else {}

        def go():
            S.render(S.load(d), d, labels=True, out=d / "scene_labeled.mp4", react=not body.get("no_react"),
                     preset=A.render_preset)
            S.render(S.load(d), d, react=not body.get("no_react"), preset=A.render_preset)
        run_bg(sid, "render", go)
        return public(sid)

    @api.get("/api/scene/{sid}/file/{path:path}")
    def files(sid: str, path: str):
        d = wpath(sid).resolve()
        f = (d / path).resolve()
        if d not in f.parents or not f.is_file() or f.suffix.lower() not in (".mp4", ".png", ".jpg", ".jpeg", ".webp"):
            raise HTTPException(404)
        return FileResponse(f)

    @api.get("/api/scene-config")
    def scene_config():
        return {"partners": list(S.PARTNER_MODES), "looks": {k: v[2] for k, v in S.LOOKS.items()},
                "voices": S.VOICES, "shots": list(S.B_SHOTS), "max_lines": MAX_LINES, "cost_usd": S.JOB_COST}
