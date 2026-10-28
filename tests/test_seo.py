"""SEO surface: robots.txt, sitemap.xml, share image + icons, and the meta tags on the app and gallery pages."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://multicam.apsolut.dev"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from multicam.server import App, create_app
    monkeypatch.delenv("MULTICAM_SITE_URL", raising=False)
    return TestClient(create_app(App(tmp_path / "data")), base_url=SITE)


def test_robots_and_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "User-agent: *" in r.text and "Allow: /" in r.text and f"Sitemap: {SITE}/sitemap.xml" in r.text
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert f"<loc>{SITE}/</loc>" in r.text and f"<loc>{SITE}/gallery/</loc>" in r.text


@pytest.mark.parametrize("path,ctype", [("/og.png", "image/png"), ("/favicon.svg", "image/svg+xml"),
                                        ("/favicon-32.png", "image/png"), ("/apple-touch-icon.png", "image/png"),
                                        ("/favicon.ico", "image/png"), ("/gallery/assets/favicon.svg", "image/svg+xml")])
def test_share_image_and_icons_are_cacheable_images(client, path, ctype):
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(ctype), (path, r.headers)
    assert "max-age" in r.headers.get("cache-control", ""), path


def test_og_image_size():
    from PIL import Image
    f = ROOT / "multicam" / "web" / "og.png"
    assert Image.open(f).size == (1200, 630) and f.stat().st_size < 300_000


def _meta(html, attr, key):
    m = re.search(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)"', html)
    return m and m.group(1)


@pytest.mark.parametrize("page,url", [("multicam/web/index.html", f"{SITE}/"), ("docs/index.html", f"{SITE}/gallery/")])
def test_pages_have_full_meta(page, url):
    html = (ROOT / page).read_text(encoding="utf-8")
    assert re.search(r"<title>[^<]*Multicam[^<]*</title>", html)
    assert f'<link rel="canonical" href="{url}">' in html
    assert _meta(html, "name", "description")
    for k in ("og:type", "og:site_name", "og:title", "og:description", "og:image:alt"):
        assert _meta(html, "property", k), k
    assert _meta(html, "property", "og:url") == url
    assert _meta(html, "property", "og:image") == f"{SITE}/og.png"
    assert _meta(html, "property", "og:image:width") == "1200" and _meta(html, "property", "og:image:height") == "630"
    assert _meta(html, "name", "twitter:card") == "summary_large_image"
    assert _meta(html, "name", "twitter:site") == "@apsolut" and _meta(html, "name", "twitter:creator") == "@apsolut"
    assert _meta(html, "name", "theme-color") == "#0a0a0a"
    assert 'type="image/svg+xml"' in html and 'sizes="32x32"' in html and 'rel="apple-touch-icon"' in html
    assert '<script type="application/ld+json">' in html


def test_app_jsonld():
    import json
    html = (ROOT / "multicam" / "web" / "index.html").read_text(encoding="utf-8")
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
    assert ld["@type"] == "WebApplication" and ld["author"]["name"] == "Aleksandar Perisic"
    assert ld["offers"]["price"] == "0" and "https://github.com/apsolut/one-shot-multicam" in ld["sameAs"]


def test_index_still_served_for_share_hash_routes(client):
    r = client.get("/")  # /#p=<id> deep links are client-side: the fragment never reaches the server
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "app.js" in r.text
    assert client.get("/gallery/").status_code == 200


OLD = "https://one-shot-multicam.fly.dev"


@pytest.fixture()
def old_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from multicam.server import App, create_app
    return TestClient(create_app(App(tmp_path / "data")), base_url=OLD, follow_redirects=False)


@pytest.mark.parametrize("path", ["/", "/gallery/", "/gallery/index.html?x=1&y=2", "/og.png", "/robots.txt",
                                  "/sitemap.xml", "/app.js"])
def test_old_host_301s_to_canonical_domain(old_client, path):
    for method in ("GET", "HEAD"):
        r = old_client.request(method, path)
        assert r.status_code == 301 and r.headers["location"] == f"{SITE}{path}", (method, path, r.headers)


def test_old_host_keeps_api_files_and_non_get(old_client, client):
    assert old_client.get("/api/config").status_code == 200  # Fly health check path + in-flight clients
    assert old_client.get("/files/nope/x.mp4").status_code != 301
    assert old_client.post("/api/projects").status_code != 301
    assert old_client.get("/", headers={"host": "multicam.apsolut.dev"}).status_code == 200
    assert client.get("/").status_code == 200 and client.get("/api/config").status_code == 200


def test_internal_health_check_host_is_not_redirected(tmp_path):
    from fastapi.testclient import TestClient
    from multicam.server import App, create_app
    c = TestClient(create_app(App(tmp_path / "data")), base_url="http://172.19.0.2:8787", follow_redirects=False)
    assert c.get("/api/config").status_code == 200 and c.get("/").status_code == 200


def test_site_url_env_wins(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from multicam.server import App, create_app
    monkeypatch.setenv("MULTICAM_SITE_URL", SITE)
    c = TestClient(create_app(App(tmp_path / "data")), base_url="http://172.19.0.2:8787")
    assert f"Sitemap: {SITE}/sitemap.xml" in c.get("/robots.txt").text
    assert f"<loc>{SITE}/gallery/</loc>" in c.get("/sitemap.xml").text
