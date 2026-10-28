#!/usr/bin/env python3
"""Copy the shared web assets the gallery needs into docs/assets/ so docs/ is self-contained.

docs/index.html is served twice: by GitHub Pages at https://apsolut.github.io/one-shot-multicam/ and by
`multicam serve` at /gallery/. Root-absolute paths (/style.css) only work on the second, so the page
references these copies with relative paths (assets/style.css). Run this after editing any source below;
tests/test_pages_assets.py fails when the copies are stale.

    python scripts/sync_pages_assets.py          # copy
    python scripts/sync_pages_assets.py --check  # exit 1 if any copy is stale
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "multicam" / "web"
DEST = ROOT / "docs" / "assets"
# source (relative to multicam/web) -> same relative path under docs/assets
FILES = ["style.css", "polish.css", "subpage.js", "fonts/InterVariable.woff2"]


def stale() -> list[str]:
    return [f for f in FILES if not (DEST / f).is_file() or (DEST / f).read_bytes() != (WEB / f).read_bytes()]


def sync() -> list[str]:
    changed = stale()
    for f in changed:
        (DEST / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WEB / f, DEST / f)
    return changed


if __name__ == "__main__":
    if "--check" in sys.argv:
        bad = stale()
        print("stale: " + ", ".join(bad) if bad else "docs/assets in sync")
        sys.exit(1 if bad else 0)
    print("updated: " + ", ".join(sync()) if stale() else "docs/assets already in sync")
