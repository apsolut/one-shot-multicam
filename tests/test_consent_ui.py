"""Static checks for the own-face consent box: visible, required, never a silent no-op on Analyze."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "multicam" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
UX = (WEB / "ux.js").read_text(encoding="utf-8")
CSS = (WEB / "polish.css").read_text(encoding="utf-8")


def test_consent_row_markup_is_accessible():
    m = re.search(r'<div class="consent-own" id="consentRow">(.*?)</div>', HTML, re.S)
    assert m, "consent row missing"
    row = m.group(1)
    # the error region sits ABOVE the box, is a role=alert region and is empty until Analyze is tapped
    assert row.index('id="consentErr"') < row.index('id="consent"')
    assert '<p class="consent-err" id="consentErr" role="alert"></p>' in row
    # the label wraps the input (whole row tappable); never pre-ticked; aria-required kept
    lab = re.search(r'<label class="consent" for="consent">(.*?)</label>', row, re.S).group(1)
    inp = re.search(r'<input type="checkbox" id="consent"[^>]*>', lab).group(0)
    assert "checked" not in inp and 'aria-required="true"' in inp and 'aria-describedby="consentErr"' in inp
    assert '<span class="consent-txt">This is my own face, or I have permission from the person in the video.</span>' in lab
    for gone in ("(required)", "consent-req", "<small>", "Tick this to continue"):
        assert gone not in row, gone


def test_analyze_unticked_shows_error_instead_of_doing_nothing():
    # one capture-phase guard ahead of the talk/scene/dub handlers on #start: nothing uploads or starts
    assert 'window.addEventListener("click"' in APP and 'closest?.("#start")' in APP
    assert "stopImmediatePropagation(); consentError(true)" in APP
    assert 'const CONSENT_ERR = "Tick the box to confirm, then tap Analyze.";' in APP
    body = APP[APP.index("function consentError"):APP.index('$("#consent").onchange')]
    for needle in ('"invalid"', 'aria-invalid', '#consentErr', 'class="consent-err-ic" aria-hidden="true">!</span>',
                   '<span class="vh">Error: </span>', 'box.scrollIntoView({block: "nearest"})', "box.focus("):
        assert needle in body, needle
    for gone in ("shake", "smooth", 'block: "center"'):
        assert gone not in body, gone
    assert "shake" not in APP and "animationend" not in body
    # ticking clears the error
    assert 'if ($("#consent").checked) consentError(false)' in APP


def test_consent_never_disables_or_greys_analyze_and_no_hint_before_tap():
    canstart = APP[APP.index("const canStart"):APP.index("function consentError")]
    assert "#consent" not in canstart.split("window.uxSync")[0]
    sync = UX[UX.index("function sync()"):UX.index("window.uxSync = sync")]
    assert "#consent" not in sync and "own" not in sync.replace("own-face", "")
    assert "btn.disabled = !!why;" in sync
    for gone in ("Tick “This is my own face”", "Tick this to continue", "above the button."):
        assert gone not in UX and gone not in APP and gone not in HTML, gone


def test_consent_sits_above_analyze_on_narrow_screens():
    assert 'matchMedia("(max-width: 980px)")' in UX and "go.before(row)" in UX
    assert re.search(r"#step3 #consentRow\{margin:0 0 16px\}", CSS)


def test_consent_css_govuk_large_box_red_error_without_glow():
    block = CSS[CSS.index("own-face consent"):CSS.index("promo code: visible field")]
    assert re.search(r"#consentRow input\[type=checkbox\]\{[^}]*width:40px;height:40px[^}]*border:3px solid #c8c8c8", block)
    assert re.search(r"#consentRow input\[type=checkbox\]:checked\{background-color:#f0fe68;border-color:#f0fe68", block)
    assert "stroke='%23000000'" in block  # black check
    assert re.search(r"#consentRow \.consent\{[^}]*min-height:56px[^}]*color:#fff;font-size:18px;line-height:1\.4", block)
    assert re.search(r"#consentRow\.invalid\{border-left:4px solid #ff5a5a", block)
    assert re.search(r"\.consent-err\{[^}]*color:#ff5a5a;font-size:17px[^}]*font-weight:700", block)
    assert re.search(r"#start\{min-height:56px;font-size:18px;font-weight:700\}", block)
    assert "@media (max-width:980px){#start{width:100%}}" in block
    assert "gradient" not in block and "blur" not in block and "shake" not in block and "@keyframes" not in block
    assert not re.search(r"box-shadow:(?!none)", block)
    assert "shake" not in CSS


def test_promo_field_is_visible_and_checked_inline():
    m = re.search(r'<input id="judge"[^>]*>', HTML)
    assert m and 'type="text"' in m.group(0) and 'type="password"' not in m.group(0)
    for a in ('autocomplete="off"', 'spellcheck="false"', 'autocapitalize="off"', 'aria-describedby="judgeSt"'):
        assert a in m.group(0), a
    assert re.search(r'<input id="key" type="password"', HTML)  # the Comfy key stays masked
    assert '<p class="promo-st" id="judgeSt" aria-live="polite" hidden></p>' in HTML
    for needle in ("Code works · ", "That code isn't valid", "This code is used up", "setTimeout(() => checkPromo(v), 600)",
                   '"X-Judge-Code": v'):
        assert needle in APP, needle
    body = APP[APP.index("function promoStatus"):APP.index('$("#keyToggle")')]
    assert "console." not in body  # codes are never logged
    assert re.search(r"\.promo-st\.bad\{color:#ff5a5a\}", CSS)
    assert re.search(r"#judge\.invalid,#judge\.invalid:focus\{border:2px solid #ff5a5a!important;box-shadow:none!important\}", CSS)


def test_server_promo_compare_is_exact_after_trim():
    # the field keeps case (no forced capitals): the server only strips whitespace before comparing
    srv = (WEB.parent / "server.py").read_text(encoding="utf-8")
    assert "c, hit = code.strip().encode(), None" in srv
