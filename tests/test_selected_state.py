"""Selected items keep their highlight without :hover (touch has no hover; a sticky tap must not repaint them)."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "multicam" / "web"
CSS = {n: (WEB / n).read_text(encoding="utf-8") for n in ("style.css", "polish.css")}


def _top_level_rules(css):
    depth, start, out = 0, 0, []
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                sel = css[start:i].strip()
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append(sel); start = i + 1
    return out


def test_control_hover_rules_only_apply_on_hover_devices():
    for name, css in CSS.items():
        for sel in _top_level_rules(css):
            if sel.startswith("@media"):
                continue
            for part in sel.split(","):
                if ":hover" in part and "button" in part and not re.search(r"\.(on|chosen):hover", part):
                    raise AssertionError(f"{name}: unguarded button :hover rule {sel!r}")


def test_selected_state_rules_also_cover_hover():
    css = CSS["polish.css"]
    block = css[css.index("selected state: persistent"):]
    for sel in (".modes button.on", ".seg.access-seg button.on", ".seg.engine button.on", ".camsel button.on",
                "button.preset.ang.on", ".choice button.on", ".sc-pick.chosen", ".still.on", ".stage-tabs button.on"):
        assert re.search(re.escape(sel) + r",\s*" + re.escape(sel) + r":hover", block), sel
    assert re.search(r"\.modes button\.on,[^{]*\{border:2px solid var\(--acc\)", block)
    assert ":focus-visible{outline:2px solid var(--acc)" in block
    assert "gradient" not in block and "blur" not in block


def test_default_mode_selected_in_markup():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<button type="button" data-mode="talk" class="on" aria-pressed="true">' in html
