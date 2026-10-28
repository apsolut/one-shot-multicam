"""Free trial runs on the hosted key: pool, one run per visitor (IP + cookie), per-run job cap, refunds,
top-up by raising MULTICAM_HOSTED_RUNS. No Comfy calls (no balance floor configured)."""
import pytest

from multicam.server import App


@pytest.fixture
def mk(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OP_KEY", "op-key-not-real")
    monkeypatch.setenv("MULTICAM_JUDGE_CODE", "judge-code-for-tests")

    def make(runs=6, per_run=10, sub="a"):
        return App(tmp_path / sub, hosted_key_env="TEST_OP_KEY", public=True, hosted_access="judge", max_jobs=0,
                   trial_runs=runs, trial_jobs_per_run=per_run, judge_max_jobs=10)
    return make


def test_resolve_key_order(mk):
    A = mk()
    assert A.resolve_key("own", None) == ("own", False)
    assert A.resolve_key(None, None)[1] == "trial"
    assert A.resolve_key(None, "judge-code-for-tests")[1] == "judge"  # judge code still wins over the trial
    with pytest.raises(PermissionError):
        A.resolve_key(None, "wrong-code")
    assert mk(runs=0).trial_enabled is False


def test_pool_and_one_run_per_visitor(mk):
    A = mk(runs=6)
    for i in range(6):
        A.charge_trial(4, f"10.0.0.{i}", f"cookie{i}", f"proj{i}")
    assert A.trial_status("10.0.0.99", "new")["runs_left"] == 0
    with pytest.raises(PermissionError, match="used up"):
        A.charge_trial(4, "10.0.0.99", "new", "projX")
    A2 = mk(runs=6, sub="b")  # a fresh pool
    A2.charge_trial(4, "1.1.1.1", "c1", "p1")
    with pytest.raises(PermissionError, match="one per visitor"):
        A2.charge_trial(4, "1.1.1.1", "other-cookie", "p2")  # same IP, new project
    with pytest.raises(PermissionError, match="one per visitor"):
        A2.charge_trial(4, "2.2.2.2", "c1", "p2")  # same cookie, new IP
    st = A2.trial_status("1.1.1.1", None)
    assert st["used_by_you"] and st["runs_left"] == 5


def test_same_project_continues_until_cap(tmp_path, mk):
    A = mk(runs=6, per_run=10)
    A.charge_trial(1, "3.3.3.3", "c", "dub1")      # Dub translate
    A.charge_trial(8, "3.3.3.3", "c", "dub1")      # Dub generate: same run
    assert A.trial_status("9.9.9.9", None)["runs_left"] == 5
    with pytest.raises(PermissionError, match="covers up to 10"):
        A.charge_trial(2, "3.3.3.3", "c", "dub1")


def test_refund_releases_unused_run(mk):
    A = mk(runs=6)
    A.charge_trial(4, "4.4.4.4", "c", "p")
    A.refund_trial(4, "p")  # nothing reached Comfy (e.g. upload failed)
    st = A.trial_status("4.4.4.4", "c")
    assert st["runs_left"] == 6 and not st["used_by_you"]
    A.charge_trial(4, "4.4.4.4", "c", "p2")  # the visitor can still use their run
    A.refund_trial(1, "p2")                  # partial refund keeps the run
    assert A.trial_status("4.4.4.4", "c")["runs_left"] == 5


def test_top_up_by_raising_the_pool(mk):
    A = mk(runs=6)
    for i in range(6):
        A.charge_trial(4, f"5.0.0.{i}", None, f"q{i}")
    assert A.trial_status("x", None)["runs_left"] == 0
    B = mk(runs=12)  # same data dir / counter file, MULTICAM_HOSTED_RUNS=12
    assert B.trial_status("x", None)["runs_left"] == 6
    assert B.trial_status("5.0.0.0", None)["used_by_you"]  # earlier visitors still used theirs


def test_judge_ledger_is_separate(mk):
    A = mk(runs=6)
    A.charge_hosted(3, "6.6.6.6", judge=True)
    assert A.judge_used() == 3 and A.trial_status("6.6.6.6", None)["runs_left"] == 6
