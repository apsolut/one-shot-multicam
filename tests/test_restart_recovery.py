"""A deploy/restart kills runner threads but "busy" stayed in state.json forever (project 4c82476e788c4c50 spun
on "generate" for an hour). Startup recovery, stale detection and the free Retry endpoint. No Comfy calls."""
import time

from fastapi.testclient import TestClient

from multicam import comfy
from multicam.server import App, create_app
from multicam.util import load_json, save_json

PLAN = {"duration": 10.0, "mode": "talk", "angle_names": {}, "note": "",
        "shots": [{"i": 0, "cam": "A", "start": 0, "end": 4, "words": "hi", "chunk": None},
                  {"i": 1, "cam": "B", "start": 4, "end": 7, "words": "there", "chunk": "c01"},
                  {"i": 2, "cam": "B", "start": 7, "end": 10, "words": "you", "chunk": "c02"}],
        "chunks": [{"id": "c01", "start": 4.0, "end": 7.0, "words": "there"},
                   {"id": "c02", "start": 7.0, "end": 10.0, "words": "you"}]}


def _project(data, pid="p1", busy="generate", jobs=None, clips=None, render_running=True):
    root = data / pid
    (root / "work").mkdir(parents=True)
    (root / "source.mp4").write_bytes(b"x")
    save_json(PLAN, root / "work" / "plan_base.json")
    steps = {s: {"status": "done", "msg": "", "t": time.time() - 3600} for s in
             ("transcribe", "plan", "angles", "cameras", "align", "render")}
    if render_running:
        steps["render"] = {"status": "running", "msg": "ffmpeg (labeled copy)", "t": time.time() - 3600}
    save_json({"id": pid, "name": "t", "mode": "talk", "created": time.time() - 3700, "source": "source.mp4",
               "info": {}, "steps": steps, "jobs": jobs or [], "clips": clips or {}, "stills": {},
               "engine": "reshoot", "cam_stills": {"B": "right90", "C": "wide"}, "edits": {}, "renders": [],
               "busy": busy, "error": None}, root / "state.json")
    return root


def _clip(chunk):
    return {"B": {"history": [], "file": f"work/gen/clips/{chunk}_B_v1.mp4", "src_start": 0.0, "still": "right90",
                  "synced": True}}


def test_startup_marks_orphaned_busy_project_failed_with_retry(tmp_path):
    root = _project(tmp_path / "d", jobs=[{"uid": "c01_B_v1", "kind": "camera", "chunk": "c01", "cam": "B",
                                            "status": "running", "prompt_id": "pid-1", "started": time.time() - 3600}])
    _project(tmp_path / "d", pid="idle", busy=None, render_running=False)
    A = App(tmp_path / "d")
    assert A.recovered == ["p1"]
    st = load_json(root / "state.json")
    assert st["busy"] is None and "server restart" in st["error"] and st["interrupted"]["step"] == "render"
    assert st["steps"]["render"]["status"] == "error"
    assert st["jobs"][0]["status"] == "lost" and st["jobs"][0]["resumable"] and st["jobs"][0]["prompt_id"] == "pid-1"
    pub = A.public(A.get("p1"))
    assert pub["retry"] is True and pub["stale"] is False
    assert load_json(tmp_path / "d" / "idle" / "state.json").get("interrupted") is None


def test_stale_detection(tmp_path, monkeypatch):
    _project(tmp_path / "d", busy=None)
    A = App(tmp_path / "d")
    p = A.get("p1")
    p.state["busy"] = "generate"
    assert A.is_stale(p)  # busy without a live runner thread in this process = orphan
    A.live.add("p1")
    p.state["busy_t"] = time.time()
    assert not A.is_stale(p)  # live and recently progressing
    assert A.is_stale(p, now=time.time() + 10 * 3600)  # live but no progress for hours


def test_retry_resumes_finished_comfy_job_and_rerenders_without_new_submission(tmp_path, monkeypatch):
    _project(tmp_path / "d", clips={"c01": _clip("c01")},
             jobs=[{"uid": "c02_B_v1", "kind": "camera", "chunk": "c02", "cam": "B", "status": "running",
                    "prompt_id": "pid-2", "label": "CAM B · x · c02 · far right"}])
    A = App(tmp_path / "d")
    fetched, rendered = [], []

    def fake_fetch(prompt_id, out, key=None):
        fetched.append((prompt_id, key))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"clip")
        return "success"
    monkeypatch.setattr(comfy, "fetch_finished", fake_fetch)
    monkeypatch.setattr(comfy, "submit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no new Comfy job")))
    monkeypatch.setattr(A, "render", lambda p, shake=True: rendered.append(p.id))
    c = TestClient(create_app(A))
    r = c.post("/api/projects/p1/retry", headers={"X-Comfy-Key": "k", "X-Pay-Mode": "key"})
    assert r.status_code == 200, r.text
    for _ in range(100):
        if not A.get("p1").state.get("busy"):
            break
        time.sleep(0.02)
    st = A.get("p1").state
    assert fetched == [("pid-2", "k")] and rendered == ["p1"]
    assert st["clips"]["c02"]["B"]["file"] == "work/gen/clips/c02_B_v1.mp4"
    assert st["jobs"][0]["status"] == "success" and st["error"] is None and "interrupted" not in st


def test_retry_without_key_just_rerenders_and_reports_missing_clips(tmp_path, monkeypatch):
    _project(tmp_path / "d", clips={"c01": _clip("c01")})
    A = App(tmp_path / "d")
    monkeypatch.setattr(comfy, "fetch_finished", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no key")))
    rendered = []
    monkeypatch.setattr(A, "render", lambda p, shake=True: rendered.append(p.id))
    c = TestClient(create_app(A))
    assert c.post("/api/projects/p1/retry").status_code == 200
    for _ in range(100):
        if not A.get("p1").state.get("busy"):
            break
        time.sleep(0.02)
    st = A.get("p1").state
    assert rendered == ["p1"] and "1 clip(s) still missing" in st["error"]


def test_retry_refuses_a_live_progressing_run(tmp_path):
    _project(tmp_path / "d", busy=None)
    A = App(tmp_path / "d")
    p = A.get("p1")
    p.state.update(busy="generate", busy_t=time.time())
    A.live.add("p1")
    r = TestClient(create_app(A)).post("/api/projects/p1/retry")
    assert r.status_code == 409 and "still making progress" in r.text


def test_scene_and_dub_orphans_cleared_on_startup(tmp_path):
    for kind in ("scenes", "dubs"):
        d = tmp_path / "d" / kind / "abc123"
        d.mkdir(parents=True)
        save_json({"created": time.time(), "busy": "generate", "log": []}, d / "web.json")
    A = App(tmp_path / "d")
    TestClient(create_app(A))
    for kind in ("scenes", "dubs"):
        w = load_json(tmp_path / "d" / kind / "abc123" / "web.json")
        assert w["busy"] is None and "server restart" in w["error"] and w["interrupted"]["what"] == "generate"


def test_ui_has_retry_button_wired_to_the_free_endpoint():
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "multicam" / "web"
    assert 'id="retryBtn"' in (web / "index.html").read_text()
    js = (web / "app.js").read_text()
    assert "/retry`" in js and "S.retry" in js and "S.stale" in js


def test_ttl_janitor_gives_a_recovered_project_grace(tmp_path):
    """Regression: clearing busy let the 60-min TTL janitor delete the recovered project at once."""
    _project(tmp_path / "d")  # created ~62 min ago, busy (orphaned)
    _project(tmp_path / "d", pid="old", busy=None, render_running=False)
    A = App(tmp_path / "d", ttl_minutes=60)
    assert A.recovered == ["p1"]
    assert A.cleanup() == ["old"]
    assert (tmp_path / "d" / "p1" / "state.json").exists()
    A.get("p1").state["interrupted"]["t"] = time.time() - 61 * 60
    A.get("p1").save()
    assert A.cleanup() == ["p1"]  # still deleted, just one TTL after the interruption
