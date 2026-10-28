"""Static checks: too-big / too-long clips are rejected on pick with a persistent inline error, and finished
steps show the accent check mark."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "multicam" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "polish.css").read_text(encoding="utf-8")


def _set_file():
    return APP[APP.index("function setFile(f)"):APP.index('$$("#mode button").forEach(b => b.onclick')]


def test_file_error_region_in_step1_above_the_drop_zone():
    step1 = HTML[HTML.index('id="step1"'):HTML.index('id="step2"')]
    assert '<p class="file-err" id="fileErr" role="alert"></p>' in step1
    assert step1.index('id="fileErr"') < step1.index('id="drop"')


def test_limits_come_from_server_config_and_are_checked_on_pick():
    body = _set_file()
    assert "cfg.max_upload_mb && f.size > cfg.max_upload_mb * 1048576" in body
    assert "cfg.max_upload_s && d > cfg.max_upload_s + 0.5" in body
    assert "rejectFile(...tooBigMsg(" in body and "rejectFile(...tooLongMsg(" in body
    # the old vanishing toast is gone (the inline message replaces it)
    assert "Max ${cfg.max_upload_mb} MB here" not in APP
    assert "over the ${cfg.max_upload_s}s limit" not in APP


def test_rejected_file_leaves_no_stale_state():
    rej = APP[APP.index("function rejectFile"):APP.index("const tooBigMsg")]
    for needle in ("file = null", "fileDur = null", "box.hidden = true", 'box.innerHTML = ""',
                   "URL.revokeObjectURL", '$(s).value = ""', "fileError(title, help)", "canStart()"):
        assert needle in rej, needle


def test_messages_are_plain_and_clear_on_a_good_pick():
    assert "This video is ${num(mb, cfg.max_upload_mb)} MB. The limit is ${cfg.max_upload_mb} MB." in APP
    assert "or set your camera to 1080p." in APP
    assert "This video is ${num(s, cfg.max_upload_s)} seconds. The limit is ${cfg.max_upload_s} seconds." in APP
    body = _set_file()
    assert body.index('fileError("")') < body.index("file = f;")
    fe = APP[APP.index("function fileError"):APP.index("function rejectFile")]
    assert '<span class="vh">Error: </span>' in fe and "esc(title)" in fe and "esc(help)" in fe


def test_error_style_solid_red_no_glow():
    m = re.search(r"\.file-err\{([^}]*)\}", CSS)
    assert m and "#ff5a5a" in m.group(1) and "shadow" not in m.group(1)
    assert ".file-err:empty{display:none}" in CSS


def test_done_step_check_mark_uses_accent():
    rules = re.findall(r"\.step\[data-state=done\] \.step-n\{([^}]*)\}", CSS)
    assert rules and "background:var(--acc)" in rules[-1] and "color:var(--acc-ink)" in rules[-1]


def test_docs_polish_css_mirrors_web():
    assert (ROOT / "docs" / "assets" / "polish.css").read_text(encoding="utf-8") == CSS


def test_upload_hint_has_no_stale_range():
    assert "10–60 s" not in HTML
    assert '<p class="step-sub" id="dropHint">Vertical selfie video.</p>' in HTML
