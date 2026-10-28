"""docs/ is served both by GitHub Pages (apsolut.github.io/one-shot-multicam/) and by `multicam serve` at /gallery/.

It must be self-contained: no root-absolute asset paths, and the copies in docs/assets/ must match multicam/web
(refresh with `python scripts/sync_pages_assets.py`)."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sync_pages_assets", ROOT / "scripts" / "sync_pages_assets.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def test_docs_assets_in_sync_with_web_sources():
    assert not sync.stale(), f"docs/assets stale, run: python scripts/sync_pages_assets.py ({sync.stale()})"


def test_index_has_no_root_absolute_asset_paths():
    # href="/..." / src="/..." would resolve to apsolut.github.io/ on Pages (404). Only the
    # data-home link (rewritten to the live app on Pages) may use "/".
    bad = [m.group(0) for m in re.finditer(r'<[^>]*\b(?:href|src)="/[^"]*"[^>]*>', INDEX) if "data-home" not in m.group(0)]
    assert not bad, bad


def test_index_references_relative_assets_that_exist():
    refs = re.findall(r'(?:href|src)="(assets/[^"]+)"', INDEX)
    assert {"assets/style.css", "assets/polish.css", "assets/subpage.js", "assets/fonts/InterVariable.woff2"} <= set(refs)
    for r in refs:
        assert (ROOT / "docs" / r).is_file(), r


def test_css_urls_resolve_relative_to_assets():
    for css in ("style.css", "polish.css"):
        for u in re.findall(r'url\(["\']?([^"\')]+)', (ROOT / "docs" / "assets" / css).read_text(encoding="utf-8")):
            if u.startswith("data:"):
                continue
            assert not u.startswith("/"), (css, u)
            assert (ROOT / "docs" / "assets" / u).is_file(), (css, u)


def test_gallery_media_is_relative_and_committed():
    cfg = (ROOT / "docs" / "config.js").read_text(encoding="utf-8")
    media = set(re.findall(r'"(media/[^"]+)"', cfg + INDEX))
    assert media
    for m in media:
        assert (ROOT / "docs" / m).is_file(), m


def test_pages_links_point_at_live_app():
    js = (ROOT / "multicam" / "web" / "subpage.js").read_text(encoding="utf-8")
    assert "https://multicam.apsolut.dev/" in js and "github\\.io" in js and "Try it live" in js
    assert "https://multicam.apsolut.dev/" in INDEX
