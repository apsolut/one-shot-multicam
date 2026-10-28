"""A free run is counted once per project / scene / dub: never again on a refresh, resume, retry or a second
Generate inside the same project (trial.used in hosted_jobs.json; visitor = hashed IP + mc_vid cookie)."""
from types import SimpleNamespace

import pytest

from multicam.server import VISITOR_COOKIE, App
from multicam.util import load_json


@pytest.fixture
def A(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OP_KEY", "op-key-not-real")
    return App(tmp_path / "d", hosted_key_env="TEST_OP_KEY", public=True, hosted_access="judge", max_jobs=0,
               trial_runs=6, trial_jobs_per_run=10)


def used(A):
    return int(load_json(A.counter_file)["trial"]["used"])


def fake_project(work):
    """Project-like object for App.hosted_run; `submit(n)` simulates Comfy submissions."""
    p = SimpleNamespace(work=work, state={"prompt_ids": []})
    return p, lambda n: p.state["prompt_ids"].extend(f"id{len(p.state['prompt_ids']) + i}" for i in range(n))


def paid_action(A, p, n, submits, ip="7.7.7.7", cookie="vid1"):
    """What charge_or_403 does for a trial request: reserve, run in the background, refund the unsubmitted part."""
    pkey = A.project_key(p)
    A.charge_trial(n, ip, cookie, pkey)
    A.hosted_run(p, n, ip, submits, trial=pkey)()


def test_second_generate_and_retry_count_once(tmp_path, A):
    p, sub = fake_project(tmp_path / "proj1")
    paid_action(A, p, 4, lambda: sub(4))          # Generate
    assert used(A) == 1
    paid_action(A, p, 1, lambda: sub(1))          # re-roll / second Generate in the same project
    paid_action(A, p, 1, lambda: None)            # a retry whose job never reached Comfy (refunded)
    assert used(A) == 1
    assert A.trial_status("7.7.7.7", "vid1") == {"runs_total": 6, "runs_left": 5, "used_by_you": True, "jobs_per_run": 10}


def test_failed_first_try_then_retry_counts_once(tmp_path, A):
    p, sub = fake_project(tmp_path / "proj2")
    paid_action(A, p, 4, lambda: None)            # upload/submit failed: whole run released
    assert used(A) == 0 and not A.trial_status("7.7.7.7", "vid1")["used_by_you"]
    paid_action(A, p, 4, lambda: sub(4))          # retry
    paid_action(A, p, 4, lambda: sub(4))          # generate again (same project, within the cap)
    assert used(A) == 1


def test_same_dir_is_the_same_run_across_restarts(tmp_path, A):
    from multicam.dub_web import _Shim as DubShim
    from multicam.scene_web import _Shim as SceneShim
    d = tmp_path / "dubs" / "abc"
    assert A.project_key(DubShim(d, "de")) == A.project_key(DubShim(d, "de"))   # translate vs generate
    s = tmp_path / "scenes" / "xyz"
    assert A.project_key(SceneShim(s)) == A.project_key(SceneShim(s))
    A.charge_trial(1, "8.8.8.8", "vid2", A.project_key(DubShim(d, "de")))        # Dub translate
    B = App(A.data, hosted_key_env="TEST_OP_KEY", public=True, hosted_access="judge", max_jobs=0,
            trial_runs=6, trial_jobs_per_run=10)                                  # server restart (resume)
    B.charge_trial(8, "8.8.8.8", "vid2", B.project_key(DubShim(d, "de")))        # Dub generate
    assert used(B) == 1


def test_refreshing_config_never_counts(A):
    from fastapi.testclient import TestClient
    from multicam.server import create_app
    c = TestClient(create_app(A))
    r = c.get("/api/config")
    assert r.cookies.get(VISITOR_COOKIE)                         # visitor cookie issued once
    for _ in range(5):
        assert c.get("/api/config").json()["trial"]["runs_left"] == 6
    assert not A.counter_file.exists() or used(A) == 0
