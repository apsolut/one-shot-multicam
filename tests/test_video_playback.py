"""Never more than one video with sound; results never autoplay; Scene/Dub results fit a phone screen."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "multicam" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
UX = (WEB / "ux.js").read_text(encoding="utf-8")
CSS = (WEB / "polish.css").read_text(encoding="utf-8")
SUB = (WEB / "subpage.js").read_text(encoding="utf-8")


def test_only_muted_videos_carry_autoplay():
    for tag in re.findall(r"<video\b[^>]*>", HTML):
        if re.search(r"\bautoplay\b", tag):
            assert re.search(r"\bmuted\b", tag), tag
    assert re.search(r'<video id="stageVideo"(?![^>]*autoplay)[^>]*>', HTML)  # the stage also shows results


def test_result_never_loaded_into_hidden_stage_or_autoplayed():
    body = UX[UX.index("function stageResult"):UX.index("window.uxStageResult")]
    assert '!r || !wideStage.matches' in body and "sv.autoplay = false" in body and "r.v.pause()" in body
    assert 'window.matchMedia("(min-width: 981px)")' in UX
    # the idle-panel restart only touches the muted example loop
    assert "sv.paused && sv.muted && !sv.dataset.result && !playingFull" in UX


def test_one_sound_at_a_time_and_hidden_tab_pauses():
    for needle in ('document.addEventListener("play", soloSound, true)', 'document.addEventListener("volumechange", soloSound, true)',
                   '"visibilitychange"', "function soundPlaying"):
        assert needle in UX, needle
    assert 'document.addEventListener("play", solo, true)' in SUB and "!o.muted) o.pause()" in SUB


def test_scene_result_fits_phone_width():
    assert "#scResult,#dbResult{grid-template-columns:minmax(0,1fr)" in CSS
    assert re.search(r"#scPlayer,#dbPlayer\{width:100%;max-width:100%;height:auto", CSS)
    assert re.search(r"\.sc-edl\{[^}]*overflow-x:auto", CSS)
