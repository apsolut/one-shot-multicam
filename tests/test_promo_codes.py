"""Multiple promo codes: legacy MULTICAM_JUDGE_CODE + MULTICAM_PROMO_CODES='CODE:LIMIT,...', each with its own
hashed counter in the ledger, individually revocable, the balance floor still on top. No Comfy calls."""
import json

import pytest

from multicam.server import App

LEGACY = "judge-code-for-tests"
P1, P2, P3 = "MC-AAAA-1111", "MC-BBBB-2222", "MC-CCCC-3333"


@pytest.fixture
def mk(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OP_KEY", "op-key-not-real")
    monkeypatch.setenv("MULTICAM_JUDGE_CODE", LEGACY)
    monkeypatch.setenv("MULTICAM_PROMO_CODES", f"{P1}:10, {P2}:3 ,{P3}")

    def make(**kw):
        return App(tmp_path / "d", hosted_key_env="TEST_OP_KEY", public=True, hosted_access="judge", max_jobs=0,
                   trial_runs=6, trial_jobs_per_run=10, judge_max_jobs=kw.pop("judge_max_jobs", 10), **kw)
    return make


def test_parse_multi_codes(mk, monkeypatch):
    A = mk(judge_max_jobs=7)
    codes = A.promo_codes()
    assert codes["judge"] == (LEGACY, 7)
    assert codes[A.promo_id(P1)] == (P1, 10) and codes[A.promo_id(P2)] == (P2, 3)
    assert codes[A.promo_id(P3)] == (P3, 7)  # no ':LIMIT' -> judge_max_jobs
    monkeypatch.setenv("MULTICAM_PROMO_CODES", "short:5,MC-DDDD-4444:abc,MC-EEEE-5555:0,,MC-FFFF-6666:2")
    assert set(A.promo_codes()) == {"judge", A.promo_id("MC-FFFF-6666")}  # bad/short/zero entries ignored
    monkeypatch.delenv("MULTICAM_JUDGE_CODE")
    assert A.judge_enabled and set(A.promo_codes()) == {A.promo_id("MC-FFFF-6666")}  # works without legacy code
    monkeypatch.delenv("MULTICAM_PROMO_CODES")
    assert not A.judge_enabled


def test_resolve_and_per_code_counting(mk):
    A = mk()
    assert A.resolve_key(None, LEGACY)[1] == "judge"
    assert A.resolve_key(None, f"  {P1} ")[1] == "promo:" + A.promo_id(P1)
    with pytest.raises(PermissionError):
        A.resolve_key(None, "MC-ZZZZ-9999")
    A.charge_hosted(2, "1.1.1.1", judge=A.promo_id(P1))
    A.charge_hosted(1, "1.1.1.1", judge="judge")
    assert A.promo_used(A.promo_id(P1)) == 2 and A.promo_used(A.promo_id(P2)) == 0 and A.judge_used() == 1
    A.refund_hosted(1, "1.1.1.1", judge=A.promo_id(P1))
    assert A.promo_used(A.promo_id(P1)) == 1
    raw = A.counter_file.read_text()
    assert P1 not in raw and A.promo_id(P1) in raw  # only the hash is stored
    assert json.loads(raw)["jobs"] == 0  # promo jobs don't touch the open-demo counter


def test_exhausted_code(mk):
    A = mk()
    pid = A.promo_id(P2)
    A.charge_hosted(3, "2.2.2.2", judge=pid)
    with pytest.raises(PermissionError, match="used up"):
        A.charge_hosted(1, "2.2.2.2", judge=pid)
    A.charge_hosted(1, "2.2.2.2", judge=A.promo_id(P1))  # other codes unaffected


def test_removed_code_is_revoked(mk, monkeypatch):
    A = mk()
    pid = A.promo_id(P1)
    A.charge_hosted(4, "3.3.3.3", judge=pid)
    monkeypatch.setenv("MULTICAM_PROMO_CODES", f"{P2}:3")
    assert not A.judge_ok(P1)
    with pytest.raises(PermissionError):
        A.resolve_key(None, P1)
    with pytest.raises(PermissionError):
        A.charge_hosted(1, "3.3.3.3", judge=pid)  # an in-flight id of a revoked code can't spend either
    assert A.judge_ok(P2) and A.judge_ok(LEGACY)


def test_floor_applies_to_promo_codes(mk, monkeypatch):
    A = mk(hosted_floor_usd=28.15)
    monkeypatch.setattr(A, "_floor_ok", lambda n: False)
    with pytest.raises(PermissionError, match="budget"):
        A.charge_hosted(1, "4.4.4.4", judge=A.promo_id(P1))
    assert A.promo_used(A.promo_id(P1)) == 0


def test_check_key_endpoint_per_code(mk):
    from fastapi.testclient import TestClient
    from multicam.server import create_app
    A = mk()
    A.charge_hosted(2, "5.5.5.5", judge=A.promo_id(P2))
    c = TestClient(create_app(A))
    r = c.post("/api/check-key", headers={"X-Judge-Code": P2}).json()
    assert r["judge_jobs_left"] == 1 and r["judge_jobs_total"] == 3
    r = c.post("/api/check-key", headers={"X-Judge-Code": P1}).json()
    assert r["judge_jobs_left"] == 10
    assert c.post("/api/check-key", headers={"X-Judge-Code": LEGACY}).json()["judge_jobs_left"] == 10
    assert c.post("/api/check-key", headers={"X-Judge-Code": "MC-NOPE-0000"}).status_code == 403
    assert c.get("/api/config").json()["judge_code"] is True
