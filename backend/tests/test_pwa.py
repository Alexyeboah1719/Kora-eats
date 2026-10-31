"""PWA checks: manifest, icons, installability basics, and the service worker's caching rules."""
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_manifest_is_served_and_installable(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200 and "manifest+json" in r.headers["content-type"]
    m = r.json()
    assert m["display"] == "standalone" and m["start_url"].startswith("/") and m["scope"] == "/"
    assert m["name"] and m["short_name"] and m["theme_color"] and m["background_color"]
    purposes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert ("192x192", "any") in purposes and ("512x512", "any") in purposes and ("512x512", "maskable") in purposes   # Chrome's install criteria


def test_icons_exist_with_declared_sizes(client):
    for icon in json.loads((FRONTEND / "manifest.webmanifest").read_text())["icons"]:
        w, h = (int(x) for x in icon["sizes"].split("x"))
        assert png_size(FRONTEND / icon["src"].lstrip("/")) == (w, h)
        assert client.get(icon["src"]).status_code == 200
    assert png_size(FRONTEND / "icons/apple-touch-icon.png") == (180, 180)


def test_service_worker_and_offline_page_are_served(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "classify" in sw.text
    assert "You're offline" in client.get("/offline.html").text


def test_every_page_is_pwa_ready():
    pages = list(FRONTEND.rglob("*.html"))
    pages = [p for p in pages if p.name not in ("offline.html", "status.html") and "auth" not in p.parts]
    assert len(pages) >= 15
    for p in pages:
        html = p.read_text()
        assert 'rel="manifest"' in html and 'name="theme-color"' in html and "viewport-fit=cover" in html, p.name
        assert 'rel="apple-touch-icon"' in html, p.name


def test_precached_files_all_exist():
    sw = (FRONTEND / "sw.js").read_text()
    shell = json.loads(sw[sw.index("const SHELL = ") + 14: sw.index("];", sw.index("const SHELL = ")) + 1].replace("\n", " "))
    for path in shell:
        target = FRONTEND / path.lstrip("/")
        assert path == "/" or target.exists(), f"service worker precaches missing file {path}"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed to run the service-worker rules")
def test_service_worker_never_caches_private_data():
    script = f"""
    const {{ classify }} = require({json.dumps(str(FRONTEND / 'sw.js'))});
    const c = (path, method = 'GET', mode = 'cors', host = 'localhost') =>
      classify(new URL('http://' + host + path), method, mode, host === 'localhost');
    const out = {{
      private: ['/api/cart','/api/orders','/api/orders/5/tracking','/api/payments','/api/payments/orders/1/verify','/api/auth/me',
                '/api/me/profile','/api/me/addresses','/api/notifications','/api/admin/orders','/api/rider/available',
                '/api/vendor/orders','/api/ai/preferences','/api/support/tickets','/api/groups/ABC123','/api/ai/feed','/api/me/food-profile',
                '/api/me/food-memory','/api/vendor/intelligence','/api/orders/5/pickup-qr.svg','/api/admin/dispatch'].map(p => c(p)),
      public: ['/api/home','/api/categories','/api/vendors','/api/vendors/3','/api/menu-items','/api/taxonomy','/api/vendor-requirements','/api/ai/options'].map(p => c(p)),
      post: c('/api/cart/items', 'POST'), tiles: c('/19/1/1.png', 'GET', 'cors', 'a.tile.openstreetmap.org'),
      page: c('/orders.html', 'GET', 'navigate'), asset: c('/js/app.js'), cdn: c('/npm/bootstrap.css', 'GET', 'cors', 'cdn.jsdelivr.net'),
      stranger: c('/x.js', 'GET', 'cors', 'evil.example.com'),
    }};
    console.log(JSON.stringify(out));
    """
    res = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert set(res["private"]) == {"network-only"}                                  # nothing private is ever stored
    assert set(res["public"]) == {"network-first-api"}
    assert res["post"] == "network-only" and res["tiles"] == "network-only"
    assert res["page"] == "network-first-page" and res["asset"] == "stale-while-revalidate" and res["cdn"] == "stale-while-revalidate"
    assert res["stranger"] == "network-only"


def test_manifest_has_app_shortcuts_for_the_ai_features():
    m = json.loads((FRONTEND / "manifest.webmanifest").read_text())
    urls = {x["url"] for x in m["shortcuts"]}
    assert {"/decide.html", "/split.html", "/orders.html", "/cart.html"} <= urls
    for sc in m["shortcuts"]:
        assert (FRONTEND / sc["url"].lstrip("/")).exists()
