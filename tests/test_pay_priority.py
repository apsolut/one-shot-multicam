"""Which credits pay: an explicit X-Pay-Mode (sent by the web UI) always wins and never falls back; without it
the order is own key > promo code > free trial. A sent key never loses to the trial. No Comfy calls."""
import json
import re
from pathlib import Path

import pytest

from multicam.server import VISITOR_COOKIE, App
from multicam.util import load_json

CODE = "TEST-CODE-0001"
WEB = Path(__file__).resolve().parents[1] / "multicam" / "web"


@pytest.fixture
def A(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OP_KEY", "op-key-not-real")
    monkeypatch.delenv("MULTICAM_JUDGE_CODE", raising=False)
    monkeypatch.setenv("MULTICAM_PROMO_CODES", f"{CODE}:2")
    return App(tmp_path / "d", hosted_key_env="TEST_OP_KEY", public=True, hosted_access="judge", max_jobs=0,
               trial_runs=3, trial_jobs_per_run=10)


def test_default_order_key_beats_promo_and_trial(A):
    pid = A.promo_id(CODE)
    assert A.resolve_key("own", CODE) == ("own", False)          # key + code: the key pays
    assert A.resolve_key("  own ", None) == ("own", False)
    assert A.resolve_key(None, CODE)[1] == "promo:" + pid        # code beats the trial
    assert A.resolve_key("", "")[1] == "trial"                   # trial only when neither is present
    assert A.resolve_key("  ", None)[1] == "trial"


def test_explicit_mode_wins_and_never_falls_back(A):
    pid = A.promo_id(CODE)
    assert A.resolve_key("own", CODE, "key") == ("own", False)
    assert A.resolve_key("own", CODE, "promo")[1] == "promo:" + pid   # explicit promo pick beats a saved key
    assert A.resolve_key("own", CODE, "judge")[1] == "promo:" + pid   # UI segment name works too
    assert A.resolve_key("own", None, "trial")[1] == "trial"          # explicit free run: the key isn't charged
    assert A.resolve_key("own", None, "FREE")[1] == "trial"
    with pytest.raises(PermissionError, match="no key was sent"):
        A.resolve_key(None, None, "key")                               # key chosen but missing: no silent trial
    with pytest.raises(PermissionError, match="no key was sent"):
        A.resolve_key(None, CODE, "key")
    with pytest.raises(PermissionError, match="none was sent"):
        A.resolve_key("own", None, "promo")
    with pytest.raises(PermissionError, match="isn't valid"):
        A.resolve_key(None, "TEST-WRONG-99", "promo")
    with pytest.raises(PermissionError, match="unknown payment mode"):
        A.resolve_key("own", None, "bogus")


def test_trial_mode_without_trial(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OP_KEY", "op-key-not-real")
    B = App(tmp_path / "b", hosted_key_env="TEST_OP_KEY", public=True, hosted_access="judge", max_jobs=0, trial_runs=0)
    with pytest.raises(PermissionError, match="free runs aren't available"):
        B.resolve_key(None, None, "trial")


def _project(A, pid="p1"):
    root = A.data / pid
    (root / "work").mkdir(parents=True)
    (root / "src.mp4").write_bytes(b"")
    (root / "state.json").write_text(json.dumps({"id": pid, "name": "t", "mode": "talk", "source": "src.mp4",
                                                 "stills": {}, "clips": {}, "steps": {}, "busy": None, "jobs": []}))
    return pid


def _ledger(A):
    d = load_json(A.counter_file) if A.counter_file.exists() else {}
    return d.get("promo_jobs", {}), d.get("trial", {}).get("used", 0)


@pytest.fixture
def client(A, monkeypatch):
    from fastapi.testclient import TestClient
    import multicam.comfy as comfy
    from multicam.server import create_app
    paid = []

    def fake_angles(self, p, key):  # stands in for the Qwen job: one "submission", records who paid
        paid.append("operator" if key == "op-key-not-real" else "user:" + key)
        with p.lock:
            p.state.setdefault("prompt_ids", []).append(f"fake{len(paid)}")
            p.save()
    monkeypatch.setattr(App, "_gen_angles", fake_angles)
    monkeypatch.setattr(comfy, "export_workflows", lambda *a, **k: None)
    c = TestClient(create_app(A))
    c.get("/api/config")
    assert c.cookies.get(VISITOR_COOKIE)
    return c, paid


def _angles(c, pid, **hdr):
    import time
    r = c.post(f"/api/projects/{pid}/angles", json={"confirm": True}, headers=hdr)
    for _ in range(100):
        if not c.get(f"/api/projects/{pid}").json().get("busy"):
            break
        time.sleep(0.02)
    return r


def test_endpoint_key_wins_and_promo_counts_only_its_own(A, client):
    c, paid = client
    pid = _project(A)
    assert _angles(c, pid, **{"X-Comfy-Key": "user-key", "X-Judge-Code": CODE}).status_code == 200
    assert paid == ["user:user-key"] and _ledger(A) == ({}, 0)   # nothing on promo or trial
    assert _angles(c, pid, **{"X-Judge-Code": CODE, "X-Pay-Mode": "promo"}).status_code == 200
    assert _angles(c, pid, **{"X-Judge-Code": CODE, "X-Pay-Mode": "promo"}).status_code == 200
    assert _ledger(A) == ({A.promo_id(CODE): 2}, 0)               # promo counter only, trial untouched
    r = _angles(c, pid, **{"X-Judge-Code": CODE, "X-Pay-Mode": "promo"})
    assert r.status_code == 403 and "used up" in r.json()["detail"]   # limit 2 enforced
    assert _ledger(A) == ({A.promo_id(CODE): 2}, 0)
    raw = A.counter_file.read_text()
    assert CODE not in raw                                         # only the hash is stored


def test_endpoint_key_mode_without_key_is_refused_not_trial(A, client):
    c, paid = client
    pid = _project(A)
    r = _angles(c, pid, **{"X-Pay-Mode": "key"})
    assert r.status_code == 403 and "no key was sent" in r.json()["detail"]
    assert paid == [] and _ledger(A) == ({}, 0)
    assert _angles(c, pid, **{"X-Comfy-Key": "user-key", "X-Pay-Mode": "trial"}).status_code == 200
    assert paid == ["operator"] and _ledger(A)[1] == 1            # an explicit free-run pick uses the trial
    assert _angles(c, pid).status_code == 200                     # legacy client, no headers: trial (same run)
    assert _ledger(A)[1] == 1


def test_check_key_promo(A, client):
    c, _ = client
    ok = c.post("/api/check-key", headers={"X-Judge-Code": CODE}).json()
    assert ok["judge"] and ok["judge_jobs_left"] == 2 and ok["judge_jobs_total"] == 2
    assert c.post("/api/check-key", headers={"X-Judge-Code": "TEST-WRONG-99"}).status_code == 403


def test_web_client_sends_the_chosen_mode():
    js = (WEB / "app.js").read_text()
    api = js[js.index("async function api("):js.index("function confirmBox(")]
    assert 'headers["X-Pay-Mode"]' in api                         # the server always learns the explicit choice
    assert 'm === "key" && key()' in api and 'm === "judge" && judge()' in api
    assert "withKey && key()" not in api                          # no more "whatever happens to be stored"
    pm = js[js.index("function payMode()"):js.index("function setPayPick(")]
    order = [pm.index(x) for x in ("payPick()", 'return "key"', 'return "judge"', 'return "free"')]
    assert order == sorted(order)                                 # pick > saved key > promo > free
    assert "with your key —" not in js                            # confirm names the real payer (payLabel)
    for f in ("app.js", "dub.js", "scene.js"):
        src = (WEB / f).read_text()
        assert not re.search(r'confirmBox\("(Spend on Comfy|Generate angle|Re-roll|Generate the scene)', src)
        assert "confirmBox(`Dub into" not in src
    ux = (WEB / "ux.js").read_text()
    assert "setPayPick(a)" in ux and "payMode()" in ux            # switch click persists; chip follows payMode
