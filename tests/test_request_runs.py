"""'Request more runs' mailto: shown wherever the visitor is out of runs; the email never carries a code or key."""
import re
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "multicam" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "polish.css").read_text(encoding="utf-8")
DOCS_CSS = (ROOT / "docs" / "assets" / "polish.css").read_text(encoding="utf-8")


def _fn(name, end):
    return APP[APP.index(name):APP.index(end, APP.index(name))]


def test_mailto_address_subject_and_body_are_url_encoded():
    assert 'const RUNS_MAIL = "multicam@apsolut.dev"' in APP
    href = _fn("function reqRunsHref", "\n}\n")
    assert "mailto:${RUNS_MAIL}?subject=${encodeURIComponent(\"Multicam: request more runs\")}&body=${encodeURIComponent(body)}" in href
    for needle in ("Hi Alek,", "I ran out of runs on Multicam and would love 2 more to keep testing.",
                   "What I want to make: ", "(Context: ${ctx", "Thanks!"):
        assert needle in href, needle
    # the subject encodes cleanly (what the browser builds)
    assert quote("Multicam: request more runs", safe="") == "Multicam%3A%20request%20more%20runs"


def test_email_never_contains_a_code_key_or_server_text():
    href = _fn("function reqRunsHref", "\n}\n")
    for secret in ("judge()", "key()", "JUDGE_LS", "KEY_LS", "X-Judge-Code", "X-Comfy-Key", "e.message", "msg"):
        assert secret not in href, secret
    # the context is always one of the fixed state labels
    ctx = _fn("function runsCtx", "\n}\n")
    assert "return RUNS_CTX." in ctx and "msg}" not in ctx and "${" not in ctx
    labels = re.search(r"const RUNS_CTX = \{(.*?)\};", APP, re.S).group(1)
    for lab in ("promo code used up", "promo code expired / no longer valid", "free runs used", "free demo credits used up"):
        assert lab in labels, lab


def test_button_markup_in_every_out_of_runs_spot():
    # next to Generate (runs-left pill), under the promo status, and at the top of the payment sheet
    assert re.search(r'<span class="runs" id="runsLeft" hidden></span>\s*<a class="btn req-runs" id="reqRunsGo"[^>]*hidden>Request more runs</a>', HTML)
    assert re.search(r'id="judgeSt"[^>]*hidden></p>\s*<a class="btn req-runs" id="reqRunsPromo"[^>]*hidden>Request more runs</a>', HTML)
    assert re.search(r'<div class="req-runs-box" id="reqRunsBox" hidden>.*?<a class="btn req-runs solo" id="reqRunsSheet"', HTML)
    # the small text link inside the payment sheet
    sheet = HTML[HTML.index('id="paySheet"'):HTML.index('id="histSheet"')]
    assert 'Need more runs? <a id="reqRunsMail" href="mailto:multicam@apsolut.dev">Email multicam@apsolut.dev</a>' in sheet


def test_states_that_show_the_button():
    # promo code used up / expired (inline under the field)
    ps = _fn("function promoStatus", "\n}\n")
    assert '#reqRunsPromo' in ps and "used up|expired" in ps
    assert '"This code is used up"' in APP and '"This code has expired"' in APP
    # free trial runs gone (0 of N left) and demo credits used up: banner carries the button
    assert "run it yourself</a>. ${reqRunsBtn(RUNS_CTX.trial)}" in APP
    assert "still work. ${reqRunsBtn(RUNS_CTX.demo)}" in APP
    assert "${reqRunsBtn(RUNS_CTX.demo)}</p>`, \"Paste my key\")" in APP  # the 'use your own key' dialog
    # nothing can pay -> Generate-side + sheet button
    out = _fn("function outNow", "\n}\n")
    assert "!freeAvail()" in out and "judgeLeft === 0" in out and "runsOut" in out
    assert "renderReqRuns();\n  window.uxSync?.();" in APP


def test_api_403_used_up_or_expired_shows_the_button():
    api = _fn("async function api", "\n}\n")
    assert "r.status === 403 && opts.withKey && outOfRuns(e.message)" in api
    assert "runsOut = runsCtx(e.message); renderReqRuns()" in api
    rx = re.search(r"const outOfRuns = msg => /(.*?)/i\.test", APP).group(1)
    for msg in ("this promo code is used up (3/3 jobs). Paste your own Comfy Cloud key",
                "that promo code isn't valid any more. Paste your own Comfy Cloud key",
                "all 20 free runs are used up. Paste your own Comfy Cloud key",
                "you've used your free run (one per visitor).",
                "the public demo budget is used up (40/40 jobs).",
                "your code expired", "0 left"):
        assert re.search(rx.replace("\\/", "/"), msg, re.I), msg
    for msg in ("that promo code isn't valid", "rate limit: try later", "Comfy Cloud API key required"):
        assert not re.search(rx, msg, re.I), msg


def test_css_secondary_outline_or_yellow_solo_44px_no_glow():
    for css in (CSS, DOCS_CSS):
        block = css[css.index("request more runs: mailto"):css.index("selected state: persistent")]
        assert re.search(r"a\.btn\.req-runs\{[^}]*min-height:44px[^}]*border:1px solid var\(--acc\)[^}]*background:transparent", block)
        assert re.search(r"a\.btn\.req-runs\.solo\{background:#f0fe68;border-color:#f0fe68;color:#0a0a0a\}", block)
        assert "gradient" not in block and "blur" not in block
        assert not re.search(r"box-shadow:(?!none)", block)
        assert "a.btn.req-runs[hidden]{display:none}" in block
