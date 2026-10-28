"""Web endpoints for Dub mode, mounted by `multicam serve` under /api/dub.

Flow: upload a take (transcribed for free) -> Translate (one cheap Gemma job) -> review/edit the lines ->
Generate (LipDub per sentence window = dubbed CAM A in your voice, then CrossView re-shoots for B/C on the
dubbed take) -> a multicam edit in the new language. Dub projects live in <data>/dubs/<id>/. Keys follow the
same rules as the rest of the app (X-Comfy-Key per request, or the operator key in judge mode under the same
budget guards); they are never stored.
"""

import math
import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote

from . import dub as D
from .util import load_json, save_json

MAX_TEXT = 400


class _Shim:
    """Duck-typed Project for App.hosted_run: one entry per job unit that actually ran (refunds the rest)."""

    def __init__(self, d: Path, lang: str):
        self.d, self.lang = d, lang
        self.work = d  # project identity for free trial runs (App.project_key); workflows/ lives here

    @property
    def workflow_ids(self):
        try:
            return load_json(self.d / "dub.json").get("prompt_ids", [])
        except Exception:  # noqa: BLE001
            return []

    @property
    def state(self):
        n = 0
        try:
            dp = load_json(self.d / "dub.json")
            n += sum(math.ceil((w["end"] - w["start"]) / 5.0) for w in dp["windows"] if w["id"] in dp.get("clips", {}))
            n += int(dp.get("translated", 0))
            cj = self.d / "mc" / "gen" / "reshoot" / "clips.json"
            if cj.exists():
                n += len(load_json(cj))
        except Exception:  # noqa: BLE001
            pass
        return {"prompt_ids": list(range(n))}


def mount(api, A, key_or_403, charge_or_403):
    from fastapi import File, Form, Header, HTTPException, Request, UploadFile
    from fastapi.responses import FileResponse

    root = A.data / "dubs"
    root.mkdir(parents=True, exist_ok=True)
    locks: dict[str, threading.Lock] = {}

    def wpath(did):
        if not did.isalnum() or not (root / did / "web.json").exists():
            raise HTTPException(404, "dub not found")
        return root / did

    def web(did) -> dict:
        return load_json(wpath(did) / "web.json")

    def set_web(did, **kw):
        with locks.setdefault(did, threading.Lock()):
            w = load_json(root / did / "web.json")
            w.update(kw)
            save_json(w, root / did / "web.json")

    def log_fn(did):
        def log(msg):
            set_web(did, log=(web(did).get("log", []) + [f"{time.strftime('%H:%M:%S')} {msg}"])[-40:])
        return log

    def url(did, p):
        rel = Path(p).resolve().relative_to((root / did).resolve())
        return f"/api/dub/{did}/file/{quote(str(rel))}?t={int(Path(p).stat().st_mtime)}"

    def cleanup():
        if not A.ttl_minutes:
            return
        cutoff = time.time() - A.ttl_minutes * 60
        for d in root.iterdir():
            try:
                w = load_json(d / "web.json")
                if w.get("created", 0) < cutoff and not w.get("busy"):
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    def run_bg(did, name, fn):
        if web(did).get("busy"):
            raise HTTPException(409, f"busy: {web(did)['busy']} — wait for it to finish")
        set_web(did, busy=name, error=None)

        def go():
            try:
                fn()
            except BaseException as e:  # noqa: BLE001
                traceback.print_exc()
                set_web(did, error=str(e)[:600])
            finally:
                set_web(did, busy=None)
        A.pool.submit(go)

    def ready(did) -> Path:
        d = wpath(did)
        w = web(did)
        if w.get("busy"):
            raise HTTPException(409, f"busy: {w['busy']} — wait for it to finish")
        if not (d / "dub.json").exists():
            raise HTTPException(409, w.get("error") or "still transcribing your take — try again in a moment")
        return d

    def n_inserts(d: Path) -> int:
        from .plan import PlanParams, build_plan
        from .reshoot import insert_ordinals
        try:  # cut plan of the original take: the dubbed take keeps the same timing, so it is a close estimate
            return len(insert_ordinals(build_plan(load_json(d / "transcript.json"), PlanParams())))
        except Exception:  # noqa: BLE001
            return 4

    def public(did) -> dict:
        d = wpath(did)
        w = web(did)
        out = {"id": did, "busy": w.get("busy"), "error": w.get("error"), "log": w.get("log", [])[-12:]}
        if (d / "dub.json").exists():
            dp = load_json(d / "dub.json")
            out.update({
                "lang": dp["lang"], "lang_name": D.LANGS[dp["lang"]][0], "validated": D.LANGS[dp["lang"]][1],
                "translated": bool(dp.get("translated")),
                "windows": [{"id": x["id"], "start": x["start"], "end": x["end"], "text": x["text"],
                             "translation": x.get("translation", ""), "fit": x.get("fit"),
                             "dubbed": x["id"] in dp.get("clips", {})} for x in dp["windows"]],
                "estimate": D.estimate(dp["windows"], n_inserts(d)),
            })
        for rel, lab in (("mc/multicam.mp4", "output"), ("mc/multicam_labeled.mp4", "output_labeled"),
                         ("source.mp4", "source")):
            if (d / rel).exists():
                out[lab] = url(did, d / rel)
        if out.get("lang") and (d / f"dub_{out['lang']}.mp4").exists():
            out["dubbed_take"] = url(did, d / f"dub_{out['lang']}.mp4")
        return out

    @api.post("/api/dub")
    def create(request: Request, file: UploadFile = File(...), consent: str = Form(""), lang: str = Form("de"),
               script: str = Form("")):
        if consent.lower() not in ("1", "true", "yes", "on"):
            raise HTTPException(400, "please confirm this is your own face and voice")
        if lang not in D.LANGS or lang == "en":
            raise HTTPException(400, f"lang must be one of {', '.join(k for k in D.LANGS if k != 'en')}")
        try:
            A.check_project_quota(A.client_ip(request))
        except PermissionError as e:
            raise HTTPException(429, str(e))
        cleanup()
        from .server import UploadRejected, _limited_copy
        from .util import probe
        did = secrets.token_hex(8)
        d = root / did
        d.mkdir(parents=True)
        src = d / "source.mp4"
        try:
            _limited_copy(file.file, src, A.max_upload_mb)
            if A.max_upload_s and probe(src)["duration"] > A.max_upload_s + 0.5:
                raise UploadRejected(413, f"max {A.max_upload_s:.0f} s on this server")
        except UploadRejected as e:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(e.status, str(e))
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(400, f"upload failed: {str(e)[:200]}")
        A.check_project_quota(A.client_ip(request), record=True)
        save_json({"id": did, "created": time.time(), "log": []}, d / "web.json")

        def analyze():
            from .transcribe import analyze as tr_analyze
            log_fn(did)("transcribing your take (whisper, free)")
            with A.analyze_sem:
                tr = tr_analyze(src, d / "transcript.json", model=A.whisper_model, script=script[:4000] or None)
            wins = D.plan_windows(tr, probe(src)["duration"])
            save_json({"lang": lang, "source": str(src), "windows": wins}, d / "dub.json")
            log_fn(did)(f"{len(wins)} sentence windows; next: Translate (one cheap job) or type your own lines")
        run_bg(did, "analyze", analyze)
        return public(did)

    @api.get("/api/dub/{did}")
    def get(did: str):
        return public(did)

    @api.post("/api/dub/{did}/translate")
    def translate(did: str, request: Request, x_comfy_key: str | None = Header(default=None),
                  x_judge_code: str | None = Header(default=None),
                       x_pay_mode: str | None = Header(default=None)):
        d = ready(did)
        dp = load_json(d / "dub.json")
        key, hosted = key_or_403(x_comfy_key, x_judge_code, x_pay_mode)

        def work():
            log_fn(did)(f"translating to {D.LANGS[dp['lang']][0]} (Gemma 3 12B, open weights)")
            pids: list = []
            try:
                out = D.translate([w["text"] for w in dp["windows"]], dp["lang"], key=key, prompt_ids=pids)
            finally:
                if pids:
                    c0 = load_json(d / "dub.json")
                    c0["prompt_ids"] = c0.get("prompt_ids", []) + pids
                    save_json(c0, d / "dub.json")
            cur = load_json(d / "dub.json")
            for w, t in zip(cur["windows"], out):
                w["translation_mt"] = w["translation"] = t
                w["fit"] = D.fit_report(w["text"], t)
                cur.get("clips", {}).pop(w["id"], None)
            cur["translated"] = 1
            save_json(cur, d / "dub.json")
            log_fn(did)("translated: please read and fix the lines, then Generate")
        run_bg(did, "translate", charge_or_403(request, 1, hosted, _Shim(d, dp["lang"]), work))
        return public(did)

    @api.post("/api/dub/{did}/lines")
    async def lines(did: str, request: Request):
        d = ready(did)
        body = await request.json()
        dp = load_json(d / "dub.json")
        if body.get("lang") and body["lang"] != dp["lang"]:
            if body["lang"] not in D.LANGS or body["lang"] == "en":
                raise HTTPException(400, "unknown language")
            dp["lang"], dp["clips"], dp["translated"] = body["lang"], {}, 0
            for w in dp["windows"]:
                w.pop("translation", None); w.pop("fit", None)
        for w, t in zip(dp["windows"], body.get("translations") or []):
            t = str(t or "").strip()[:MAX_TEXT]
            if t and t != w.get("translation"):
                w["translation"] = t
                w["fit"] = D.fit_report(w["text"], t)
                dp.get("clips", {}).pop(w["id"], None)  # re-dub only the changed window
        save_json(dp, d / "dub.json")
        return public(did)

    @api.post("/api/dub/{did}/generate")
    async def generate(did: str, request: Request, x_comfy_key: str | None = Header(default=None),
                       x_judge_code: str | None = Header(default=None),
                       x_pay_mode: str | None = Header(default=None)):
        d = ready(did)
        body = await request.json() if (await request.body()) else {}
        dp = load_json(d / "dub.json")
        if not all(w.get("translation") for w in dp["windows"]):
            raise HTTPException(400, "translate (or type) every line first")
        est = D.estimate(dp["windows"], n_inserts(d))
        if not body.get("confirm"):
            raise HTTPException(400, {"needs_confirm": True, **est})
        if est["jobs"] > max(A.max_jobs_per_run, 12):
            raise HTTPException(400, f"{est['jobs']} jobs exceeds the per-run cap; use a shorter take")
        key, hosted = key_or_403(x_comfy_key, x_judge_code, x_pay_mode)
        log = log_fn(did)

        def work():
            cur = load_json(d / "dub.json")
            try:
                dubbed = D.make_dubbed_take(d / "source.mp4", d, cur, key=key, log=log)
            finally:
                save_json(cur, d / "dub.json")  # keeps clips + prompt_ids even if a window failed
            script = " ".join(w["translation"] for w in cur["windows"])
            pids = cur.setdefault("prompt_ids", [])
            try:
                D.multicam_from_dub(dubbed, d / "mc", cur["lang"], script, key=key, log=log, preset=A.render_preset,
                                    model=A.whisper_model, seams=[w["start"] for w in cur["windows"][1:]],
                                    prompt_ids=pids)
            finally:
                c2 = load_json(d / "dub.json")
                c2["prompt_ids"] = list(dict.fromkeys(c2.get("prompt_ids", []) + pids))
                save_json(c2, d / "dub.json")
            log("done")
        run_bg(did, "generate", charge_or_403(request, est["jobs"], hosted, _Shim(d, dp["lang"]), work))
        return public(did)

    @api.get("/api/dub/{did}/file/{path:path}")
    def files(did: str, path: str):
        d = wpath(did).resolve()
        f = (d / path).resolve()
        if d not in f.parents or not f.is_file() or f.suffix.lower() not in (".mp4", ".jpg", ".png"):
            raise HTTPException(404)
        return FileResponse(f)

    @api.get("/api/dub-config")
    def dub_config():
        return {"langs": {k: {"name": v[0], "validated": v[1]} for k, v in D.LANGS.items() if k != "en"},
                "usd_per_s": D.LIPDUB_USD_PER_S, "max_window_s": D.MAX_WINDOW_S}
