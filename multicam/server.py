"""`multicam serve`: local web app (FastAPI + static UI) wrapping the multicam pipeline.

Security model
* BYO key: the browser sends the user's Comfy Cloud API key in the `X-Comfy-Key` header, per request.
  The server keeps it only in the memory of the worker thread that needs it. It is never written
  to disk, never put in state/logs/responses, and never echoed back.
* Everything else (whisper, planning, alignment, ffmpeg rendering) runs locally on this machine.
* Optional hosted mode (`--hosted-key-env VAR --max-jobs N`) lets judges try it without a key,
  using the operator's key with a hard job cap.
"""

import copy
import json
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from . import __version__
from .util import load_json, probe, save_json

STEPS = ["transcribe", "plan", "angles", "cameras", "align", "render"]
STILL_TO_PROMPT = {"closeup": "closeup", "wide": "wide", "right45": "side", "left45": "side",
                   "right90": "profile", "left90": "profile", "lowangle": "lowangle", "aerial": "wide"}
# Qwen's 45/90-degree side views on a vertical frame often come back rolled 90 degrees, so default to the safe ones
DEFAULT_STILLS = {"talk": {"B": "closeup", "C": "wide"}, "music": {"B": "closeup", "C": "wide"}}
RESHOOT_CAMS = {"B": "right90", "C": "wide"}  # CrossView handles real side angles (no Qwen roll problem)
STILL_LABELS = {"closeup": "Close-up", "wide": "Wide", "right45": "45° right", "left45": "45° left",
                "right90": "Profile right", "left90": "Profile left", "lowangle": "Low angle", "aerial": "Aerial"}
BURN_NAMES = {"closeup": "close-up", "wide": "wide", "right45": "side", "left45": "side", "right90": "profile",
              "left90": "profile", "lowangle": "low angle", "aerial": "aerial"}  # burned-in label text
VISITOR_COOKIE = "mc_vid"  # random first-party id for the one-free-run-per-visitor rule
ENGINES = ("reshoot", "idlora")  # reshoot = CrossView IC-LoRA on the real take; idlora = classic Qwen still + ID LoRA
REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"


def workflows_dir() -> Path:
    import os
    return Path(os.environ.get("MULTICAM_WORKFLOWS", REPO_ROOT / "workflows"))


class Project:
    """All state of one project lives in <data>/<id>/state.json (+ work files). No secrets."""

    def __init__(self, root: Path):
        self.root = root
        self.work = root / "work"
        self.lock = threading.RLock()
        self.state = load_json(root / "state.json") if (root / "state.json").exists() else {}

    # ---------- persistence
    def save(self):
        with self.lock:
            save_json(self.state, self.root / "state.json")

    def set_step(self, step, status, msg=""):
        with self.lock:
            self.state["steps"][step] = {"status": status, "msg": msg, "t": time.time()}
            self.save()

    def set_job(self, rec: dict):
        with self.lock:
            pid_ = rec.get("prompt_id")
            if pid_ and pid_ not in self.state.setdefault("prompt_ids", []):
                self.state["prompt_ids"].append(pid_)  # every real Comfy submission, for budget accounting
            jobs = self.state.setdefault("jobs", [])
            for k, j in enumerate(jobs):
                if j["uid"] == rec["uid"]:
                    jobs[k] = {**j, **rec}
                    break
            else:
                jobs.append(rec)
            self.save()

    @property
    def id(self):
        return self.state["id"]

    def rel(self, p) -> str:
        return str(Path(p).resolve().relative_to(self.root.resolve()))

    def url(self, p) -> str:
        return f"/files/{self.id}/{quote(self.rel(p))}"

    # ---------- plan with user edits applied
    def base_plan(self) -> dict:
        return load_json(self.work / "plan_base.json")

    def edited_plan(self) -> dict:
        plan = copy.deepcopy(self.base_plan())
        for s in plan["shots"]:
            e = self.state.get("edits", {}).get(str(s["i"]))
            if e and s.get("chunk"):
                s["planned_cam"] = s["cam"]
                s["cam"] = e["cam"]
                if "offset" in e:
                    s["user_offset"] = float(e["offset"])
        return plan


class UploadRejected(ValueError):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


class App:
    def __init__(self, data_dir: Path, hosted_key_env: str | None = None, max_jobs: int | None = None,
                 max_jobs_per_run: int = 8, repo_url: str = "https://github.com/apsolut/one-shot-multicam",
                 whisper_model: str = "small", max_upload_mb: float = 500.0, max_upload_s: float | None = None,
                 ttl_minutes: float | None = None, ip_jobs_per_day: int | None = None,
                 ip_projects_per_hour: int | None = None, proxy_hops: int = 0, hosted_floor_usd: float | None = None,
                 engine: str = "reshoot", public: bool = False, hosted_access: str = "open",
                 judge_max_jobs: int = 10, trial_runs: int = 0, trial_jobs_per_run: int = 10):
        """Hosted (public) mode = hosted_key_env is set AND that env var holds a key. Its guards:
        max_jobs (global, persisted), ip_jobs_per_day / ip_projects_per_hour (per client IP, persisted),
        max_upload_s / max_upload_mb, ttl_minutes (projects auto-deleted), no project listing."""
        self.data = Path(data_dir).expanduser().resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.max_upload_mb, self.max_upload_s, self.ttl_minutes = max_upload_mb, max_upload_s, ttl_minutes
        self.ip_jobs_per_day, self.ip_projects_per_hour, self.proxy_hops = ip_jobs_per_day, ip_projects_per_hour, proxy_hops
        self.budget_lock = threading.Lock()
        self.hosted_floor_usd = hosted_floor_usd  # restart-proof stop: refuse when the real balance nears this
        self.hosted_key_env = hosted_key_env
        # "open": anyone may use the operator key (capped). "judge": only with the private judge code
        # (env MULTICAM_JUDGE_CODE); everyone else brings their own key. No code configured = BYO key only.
        self.hosted_access = hosted_access if hosted_access in ("open", "judge") else "judge"
        self.judge_max_jobs = judge_max_jobs
        # free trial runs on the operator key: a global pool (MULTICAM_HOSTED_RUNS, raise it to top up; the
        # used count lives in hosted_jobs.json on the data volume), one run per visitor (hashed IP + cookie),
        # one run = one project in any mode, capped at trial_jobs_per_run Comfy jobs. Balance floor still applies.
        self.trial_runs = max(0, int(trial_runs or 0))
        self.trial_jobs_per_run = max(1, int(trial_jobs_per_run or 10))
        self.public_site = bool(public or hosted_key_env)  # public-site guards (no listing, TTL, rate limits)
        self.engine = engine if engine in ENGINES else "reshoot"  # default for new projects
        import os as _os
        # small shared-CPU hosts: render at <=1280 px with a fast x264 preset (≈4x less CPU than 1080p/medium)
        rm = _os.environ.get("MULTICAM_RENDER_MAX", "1280" if self.public_site else "")
        self.render_max_long = int(rm) if rm.strip().isdigit() and int(rm) > 0 else None
        self.render_preset = _os.environ.get("MULTICAM_RENDER_PRESET", "veryfast" if self.public_site else "medium")
        self.max_jobs = max_jobs
        self.max_jobs_per_run = max_jobs_per_run
        self.repo_url = repo_url
        self.whisper_model = whisper_model
        self.projects: dict[str, Project] = {}
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.counter_file = self.data / "hosted_jobs.json"
        self.analyze_sem = threading.Semaphore(1 if self.public_site else 2)  # whisper is CPU-heavy
        if self.ttl_minutes:
            threading.Thread(target=self._janitor, daemon=True).start()

    @property
    def operator_key(self) -> str | None:
        import os
        return (os.environ.get(self.hosted_key_env) or None) if self.hosted_key_env else None

    @property
    def hosted(self) -> bool:
        """Open hosted mode: any visitor without a key uses the operator key (capped)."""
        return bool(self.operator_key) and self.hosted_access == "open"

    @property
    def judge_enabled(self) -> bool:
        import os
        return bool(self.operator_key) and self.hosted_access == "judge" and len(os.environ.get("MULTICAM_JUDGE_CODE", "")) >= 8

    @property
    def trial_enabled(self) -> bool:
        return bool(self.operator_key) and self.trial_runs > 0

    @staticmethod
    def _cookie_id(cookie: str | None) -> str | None:
        import hashlib
        return hashlib.sha256(("multicam-vid:" + cookie).encode()).hexdigest()[:16] if cookie else None

    @staticmethod
    def project_key(p) -> str:
        import hashlib
        return hashlib.sha256(str(getattr(p, "work", p)).encode()).hexdigest()[:16]

    def _trial(self, d: dict) -> dict:
        t = d.setdefault("trial", {})
        for k, v in (("used", 0), ("ips", {}), ("cookies", {}), ("jobs", {})):
            t.setdefault(k, v)
        return t

    def trial_status(self, ip: str, cookie: str | None) -> dict | None:
        if not self.trial_enabled:
            return None
        t = self._trial(self._ledger())
        mine = t["ips"].get(self._ip_id(ip)) or t["cookies"].get(self._cookie_id(cookie) or "-")
        return {"runs_total": self.trial_runs, "runs_left": max(0, self.trial_runs - int(t["used"])),
                "used_by_you": bool(mine), "jobs_per_run": self.trial_jobs_per_run}

    def _floor_ok(self, n: int) -> bool:
        if self.hosted_floor_usd is None:
            return True
        import os
        from .comfy import COST_PER_JOB_USD, balance_usd
        try:
            bal = balance_usd(os.environ[self.hosted_key_env])
        except Exception:  # noqa: BLE001
            bal = None
        return bal is not None and bal - n * COST_PER_JOB_USD >= self.hosted_floor_usd

    def charge_trial(self, n: int, ip: str, cookie: str | None, pkey: str):
        """Reserve n jobs of a free trial run. The first paid action on a project starts the visitor's run;
        later actions on the same project (e.g. Dub translate -> generate, a re-roll) use the same run."""
        with self.budget_lock:
            d = self._ledger()
            t = self._trial(d)
            ipk, ck = self._ip_id(ip), self._cookie_id(cookie)
            owner = t["ips"].get(ipk) or (t["cookies"].get(ck) if ck else None)
            if owner and owner != pkey:
                raise PermissionError("you've used your free run (one per visitor). Paste your own Comfy Cloud key "
                                      "to keep going (platform.comfy.org → API keys), or use a judge code")
            if pkey not in t["jobs"] and int(t["used"]) >= self.trial_runs:
                raise PermissionError(f"all {self.trial_runs} free runs are used up. Paste your own Comfy Cloud key "
                                      "(platform.comfy.org → API keys), or use a judge code")
            used = int(t["jobs"].get(pkey, 0))
            if used + n > self.trial_jobs_per_run:
                raise PermissionError(f"a free run covers up to {self.trial_jobs_per_run} Comfy jobs ({used} used on "
                                      f"this project, this needs {n}). Paste your own key for more")
            if not self._floor_ok(n):
                raise PermissionError("the free runs are used up. Paste your own Comfy Cloud key to keep going")
            if pkey not in t["jobs"]:
                t["used"] = int(t["used"]) + 1
            t["jobs"][pkey] = used + n
            t["ips"][ipk] = pkey
            if ck:
                t["cookies"][ck] = pkey
            save_json(d, self.counter_file)

    def refund_trial(self, n: int, pkey: str):
        """Give back unsubmitted trial jobs; a run with nothing submitted is released entirely."""
        if n <= 0:
            return
        with self.budget_lock:
            d = self._ledger()
            t = self._trial(d)
            if pkey not in t["jobs"]:
                return
            left = int(t["jobs"][pkey]) - n
            if left > 0:
                t["jobs"][pkey] = left
            else:
                del t["jobs"][pkey]
                t["used"] = max(0, int(t["used"]) - 1)
                for m in (t["ips"], t["cookies"]):
                    for k in [k for k, v in m.items() if v == pkey]:
                        del m[k]
            save_json(d, self.counter_file)

    def judge_ok(self, code: str | None) -> bool:
        import hmac
        import os
        return bool(self.judge_enabled and code and hmac.compare_digest(code.strip().encode(),
                                                                     os.environ["MULTICAM_JUDGE_CODE"].encode()))

    # ---------- public-demo guards
    def client_ip(self, request) -> str:
        """Client IP. Behind N trusted proxies, take the N-th address from the right of X-Forwarded-For
        (the left part of the header is client-controlled and must not be trusted)."""
        if self.proxy_hops:
            xff = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
            if len(xff) >= self.proxy_hops:
                return xff[-self.proxy_hops]
        return request.client.host if request.client else "?"

    @staticmethod
    def _ip_id(ip: str) -> str:
        import hashlib
        return hashlib.sha256(("multicam:" + ip).encode()).hexdigest()[:16]  # never store raw IPs

    def _ledger(self) -> dict:
        d = load_json(self.counter_file) if self.counter_file.exists() else {}
        d.setdefault("jobs", 0)
        d.setdefault("ip_jobs", {})
        d.setdefault("ip_projects", {})
        return d

    def _recent(self, ts: list, window: float) -> list:
        now = time.time()
        return [t for t in ts if now - t < window]

    def check_project_quota(self, ip: str, record: bool = False):
        """Per-IP upload rate limit; call with record=False before and record=True after a valid upload."""
        if not (self.public_site and self.ip_projects_per_hour):
            return
        with self.budget_lock:
            d = self._ledger()
            k = self._ip_id(ip)
            ts = self._recent(d["ip_projects"].get(k, []), 3600)
            if not record and len(ts) >= self.ip_projects_per_hour:
                raise PermissionError(f"rate limit: {self.ip_projects_per_hour} uploads per hour on the public demo; "
                                      "try again later or run it yourself (it's open source)")
            if record:
                d["ip_projects"][k] = ts + [time.time()]
                save_json(d, self.counter_file)

    # ---------- key handling
    def resolve_key(self, header_key: str | None, judge_code: str | None = None) -> tuple[str, bool | str]:
        """-> (key, False) for the visitor's own key, (operator key, True) in open hosted mode,
        (operator key, "judge") with a valid judge code."""
        if header_key and header_key.strip():
            return header_key.strip(), False
        if self.hosted:
            return self.operator_key, True
        if judge_code and judge_code.strip():
            if self.judge_ok(judge_code):
                return self.operator_key, "judge"
            raise PermissionError("that judge code isn't valid. Paste your own Comfy Cloud key instead "
                                  "(platform.comfy.org → API keys)")
        if self.trial_enabled:
            return self.operator_key, "trial"  # charge_trial enforces the pool / per-visitor / floor limits
        raise PermissionError("Comfy Cloud API key required: paste your own key "
                              "(get one at platform.comfy.org → API keys)")

    def hosted_used(self) -> int:
        return self._ledger()["jobs"]

    def judge_used(self) -> int:
        return int(self._ledger().get("judge_jobs", 0))

    def charge_hosted(self, n: int, ip: str = "?", judge: bool = False):
        """Atomically reserve n jobs of the operator's budget (global cap + per-IP daily cap).
        Judge-code jobs have their own total cap and no per-IP cap; the balance floor applies to both."""
        with self.budget_lock:
            d = self._ledger()
            if judge:
                used = int(d.get("judge_jobs", 0))
                if used + n > self.judge_max_jobs:
                    raise PermissionError(f"the judge quota is used up ({used}/{self.judge_max_jobs} jobs). "
                                          "Paste your own Comfy Cloud key to keep going")
            used = d["jobs"]
            if not judge and self.max_jobs is not None and used + n > self.max_jobs:
                raise PermissionError(f"the public demo budget is used up ({used}/{self.max_jobs} jobs). "
                                      "Paste your own Comfy Cloud key to keep going")
            if not self._floor_ok(n):  # asks Comfy for the real balance (survives restarts/disk loss)
                    raise PermissionError("the public demo budget is used up. Paste your own Comfy Cloud key to keep going")
            if judge:
                d["judge_jobs"] = int(d.get("judge_jobs", 0)) + n
                save_json(d, self.counter_file)
                return
            k = self._ip_id(ip)
            ts = self._recent(d["ip_jobs"].get(k, []), 86400)
            if self.ip_jobs_per_day is not None and len(ts) + n > self.ip_jobs_per_day:
                raise PermissionError(f"rate limit: {self.ip_jobs_per_day} demo jobs per visitor per day "
                                      f"({len(ts)} used). Paste your own key for more")
            d["jobs"] = used + n
            d["ip_jobs"][k] = ts + [time.time()] * n
            save_json(d, self.counter_file)

    def refund_hosted(self, n: int, ip: str = "?", judge: bool = False):
        """Give back jobs that were reserved but never reached Comfy (e.g. upload failed)."""
        if n <= 0:
            return
        with self.budget_lock:
            d = self._ledger()
            if judge:
                d["judge_jobs"] = max(0, int(d.get("judge_jobs", 0)) - n)
                save_json(d, self.counter_file)
                return
            d["jobs"] = max(0, d["jobs"] - n)
            k = self._ip_id(ip)
            d["ip_jobs"][k] = d["ip_jobs"].get(k, [])[:-n] if n else d["ip_jobs"].get(k, [])
            save_json(d, self.counter_file)

    def hosted_run(self, p: "Project", n: int, ip: str, fn, judge: bool = False, trial: str | None = None):
        """Wrap a paid background action so unsubmitted reservations are refunded (trial: project key)."""
        def submitted():
            return len(p.state.get("prompt_ids", []))
        before = submitted()

        def run():
            try:
                fn()
            finally:
                if trial:
                    self.refund_trial(n - (submitted() - before), trial)
                else:
                    self.refund_hosted(n - (submitted() - before), ip, judge)
        return run

    def _janitor(self):
        while True:
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(60)

    def cleanup(self) -> list[str]:
        """Delete projects older than ttl_minutes (not while they are working)."""
        gone = []
        if not self.ttl_minutes:
            return gone
        cutoff = time.time() - self.ttl_minutes * 60
        for d in self.data.iterdir():
            st = d / "state.json"
            if not st.exists():
                continue
            s = load_json(st)
            if s.get("created", 0) < cutoff and not s.get("busy"):
                self.projects.pop(d.name, None)
                shutil.rmtree(d, ignore_errors=True)
                gone.append(d.name)
        return gone

    # ---------- projects
    def get(self, pid: str) -> Project:
        if pid in self.projects:
            return self.projects[pid]
        root = self.data / pid
        if not (root / "state.json").exists() or "/" in pid or ".." in pid:
            raise KeyError(pid)
        self.projects[pid] = Project(root)
        return self.projects[pid]

    def create(self, filename: str, data_stream, mode: str, script: str | None, lyrics: str | None,
               name: str | None) -> Project:
        pid = uuid.uuid4().hex[:16]
        root = self.data / pid
        (root / "work").mkdir(parents=True)
        ext = Path(filename or "video.mp4").suffix.lower()
        if ext not in (".mp4", ".mov", ".m4v", ".webm", ".mkv"):
            ext = ".mp4"
        src = root / f"source{ext}"
        limit = int(self.max_upload_mb * 1024 * 1024)
        try:
            n = 0
            with open(src, "wb") as f:
                while chunk := data_stream.read(1 << 20):
                    n += len(chunk)
                    if n > limit:
                        raise UploadRejected(413, f"video too large (max {self.max_upload_mb:g} MB)")
                    f.write(chunk)
            try:
                info = probe(src)
            except (Exception, SystemExit):  # noqa: BLE001
                raise UploadRejected(400, "that file doesn't look like a video ffmpeg can read")
            if not info.get("width") or not info.get("duration"):
                raise UploadRejected(400, "no video stream found")
            if self.max_upload_s and info["duration"] > self.max_upload_s + 0.5:
                raise UploadRejected(400, f"video is {info['duration']:.1f}s; the limit here is {self.max_upload_s:g}s "
                                          "(trim it, or run Multicam yourself for longer takes)")
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        p = Project(root)
        p.state = {"id": pid, "name": name or Path(filename or "video").stem, "mode": mode,
                   "created": time.time(), "source": src.name, "info": info,
                   "script": (script or "").strip() or None, "lyrics": (lyrics or "").strip() or None,
                   "steps": {s: {"status": "pending", "msg": ""} for s in STEPS},
                   "jobs": [], "clips": {}, "stills": {}, "engine": self.engine,
                   "cam_stills": dict(RESHOOT_CAMS if self.engine == "reshoot" else DEFAULT_STILLS[mode]),
                   "edits": {}, "renders": [], "busy": None, "error": None}
        p.save()
        self.projects[pid] = p
        return p

    def run_bg(self, p: Project, what: str, fn):
        with p.lock:
            if p.state.get("busy"):
                raise RuntimeError(f"busy: {p.state['busy']}")
            p.state["busy"], p.state["error"] = what, None
            p.save()

        def runner():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                msg = str(e) or e.__class__.__name__
                with p.lock:
                    p.state["error"] = msg
                    for s, v in p.state["steps"].items():
                        if v["status"] == "running":
                            v["status"], v["msg"] = "error", msg
                traceback.print_exc()
            finally:
                with p.lock:
                    p.state["busy"] = None
                    p.save()
        threading.Thread(target=runner, daemon=True).start()

    # ---------- pipeline steps
    def analyze(self, p: Project):
        from .music import MusicParams, music_plan
        from .plan import PlanParams, plan as make_plan
        from .prep import prep
        from .transcribe import analyze
        src = p.root / p.state["source"]
        if p.state["mode"] == "music":
            p.set_step("transcribe", "skipped", "music mode: lyrics from you, no ASR")
            p.set_step("plan", "running", "beats + face visibility")
            lyrics = [ln.strip() for ln in (p.state.get("lyrics") or "").splitlines() if "=" in ln]
            plan, _ = music_plan(src, p.work, MusicParams(), lyrics=lyrics,
                                 angle_names={"B": STILL_TO_PROMPT[p.state["cam_stills"]["B"]],
                                              "C": STILL_TO_PROMPT[p.state["cam_stills"]["C"]]})
        else:
            p.set_step("transcribe", "running", f"faster-whisper {self.whisper_model} (CPU)")
            with self.analyze_sem:
                tr = analyze(src, p.work / "transcript.json", model=self.whisper_model, script=p.state.get("script"))
            p.set_step("transcribe", "done", f"{len(tr['words'])} words, {len(tr['sentences'])} sentences")
            p.set_step("plan", "running", "")
            P = PlanParams()
            plan = make_plan(tr, p.work / "plan.json", P)
            plan["angle_names"].update({"B": STILL_TO_PROMPT[p.state["cam_stills"]["B"]],
                                        "C": STILL_TO_PROMPT[p.state["cam_stills"]["C"]]})
        save_json(plan, p.work / "plan_base.json")
        save_json(plan, p.work / "plan.json")
        prep(plan, p.work / "gen", video=src)
        n_ins = sum(1 for s in plan["shots"] if s["cam"] != "A")
        p.set_step("plan", "done", f"{len(plan['shots'])} shots, {n_ins} inserts, {len(plan['chunks'])} chunks")

    def estimate(self, p: Project) -> dict:
        plan = p.edited_plan()
        need = []
        for s in plan["shots"]:
            if s["cam"] == "A" or not s.get("chunk"):
                continue
            if not p.state["clips"].get(s["chunk"], {}).get(s["cam"]):
                need.append({"chunk": s["chunk"], "cam": s["cam"], "shot": s["i"]})
        engine = self.engine_of(p)
        angles = 0 if engine == "reshoot" or all(p.state["stills"].get(p.state["cam_stills"][c]) for c in ("B", "C")) else 1
        from .comfy import job_cost
        usd = len(need) * job_cost(engine) + angles * job_cost("angles")
        return {"engine": engine, "angle_jobs": angles, "camera_jobs": need, "jobs": len(need) + angles,
                "usd": round(usd, 2), "per_job_usd": job_cost(engine)}

    def engine_of(self, p: Project) -> str:
        return p.state.get("engine") or "idlora"  # projects created before the re-shoot engine

    def _still_path(self, p: Project, cam: str) -> Path | None:
        name = p.state["cam_stills"].get(cam)
        f = p.state["stills"].get(name)
        return (p.root / f) if f else None

    def _gen_angles(self, p: Project, key: str):
        from .comfy import load_bindings, run_workflow
        from .frames import best_face_frame
        wfd = workflows_dir()
        src = p.root / p.state["source"]
        p.set_step("angles", "running", "picking the cleanest front frame")
        frame = p.work / "angles" / "frame.png"
        t = best_face_frame(src, frame)
        p.set_step("angles", "running", f"Qwen-Image-Edit · 8 views from frame @ {t:.2f}s")
        raw = json.loads((wfd / "qwen_angles_bindings.json").read_text())
        uid = f"angles_{sum(1 for j in p.state.get('jobs', []) if j.get('kind') == 'angles') + 1}"
        p.set_job({"uid": uid, "kind": "angles", "label": "Qwen angle stills (8)", "status": "uploading"})
        rec = run_workflow(json.loads((wfd / "qwen_angles_api.json").read_text()), load_bindings(wfd / "qwen_angles_bindings.json"),
                           {"image": str(frame)}, p.work / "angles", key=key, rename=raw.get("_outputs"),
                           on_status=lambda r: p.set_job({"uid": uid, "status": r["status"], "prompt_id": r.get("prompt_id"),
                                                          "elapsed_s": round(time.time() - r["started"], 1), "started": r["started"]}))
        if rec["status"] != "success":
            raise RuntimeError(f"angle stills failed: {rec.get('error')}")
        from .orient import check_still, face_roll
        roll = face_roll(frame)
        checks = {Path(o).stem: dict(zip(("ok", "reason"), check_still(o, frame, source_roll=roll))) for o in rec["outputs"]}
        with p.lock:
            for o in rec["outputs"]:
                p.state["stills"][Path(o).stem] = p.rel(o)
            p.state["stills_frame_t"] = t
            p.state["still_checks"] = checks
            # never keep a rotated still as a camera's automatic choice: fall back to a still that passed
            good = [n for n in ("closeup", "wide", "lowangle", "right45", "left45", "right90", "left90", "aerial")
                    if checks.get(n, {}).get("ok")]
            for cam in ("B", "C"):
                cur = p.state["cam_stills"].get(cam)
                if cur in checks and not checks[cur]["ok"] and good:
                    p.state["cam_stills"][cam] = next((n for n in good if n not in p.state["cam_stills"].values()), good[0])
        p.set_step("angles", "done", f"{len(rec['outputs'])} stills from frame @ {t:.2f}s")

    def _reshoot_job(self, p: Project, chunk_id: str, cam: str, key: str):
        from .comfy import load_bindings
        from .reshoot import ANGLES, insert_ordinals, run_reshoot_job
        wfd = workflows_dir()
        plan = p.edited_plan()
        ch = next(c for c in plan["chunks"] if c["id"] == chunk_id)
        preset = p.state["cam_stills"].get(cam)
        if preset not in ANGLES:
            preset = RESHOOT_CAMS.get(cam, "right90")
        hist = p.state["clips"].get(chunk_id, {}).get(cam, {}).get("history", [])
        variant = insert_ordinals(plan).get((chunk_id, cam), 0) + len(hist)
        version = len(hist) + 1
        out = p.work / "gen" / "clips" / f"{chunk_id}_{cam}_v{version}.mp4"
        uid = f"{chunk_id}_{cam}_v{version}"
        label = f"CAM {cam} · {ANGLES[preset][0]} re-shoot · {chunk_id}"
        p.set_job({"uid": uid, "kind": "camera", "label": label, "chunk": chunk_id, "cam": cam, "status": "queued"})
        rec = run_reshoot_job(p.root / p.state["source"], ch["start"], preset, variant, out,
                              json.loads((wfd / "crossview_api.json").read_text()),
                              load_bindings(wfd / "crossview_bindings.json"), key=key, work_dir=p.work / "gen" / "refs",
                              on_status=lambda r: p.set_job({"uid": uid, "status": r["status"],
                                                             "prompt_id": r.get("prompt_id"), "label": f"{label} · {r['camera']}",
                                                             "elapsed_s": round(time.time() - r["started"], 1),
                                                             "started": r["started"]}))
        p.set_job({"uid": uid, "status": rec["status"], "elapsed_s": rec.get("elapsed_s"), "error": rec.get("error")})
        if rec["status"] != "success":
            raise RuntimeError(f"{label} failed: {rec.get('error')}")
        self.register_clip(p, chunk_id, cam, out, ch["start"], seed=rec["seed"], still=preset,
                           extra={"synced": True, "engine": "reshoot", "camera": rec["camera"]})
        return rec

    def _camera_job(self, p: Project, chunk_id: str, cam: str, key: str):
        if self.engine_of(p) == "reshoot":
            return self._reshoot_job(p, chunk_id, cam, key)
        from .comfy import load_bindings, run_ltx_job
        from .prep import build_music_prompt, build_prompt
        wfd = workflows_dir()
        jobs = load_json(p.work / "gen" / "jobs.json")["jobs"]
        plan = p.base_plan()
        ch = next(c for c in plan["chunks"] if c["id"] == chunk_id)
        base = next((j for j in jobs if j["chunk"] == chunk_id), None)
        if base is None:
            raise RuntimeError(f"no generation job for {chunk_id}")
        job = dict(base, angle=cam, id=f"{chunk_id}_{cam}")
        still_name = p.state["cam_stills"][cam]
        tmpl = STILL_TO_PROMPT.get(still_name, "profile")
        prompt = (build_music_prompt(tmpl, ch.get("words") or "", plan.get("visual_extra"))
                  if plan.get("mode") == "music" else build_prompt(tmpl, ch["words"]))
        pf = p.work / "gen" / "prompts" / f"{chunk_id}_{cam}.txt"
        pf.write_text(prompt)
        job.update(prompt=prompt, prompt_file=str(pf))
        still = self._still_path(p, cam)
        if still is None:
            raise RuntimeError(f"no still for CAM {cam}")
        version = len(p.state["clips"].get(chunk_id, {}).get(cam, {}).get("history", [])) + 1
        out = p.work / "gen" / "clips" / f"{chunk_id}_{cam}_v{version}.mp4"
        uid = f"{chunk_id}_{cam}_v{version}"
        label = f"CAM {cam} · {STILL_LABELS.get(still_name, still_name)} · {chunk_id}"
        p.set_job({"uid": uid, "kind": "camera", "label": label, "chunk": chunk_id, "cam": cam, "status": "queued"})
        rec = run_ltx_job(job, str(still), json.loads((wfd / "idlora_api.json").read_text()),
                          load_bindings(wfd / "idlora_bindings.json"), out, key=key,
                          on_status=lambda r: p.set_job({"uid": uid, "status": r["status"],
                                                         "prompt_id": r.get("prompt_id"),
                                                         "elapsed_s": round(time.time() - r["started"], 1),
                                                         "started": r["started"]}))
        p.set_job({"uid": uid, "status": rec["status"], "elapsed_s": rec.get("elapsed_s"), "error": rec.get("error")})
        if rec["status"] != "success":
            raise RuntimeError(f"{label} failed: {rec.get('error')}")
        self.register_clip(p, chunk_id, cam, out, ch["start"], seed=rec["seed"], still=still_name)
        return rec

    def register_clip(self, p: Project, chunk_id, cam, path: Path, src_start: float, seed=None, still=None,
                      extra: dict | None = None):
        with p.lock:
            slot = p.state["clips"].setdefault(chunk_id, {}).setdefault(cam, {"history": []})
            entry = {"file": p.rel(path), "src_start": float(src_start), "seed": seed, "still": still,
                     "synced": False, "engine": None, "camera": None, "t": time.time(), **(extra or {})}
            slot["history"].append(entry)
            slot.update(entry)
            p.save()

    def generate(self, p: Project, key: str, only: list[dict] | None = None):
        est = self.estimate(p)
        todo = only if only is not None else est["camera_jobs"]
        if est["angle_jobs"] and only is None:
            self._gen_angles(p, key)
        elif only is None:
            p.set_step("angles", "skipped" if self.engine_of(p) == "reshoot" else "done",
                       "not needed: re-shoot uses your real take" if self.engine_of(p) == "reshoot" else "using existing stills")
        p.set_step("cameras", "running", f"{len(todo)} {'re-shoot (CrossView)' if self.engine_of(p) == 'reshoot' else 'ID LoRA'} job(s)")
        futures = [self.pool.submit(self._camera_job, p, j["chunk"], j["cam"], key) for j in todo]
        errors = []
        for f in futures:
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
        if errors:
            p.set_step("cameras", "error", "; ".join(errors)[:500])
            raise RuntimeError(errors[0])
        p.set_step("cameras", "done", f"{len(todo)} clip(s) generated")
        self.render(p)

    def render(self, p: Project, shake: bool = True):
        from .align import align_plan
        from .assemble import assemble, load_clips
        src = p.root / p.state["source"]
        plan = p.edited_plan()
        tr_path = p.work / "transcript.json"
        tr = load_json(tr_path) if tr_path.exists() else {"words": [], "sentences": [], "phrases": []}
        cj = {}
        for s in plan["shots"]:
            if s["cam"] == "A" or not s.get("chunk"):
                continue
            c = p.state["clips"].get(s["chunk"], {}).get(s["cam"])
            if c:
                if c.get("still") in BURN_NAMES:
                    s["label_name"] = BURN_NAMES[c["still"]]
                cj[s["chunk"]] = {"path": str(p.root / c["file"]), "angle": s["cam"], "src_start": c["src_start"],
                                  "synced": bool(c.get("synced"))}
            else:
                s["align"] = {"status": "missing", "reason": f"no CAM {s['cam']} clip for {s['chunk']} yet"}
        save_json(cj, p.work / "clips.json")
        clips = load_clips(plan, str(p.work / "clips.json"), None)
        p.set_step("align", "running", "lip-sync / beat alignment")
        report = align_plan(plan, tr, clips, p.work / "align_cache")
        for s in plan["shots"]:
            if "user_offset" in s and s.get("align", {}).get("status") == "ok":
                s["offset"] = round(s.get("offset", 0.0) + s["user_offset"], 3)
        save_json(plan, p.work / "plan.json")
        p.set_step("align", "done", ", ".join(f"{r['chunk']} {r['offset']:+.2f}s×{r['speed']:.2f}"
                                              for r in report if r.get("status") == "ok") or "nothing to align")
        p.set_step("render", "running", "ffmpeg")
        ver = len(p.state.get("renders", [])) + 1
        outd = p.work / "renders"
        outd.mkdir(exist_ok=True)
        clean, labeled = outd / f"final_v{ver}.mp4", outd / f"final_v{ver}_labeled.mp4"
        kw = dict(clips=clips, video=src, shake=shake, transcript=tr, max_long=self.render_max_long,
                  preset=self.render_preset)
        shots = assemble(plan, clean, **kw)
        p.set_step("render", "running", "ffmpeg (labeled copy)")
        assemble(plan, labeled, labels=True, **kw)
        with p.lock:
            p.state.setdefault("renders", []).append({"v": ver, "clean": p.rel(clean), "labeled": p.rel(labeled),
                                                       "t": time.time(),
                                                       "shots": [{"cam": s["cam"], "start": s["start"], "end": s["end"],
                                                                  "src": s["src"]} for s in shots]})
        n_gen = sum(1 for s in shots if s["src"] == "generated")
        p.set_step("render", "done", f"v{ver}: {n_gen} generated insert(s), real audio untouched")

    def share(self, p: Project, title: str | None) -> dict:
        from .frames import poster
        if not p.state.get("renders"):
            raise RuntimeError("render first")
        r = p.state["renders"][-1]
        d = p.work / "share"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        shutil.copy(p.root / r["clean"], d / "multicam.mp4")
        shutil.copy(p.root / r["labeled"], d / "multicam_labeled.mp4")
        poster(d / "multicam.mp4", d / "poster.jpg", t=1.0)
        title = (title or p.state.get("name") or "My multicam edit").strip()[:80]
        text = f"{title}: one selfie take, an AI multicam edit (ComfyUI + LTX-2.3). #OpenSourceHiggsfield"
        x_url = f"https://x.com/intent/tweet?text={quote(text, safe='')}&url={quote(self.repo_url, safe='')}"
        html = SHARE_HTML.replace("{{TITLE}}", _esc(title)).replace("{{X}}", x_url).replace("{{REPO}}", self.repo_url)
        (d / "index.html").write_text(html)
        zpath = p.work / "share.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in d.iterdir():
                z.write(f, f"multicam-share/{f.name}")
        return {"page": p.url(d / "index.html"), "zip": p.url(zpath), "x_intent": x_url, "text": text}

    # ---------- public state (no secrets ever)
    def public(self, p: Project) -> dict:
        st = copy.deepcopy(p.state)
        st["source_url"] = p.url(p.root / st["source"])
        st["stills"] = {k: {"url": p.url(p.root / v), "label": STILL_LABELS.get(k, "Your still " + k[-1] if k.startswith("custom_") else k)}
                        for k, v in st["stills"].items()}
        for chunk in st["clips"].values():
            for cam, c in chunk.items():
                c["url"] = p.url(p.root / c["file"])
        for r in st.get("renders", []):
            r["clean_url"], r["labeled_url"] = p.url(p.root / r["clean"]), p.url(p.root / r["labeled"])
        if (p.work / "plan_base.json").exists():
            plan = load_json(p.work / "plan.json") if (p.work / "plan.json").exists() else p.base_plan()
            edited = {s["i"]: s for s in p.edited_plan()["shots"]}
            aligned = {s["i"]: s for s in plan["shots"]}
            shots = []
            for s in p.base_plan()["shots"]:
                e, a = edited[s["i"]], aligned.get(s["i"], {})
                shots.append({"i": s["i"], "cam": e["cam"], "planned_cam": s["cam"], "start": a.get("start", s["start"]),
                              "end": a.get("end", s["end"]), "words": s.get("words", ""), "chunk": s.get("chunk"),
                              "has_clip": bool(s.get("chunk") and st["clips"].get(s["chunk"], {}).get(e["cam"])),
                              "align": {k: a.get("align", {}).get(k) for k in ("status", "offset", "speed", "method", "reason")}
                              if a.get("align") else None})
            st["plan"] = {"duration": plan["duration"], "mode": plan.get("mode", "talk"), "shots": shots,
                          "chunks": [{k: c[k] for k in ("id", "start", "end", "words")} for c in plan["chunks"]],
                          "angle_names": plan.get("angle_names")}
            st["estimate"] = self.estimate(p)
        return st


def _limited_copy(src, dest: Path, max_mb: float):
    from fastapi import HTTPException
    n, limit = 0, int(max_mb * 1024 * 1024)
    with open(dest, "wb") as f:
        while chunk := src.read(1 << 20):
            n += len(chunk)
            if n > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"file too large (max {max_mb:g} MB)")
            f.write(chunk)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


SHARE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}} · Multicam</title>
<meta property="og:title" content="{{TITLE}}"><meta property="og:type" content="video.other">
<meta property="og:image" content="poster.jpg"><meta property="og:video" content="multicam.mp4">
<meta name="twitter:card" content="player">
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;
background:radial-gradient(1200px 600px at 20% -10%,#2a1b5e 0,transparent 60%),radial-gradient(900px 500px at 110% 10%,#0f3d4a 0,transparent 55%),#0b0b12;
font:16px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Inter,sans-serif;color:#ecebf5}
main{width:min(440px,92vw);padding:24px 0}h1{font-size:22px;margin:0 0 4px}p{color:#a7a5bd;margin:0 0 16px}
video{width:100%;border-radius:18px;box-shadow:0 20px 60px #0008;background:#000;aspect-ratio:9/16}
.row{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}a.btn{flex:1;text-align:center;padding:12px 14px;border-radius:12px;
background:#1b1a2b;color:#fff;text-decoration:none;border:1px solid #2c2a44;font-weight:600}a.btn.p{background:linear-gradient(135deg,#7c5cff,#2bd2ff);border:0}
label{display:flex;gap:8px;align-items:center;color:#a7a5bd;font-size:14px;margin-top:10px;cursor:pointer}
footer{margin-top:18px;font-size:13px;color:#77758f}footer a{color:#a7a5bd}
</style></head><body><main>
<h1>{{TITLE}}</h1><p>One phone shot → multicam. Camera A is real; B/C are generated with ComfyUI (LTX-2.3 ID LoRA + Qwen angles), cut on the words over the real audio.</p>
<video id="v" src="multicam.mp4" poster="poster.jpg" controls playsinline></video>
<label><input type="checkbox" id="lab"> Show camera labels</label>
<div class="row"><a class="btn p" href="{{X}}" target="_blank" rel="noopener">Share on X</a><a class="btn" href="multicam.mp4" download>Download</a></div>
<footer>Made with <a href="{{REPO}}">Multicam</a> · #OpenSourceHiggsfield</footer>
</main><script>
const v=document.getElementById('v');document.getElementById('lab').onchange=e=>{const t=v.currentTime,p=!v.paused;
v.src=e.target.checked?'multicam_labeled.mp4':'multicam.mp4';v.currentTime=t;if(p)v.play();};
</script></body></html>"""


def create_app(app_state: App):
    from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    api = FastAPI(title="Multicam", version=__version__, docs_url="/api/docs")
    A = app_state

    def proj(pid) -> Project:
        try:
            return A.get(pid)
        except KeyError:
            raise HTTPException(404, "project not found")

    def need_plan(p):
        """Plan-dependent endpoints: 409 (not 500) while the upload is still being analyzed."""
        if not (p.work / "plan_base.json").exists():
            st = p.state["steps"].get("plan", {}).get("status")
            raise HTTPException(409, "analysis failed; upload the video again" if st == "error" or p.state.get("error")
                                else "still analyzing your video (transcribe + plan); try again in a few seconds")
        return p

    def key_or_403(k, judge_code=None):
        try:
            return A.resolve_key(k, judge_code)
        except PermissionError as e:
            raise HTTPException(403, str(e))

    from .comfy import export_workflows, set_workflow_archive
    set_workflow_archive(A.data / "workflows")

    def wf_dir(p) -> Path:
        return (getattr(p, "root", None) or p.work) / "workflows"

    def wf_ids(p) -> list:
        ids = getattr(p, "workflow_ids", None)
        return list(ids) if ids is not None else list(p.state.get("prompt_ids", []))

    def with_workflow_export(p, fn):
        """After a paid action, save the exact API graphs it submitted into <project>/workflows/."""
        def run():
            try:
                fn()
            finally:
                try:
                    export_workflows(wf_ids(p), wf_dir(p))
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        return run

    def charge_or_403(request, n, hosted, p, fn):
        """Reserve hosted budget (if the operator's key is used) and return the background callable."""
        fn = with_workflow_export(p, fn)
        if not hosted:
            return fn
        ip = A.client_ip(request)
        if hosted == "trial":
            pkey = A.project_key(p)
            try:
                A.charge_trial(n, ip, request.cookies.get(VISITOR_COOKIE), pkey)
            except PermissionError as e:
                raise HTTPException(403, str(e))
            return A.hosted_run(p, n, ip, fn, trial=pkey)
        judge = hosted == "judge"
        try:
            A.charge_hosted(n, ip, judge=judge)
        except PermissionError as e:
            raise HTTPException(429 if "rate limit" in str(e) else 403, str(e))
        return A.hosted_run(p, n, ip, fn, judge=judge)

    @api.middleware("http")
    async def visitor_cookie(request: Request, call_next):
        """Free-trial visitors get a random first-party cookie (one free run per visitor = IP + cookie)."""
        resp = await call_next(request)
        if A.trial_enabled and not request.cookies.get(VISITOR_COOKIE):
            import secrets as _secrets
            https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
            resp.set_cookie(VISITOR_COOKIE, _secrets.token_hex(12), max_age=400 * 86400, httponly=True,
                            samesite="lax", secure=https)
        return resp

    @api.get("/api/config")
    def config(request: Request):
        hosted = A.hosted
        trial = A.trial_status(A.client_ip(request), request.cookies.get(VISITOR_COOKIE))
        from .comfy import job_cost
        from .reshoot import ANGLES
        return {"version": __version__, "hosted": hosted, "hosted_jobs_left": max(0, A.max_jobs - A.hosted_used())
                if hosted and A.max_jobs is not None else None, "hosted_jobs_total": A.max_jobs if hosted else None,
                "ip_jobs_per_day": A.ip_jobs_per_day if hosted else None,
                "max_upload_s": A.max_upload_s, "max_upload_mb": A.max_upload_mb, "ttl_minutes": A.ttl_minutes,
                "cost_per_job_usd": job_cost(A.engine), "repo_url": A.repo_url, "max_jobs_per_run": A.max_jobs_per_run,
                "byok_only": not hosted and not trial, "trial": trial, "judge_code": A.judge_enabled, "public": A.public_site,
                "get_key_url": "https://platform.comfy.org/profile/api-keys",
                "engine": A.engine, "cost_usd": {e: job_cost(e) for e in (*ENGINES, "angles")},
                "reshoot_angles": {k: {"label": v[0], "variants": v[1]} for k, v in ANGLES.items()}}

    def _wf_target(kind: str, oid: str):
        """-> (project dir for workflows/, prompt ids) for a Talk/Music project, a Scene or a Dub."""
        if kind == "projects":
            p = proj(oid)
            return wf_dir(p), wf_ids(p)
        if not oid.isalnum():
            raise HTTPException(404, "not found")
        d = A.data / ("scenes" if kind == "scene" else "dubs") / oid
        if not (d / "web.json").exists():
            raise HTTPException(404, "not found")
        if kind == "scene":
            from . import scene as _S
            ids = [j.get("prompt_id") for j in _S.load(d).get("jobs", [])] if (d / "scene.json").exists() else []
        else:
            ids = load_json(d / "dub.json").get("prompt_ids", []) if (d / "dub.json").exists() else []
        return d / "workflows", ids

    def _wf_files(kind, oid):
        dest, ids = _wf_target(kind, oid)
        files = export_workflows(ids, dest)
        if not files:
            raise HTTPException(404, "no ComfyUI workflows yet: they appear once a Comfy Cloud job has run")
        return files

    for _kind in ("projects", "scene", "dub"):
        def _list(oid: str, _k=_kind):
            dest, ids = _wf_target(_k, oid)
            files = export_workflows(ids, dest)
            return {"workflows": [{"name": f.name, "prompt_id": f.stem.rsplit("_", 1)[-1],
                                   "url": f"/api/{_k}/{oid}/workflow/{f.stem.rsplit('_', 1)[-1]}"} for f in files],
                    "zip": f"/api/{_k}/{oid}/workflows.zip" if files else None}

        def _one(oid: str, prompt_id: str, _k=_kind):
            for f in _wf_files(_k, oid):
                if f.stem.endswith("_" + prompt_id):
                    return FileResponse(f, media_type="application/json", filename=f"multicam_{f.name}")
            raise HTTPException(404, "no workflow for that job")

        def _zip(oid: str, _k=_kind):
            files = _wf_files(_k, oid)
            if len(files) == 1:
                return FileResponse(files[0], media_type="application/json", filename=f"multicam_{files[0].name}")
            z = files[0].parent.parent / "workflows.zip"
            with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, f"multicam-comfyui-workflows/{f.name}")
                zf.writestr("multicam-comfyui-workflows/README.txt",
                            "ComfyUI API-format graphs, exactly as Multicam submitted them to Comfy Cloud (one per job).\n"
                            "Load one in ComfyUI (Workflow > Open, or drag it in) or POST it to /api/prompt as {\"prompt\": <graph>}.\n"
                            "Uploaded inputs (the reference clip) are referenced by their Comfy Cloud file name.\n")
            return FileResponse(z, media_type="application/zip", filename="multicam-comfyui-workflows.zip")

        api.get(f"/api/{_kind}/{{oid}}/workflows")(_list)
        api.get(f"/api/{_kind}/{{oid}}/workflow/{{prompt_id}}")(_one)
        api.get(f"/api/{_kind}/{{oid}}/workflows.zip")(_zip)

    @api.post("/api/check-key")
    def check_key(request: Request, x_comfy_key: str | None = Header(default=None),
                  x_judge_code: str | None = Header(default=None)):
        from .comfy import ComfyError, balance_usd, check_key as ck
        if not (x_comfy_key or "").strip() and not (x_judge_code or "").strip() and not A.hosted:
            t = A.trial_status(A.client_ip(request), request.cookies.get(VISITOR_COOKIE))
            if not t:
                raise HTTPException(403, "Comfy Cloud API key required: paste your own key "
                                         "(get one at platform.comfy.org → API keys)")
            return {"ok": True, "trial": t}
        if not (x_comfy_key or "").strip() and (x_judge_code or "").strip():
            if not A.judge_ok(x_judge_code):
                raise HTTPException(403, "that judge code isn't valid")
            return {"ok": True, "judge": True, "judge_jobs_left": max(0, A.judge_max_jobs - A.judge_used()),
                    "judge_jobs_total": A.judge_max_jobs}
        key, hosted = key_or_403(x_comfy_key, x_judge_code)
        try:
            u = ck(key)
        except ComfyError as e:
            raise HTTPException(401, str(e))
        return {"ok": True, "status": u.get("status"), "balance_usd": None if hosted else balance_usd(key),
                "hosted": hosted}

    @api.get("/api/projects")
    def list_projects():
        if A.public_site:  # public site: never list other visitors' projects
            return []
        out = []
        for d in sorted(A.data.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if (d / "state.json").exists():
                s = load_json(d / "state.json")
                out.append({"id": s["id"], "name": s["name"], "mode": s["mode"], "created": s["created"],
                            "renders": len(s.get("renders", []))})
        return out[:50]

    @api.post("/api/projects")
    def create(request: Request, file: UploadFile = File(...), mode: str = Form("talk"), script: str = Form(""),
               lyrics: str = Form(""), name: str = Form(""), consent: str = Form("")):
        if mode not in ("talk", "music"):
            raise HTTPException(400, "mode must be talk or music")
        if consent.lower() not in ("1", "true", "yes", "on"):
            raise HTTPException(400, "please confirm this is your face or you have the person's permission")
        try:
            A.check_project_quota(A.client_ip(request))
        except PermissionError as e:
            raise HTTPException(429, str(e))
        try:
            p = A.create(file.filename, file.file, mode, script[:4000], lyrics[:4000], name[:80])
        except UploadRejected as e:
            raise HTTPException(e.status, str(e))
        A.check_project_quota(A.client_ip(request), record=True)
        A.run_bg(p, "analyze", lambda: A.analyze(p))
        return A.public(p)

    @api.get("/api/projects/{pid}")
    def get(pid: str):
        return A.public(proj(pid))

    @api.post("/api/projects/{pid}/cams")
    async def cams(pid: str, request: Request):
        p = proj(pid)
        body = await request.json()
        from .reshoot import ANGLES
        with p.lock:
            if body.get("engine") in ENGINES and body["engine"] != A.engine_of(p):
                p.state["engine"] = body["engine"]
                if body["engine"] == "reshoot":  # stills names double as camera presets
                    p.state["cam_stills"] = {c: (v if v in ANGLES else RESHOOT_CAMS[c]) for c, v in p.state["cam_stills"].items()}
            for cam in ("B", "C"):
                if body.get(cam):
                    p.state["cam_stills"][cam] = body[cam]
            p.save()
        return A.public(p)

    @api.post("/api/projects/{pid}/stills")
    def import_still(pid: str, cam: str = Form(...), file: UploadFile = File(...)):
        p = proj(pid)
        cam = cam.upper()
        if cam not in ("B", "C"):
            raise HTTPException(400, "cam must be B or C")
        dest = p.work / "angles" / f"custom_{cam}{Path(file.filename or 'x.png').suffix or '.png'}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        _limited_copy(file.file, dest, A.max_upload_mb)
        with p.lock:
            p.state["stills"][dest.stem] = p.rel(dest)
            p.state["cam_stills"][cam] = dest.stem
            if p.state["steps"]["angles"]["status"] == "pending":
                p.state["steps"]["angles"] = {"status": "done", "msg": "your own stills", "t": time.time()}
            p.save()
        return A.public(p)

    @api.post("/api/projects/{pid}/clips")
    def import_clip(pid: str, chunk: str = Form(...), cam: str = Form(...), file: UploadFile = File(...),
                    src_start: float | None = Form(None), synced: bool = Form(False),
                    camera: str | None = Form(None)):
        """Import your own clip. synced=true: a time-locked re-shoot of the source (e.g. from
        `multicam reshoot`): no whisper alignment, offset 0, speed 1."""
        p = proj(pid)
        plan = p.base_plan()
        ch = next((c for c in plan["chunks"] if c["id"] == chunk), None)
        if not ch:
            raise HTTPException(404, "unknown chunk")
        cam = cam.upper()
        if cam not in ("B", "C"):
            raise HTTPException(400, "cam must be B or C")
        n = len(p.state["clips"].get(chunk, {}).get(cam, {}).get("history", [])) + 1
        dest = p.work / "gen" / "clips" / f"{chunk}_{cam}_v{n}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        _limited_copy(file.file, dest, A.max_upload_mb)
        A.register_clip(p, chunk, cam, dest, ch["start"] if src_start is None else src_start, still="imported",
                        extra={"synced": True, "engine": "reshoot", "camera": (camera or "")[:120] or None}
                        if synced else None)
        with p.lock:
            n = sum(len(v) for v in p.state["clips"].values())
            if p.state["steps"]["cameras"]["status"] in ("pending", "done"):
                p.state["steps"]["cameras"] = {"status": "done", "msg": f"{n} clip(s) imported", "t": time.time()}
            p.save()
        return A.public(p)

    @api.get("/api/projects/{pid}/estimate")
    def estimate(pid: str):
        return A.estimate(need_plan(proj(pid)))

    @api.post("/api/projects/{pid}/generate")
    async def generate(pid: str, request: Request, x_comfy_key: str | None = Header(default=None),
                   x_judge_code: str | None = Header(default=None)):
        p = need_plan(proj(pid))
        body = await request.json()
        est = A.estimate(p)
        if not body.get("confirm"):
            raise HTTPException(400, {"needs_confirm": True, **est})
        if est["jobs"] > A.max_jobs_per_run:
            raise HTTPException(400, f"{est['jobs']} jobs exceeds the per-run cap ({A.max_jobs_per_run})")
        key, hosted = key_or_403(x_comfy_key, x_judge_code)
        if p.state.get("busy"):
            raise HTTPException(409, f"busy: {p.state['busy']}")
        fn = charge_or_403(request, est["jobs"], hosted, p, lambda: A.generate(p, key))
        try:
            A.run_bg(p, "generate", fn)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return A.public(p)

    @api.post("/api/projects/{pid}/angles")
    async def angles(pid: str, request: Request, x_comfy_key: str | None = Header(default=None),
                   x_judge_code: str | None = Header(default=None)):
        """Only (re)generate the 8 Qwen angle stills (1 job)."""
        p = proj(pid)
        body = await request.json() if (await request.body()) else {}
        if not body.get("confirm"):
            from .comfy import job_cost
            raise HTTPException(400, {"needs_confirm": True, "jobs": 1, "usd": job_cost("angles")})
        key, hosted = key_or_403(x_comfy_key, x_judge_code)
        if p.state.get("busy"):
            raise HTTPException(409, f"busy: {p.state['busy']}")
        fn = charge_or_403(request, 1, hosted, p, lambda: A._gen_angles(p, key))
        try:
            A.run_bg(p, "angles", fn)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return A.public(p)

    @api.post("/api/projects/{pid}/reroll")
    async def reroll(pid: str, request: Request, x_comfy_key: str | None = Header(default=None),
                   x_judge_code: str | None = Header(default=None)):
        p = need_plan(proj(pid))
        body = await request.json()
        plan = p.edited_plan()
        s = next((x for x in plan["shots"] if x["i"] == int(body["shot"])), None)
        if not s or not s.get("chunk"):
            raise HTTPException(400, "pick an insert shot")
        cam = (body.get("cam") or (s["cam"] if s["cam"] != "A" else s.get("planned_cam", "B"))).upper()
        if cam not in ("B", "C"):
            raise HTTPException(400, "cam must be B or C")
        if not body.get("confirm"):
            from .comfy import job_cost
            raise HTTPException(400, {"needs_confirm": True, "jobs": 1, "usd": job_cost(A.engine_of(p))})
        key, hosted = key_or_403(x_comfy_key, x_judge_code)
        still = body.get("still") or p.state["cam_stills"].get(cam)
        from .reshoot import ANGLES
        if A.engine_of(p) == "reshoot":
            if still not in ANGLES:
                raise HTTPException(400, f"unknown camera preset {still}")
        elif still not in p.state["stills"]:
            raise HTTPException(400, f"no still for CAM {cam}; generate angles first")
        if p.state.get("busy"):
            raise HTTPException(409, f"busy: {p.state['busy']}")
        with p.lock:
            p.state["cam_stills"][cam] = still
            p.state["edits"][str(s["i"])] = {"cam": cam}
            p.save()
        fn = charge_or_403(request, 1, hosted, p, lambda: A.generate(p, key, only=[{"chunk": s["chunk"], "cam": cam}]))
        try:
            A.run_bg(p, "reroll", fn)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return A.public(p)

    @api.patch("/api/projects/{pid}/shots")
    async def edit_shots(pid: str, request: Request):
        p = need_plan(proj(pid))
        body = await request.json()
        base = {s["i"]: s for s in p.base_plan()["shots"]}
        with p.lock:
            for i, e in (body.get("edits") or {}).items():
                s = base.get(int(i))
                if not s or not s.get("chunk"):
                    continue
                if e is None or e.get("cam") == s["cam"] and "offset" not in e:
                    p.state["edits"].pop(str(i), None)
                else:
                    p.state["edits"][str(i)] = {k: v for k, v in e.items() if k in ("cam", "offset")}
            p.state["edits_t"] = time.time()
            p.save()
        return A.public(p)

    @api.post("/api/projects/{pid}/render")
    async def render(pid: str, request: Request):
        p = need_plan(proj(pid))
        body = await request.json() if (await request.body()) else {}
        try:
            A.run_bg(p, "render", lambda: A.render(p, shake=body.get("shake", True)))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return A.public(p)

    @api.post("/api/projects/{pid}/share")
    async def share(pid: str, request: Request):
        p = proj(pid)
        body = await request.json() if (await request.body()) else {}
        try:
            return A.share(p, body.get("title"))
        except RuntimeError as e:
            raise HTTPException(400, str(e))

    @api.get("/files/{pid}/{path:path}")
    def files(pid: str, path: str):
        p = proj(pid)
        f = (p.root / path).resolve()
        if p.root.resolve() not in f.parents or not f.is_file() or f.name == "state.json":
            raise HTTPException(404)
        return FileResponse(f)

    @api.exception_handler(HTTPException)
    async def http_exc(_, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    from .scene_web import mount as mount_scene  # Scene mode: /api/scene/...
    mount_scene(api, A, key_or_403, charge_or_403)
    from .dub_web import mount as mount_dub  # Dub mode: /api/dub/...
    mount_dub(api, A, key_or_403, charge_or_403)

    docs = REPO_ROOT / "docs"
    if docs.is_dir():  # the static gallery (same files GitHub Pages serves)
        api.mount("/gallery", StaticFiles(directory=str(docs), html=True), name="gallery")
    api.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return api


def serve(host="127.0.0.1", port=8787, data_dir="~/.multicam/projects", **kw):
    import uvicorn
    app = create_app(App(Path(data_dir), **kw))
    print(f"Multicam {__version__} → http://{host}:{port}  (data: {Path(data_dir).expanduser()})")
    uvicorn.run(app, host=host, port=port, log_level="info")
