import io
import json
from pathlib import Path

import pytest
import segno
from PIL import Image
from sqlalchemy import select

from app.core import share
from app.core.config import get_settings
from app.models.catalog import ApprovalStatus
from tests.images import jpeg_bytes, png_bytes
from tests.shop.test_notifications import advance_to
from tests.shop.test_pickup import place_pickup

BASE = "http://localhost:8000"


def expected_svg(payload: str) -> bytes:
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="svg", scale=6, border=2, dark="#0f2e22", xmldecl=False, svgns=True, nl=False)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "frontend_base_url", BASE)


# ---------------------------------------------------------------- QR codes
def test_vendor_and_dish_qr_encode_the_share_links(client, world):
    r = client.get(f"/api/vendors/{world.near.id}/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and r.content.startswith(b"<svg")
    assert r.content == expected_svg(f"{BASE}/v/{world.near.id}-near-kitchen")                      # exactly the QR of the public link
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["cache-control"].startswith("public")
    assert client.get(f"/api/menu-items/{world.spicy.id}/qr.svg").content == expected_svg(f"{BASE}/f/{world.spicy.id}-spicy-jollof-chicken")
    assert client.get("/api/vendors/9999/qr.svg").status_code == 404 and client.get("/api/menu-items/9999/qr.svg").status_code == 404


def test_unapproved_vendors_have_no_public_qr(client, world, db):
    world.near.approval_status = ApprovalStatus.PENDING; db.commit()
    assert client.get(f"/api/vendors/{world.near.id}/qr.svg").status_code == 404
    assert client.get(f"/api/menu-items/{world.spicy.id}/qr.svg").status_code == 404


def test_pickup_qr_is_private_and_only_while_active(world, shopper, db, make_user, login, place_order, client):
    o = place_pickup(shopper, world.spicy)
    assert shopper.get(f"/api/orders/{o['id']}/pickup-qr.svg").status_code == 404                        # unpaid: no code to show yet
    advance_to(db, o["id"], __import__("app.models.orders", fromlist=["OrderStatus"]).OrderStatus.CONFIRMED)
    r = shopper.get(f"/api/orders/{o['id']}/pickup-qr.svg")
    assert r.status_code == 200 and r.content == expected_svg(f"KORAEATS|PICKUP|{o['code']}|{o['pickup_code']}") and r.headers["cache-control"] == "no-store"
    assert login(make_user("snoop@example.com")).get(f"/api/orders/{o['id']}/pickup-qr.svg").status_code == 404     # not yours
    assert shopper.get(f"/api/orders/{place_order()['id']}/pickup-qr.svg").status_code == 404                        # delivery orders have no pickup code
    from app.models.orders import OrderStatus as S
    from app.services import order_service as svc
    order = svc.lock_order(db, o["id"])
    for st in (S.PREPARING, S.READY_FOR_PICKUP, S.COLLECTED):
        svc.change_status(db, order, st, "vendor")
    db.commit()
    assert shopper.get(f"/api/orders/{o['id']}/pickup-qr.svg").status_code == 404                                    # collected: the code is spent
    client.cookies.clear()
    assert client.get(f"/api/orders/{o['id']}/pickup-qr.svg").status_code == 401


def test_group_qr_is_for_members_only(world, shopper, make_user, login):
    code = shopper.post("/api/groups", json={"vendor_id": world.near.id}).json()["code"]
    assert shopper.get(f"/api/groups/{code}/qr.svg").content == expected_svg(f"{BASE}/group/{code}")
    assert login(make_user("outsider@example.com")).get(f"/api/groups/{code}/qr.svg").status_code == 404


# ---------------------------------------------------------------- shareable pages
def test_vendor_share_page_has_link_preview_tags(client, world, db):
    world.near.cover_url, world.near.area, world.near.description = "/uploads/cover.jpg", "Osu", "Jollof & grills"
    db.commit()
    r = client.get(f"/v/{world.near.id}-whatever-the-slug-is")
    t = r.text
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and r.headers["cache-control"] == "public, max-age=300"
    assert '<meta property="og:title" content="Near Kitchen on KORA EATS">' in t
    assert f'<meta property="og:image" content="{BASE}/uploads/cover.jpg">' in t                         # absolute URL, as crawlers need
    assert f'<link rel="canonical" href="{BASE}/v/{world.near.id}-near-kitchen">' in t
    assert f'content="0;url=/vendor.html?id={world.near.id}"' in t and 'name="twitter:card"' in t
    assert "Restaurant in Osu." in t and "4.6 (10 reviews)" in t and "Jollof &amp; grills" in t
    assert "content-security-policy-report-only" in r.headers


def test_share_page_falls_back_to_the_app_icon(client, world):
    assert f'content="{BASE}/icons/icon-512.png"' in client.get(f"/v/{world.near.id}").text


def test_food_share_page(client, world, db):
    world.spicy.image_url = "/uploads/jollof.webp"; db.commit()
    t = client.get(f"/f/{world.spicy.id}-x").text
    assert "Spicy Jollof Chicken - GH₵45.00 | KORA EATS" in t and "GH₵45.00 from Near Kitchen" in t
    assert f'content="{BASE}/uploads/jollof.webp"' in t and f"/vendor.html?id={world.near.id}" in t


def test_share_pages_never_run_vendor_supplied_markup(client, world, db):
    world.near.name = '"><script>alert(1)</script>'
    world.near.description = "<img src=x onerror=alert(2)> \"quoted\""
    db.commit()
    for path in (f"/v/{world.near.id}", f"/f/{world.spicy.id}"):
        t = client.get(path).text
        assert "<script>alert" not in t and "<img src=x" not in t        # nothing the vendor typed becomes live HTML
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in t              # it is shown as harmless text instead
    assert "&lt;img src=x onerror=alert(2)&gt; &quot;quoted&quot;" in client.get(f"/v/{world.near.id}").text


@pytest.mark.parametrize("path", ["/v/abc", "/v/99999", "/v/-3", "/f/99999", "/f/xyz", "/v/1; drop table"])
def test_unknown_share_links_are_a_clean_404(client, world, path):
    r = client.get(path)
    assert r.status_code == 404 and "Not found" in r.text and "http-equiv" not in r.text and r.headers["cache-control"] == "no-store"


def test_unapproved_vendors_are_not_shareable(client, world, db):
    world.far.approval_status = ApprovalStatus.REJECTED; db.commit()
    assert client.get(f"/v/{world.far.id}").status_code == 404 and client.get(f"/f/{world.farfood.id}").status_code == 404


def test_sitemap_and_robots(client, world, db):
    world.far.approval_status = ApprovalStatus.PENDING; db.commit()
    sm = client.get("/sitemap.xml")
    assert sm.headers["content-type"].startswith("application/xml") and f"<loc>{BASE}/v/{world.near.id}-near-kitchen</loc>" in sm.text
    assert "far-grill" not in sm.text                                                                    # only approved vendors are listed
    rb = client.get("/robots.txt").text
    assert "Disallow: /api/" in rb and "Disallow: /admin/" in rb and f"Sitemap: {BASE}/sitemap.xml" in rb


def test_apis_expose_share_paths(client, world):
    assert client.get(f"/api/vendors/{world.near.id}").json()["share_path"] == f"/v/{world.near.id}-near-kitchen"
    item = next(i for i in client.get("/api/menu-items").json() if i["id"] == world.spicy.id)
    assert item["share_path"] == f"/f/{world.spicy.id}-spicy-jollof-chicken"
    assert share.parse_ident("12-auntie-amas") == 12 and share.parse_ident("12") == 12 and share.parse_ident("../etc") is None and share.parse_ident("a1") is None


# ---------------------------------------------------------------- uploaded photos are sanitised
@pytest.fixture()
def vendor_c(world, vendor_user, login):
    return login(vendor_user)


def stored(url: str) -> Path:
    return Path(get_settings().upload_dir) / "public" / url.rsplit("/", 1)[1]


def up(c, data, name="p.jpg", path="/api/vendor/me/logo"):
    return c.post(path, files={"file": (name, data, "image/jpeg")})


def test_hidden_location_and_camera_data_are_stripped_and_rotation_applied(vendor_c):
    original = jpeg_bytes((40, 30), with_gps=True, orientation=6)
    assert b"SecretPhoneBrand" in original and Image.open(io.BytesIO(original)).getexif()                   # the upload really carries metadata
    r = up(vendor_c, original)
    assert r.status_code == 200
    raw = stored(r.json()["logo_url"]).read_bytes()
    assert b"SecretPhoneBrand" not in raw and b"Exif" not in raw
    im = Image.open(io.BytesIO(raw))
    assert not im.getexif() and im.size == (30, 40)                                                        # no EXIF/GPS left; portrait orientation baked in


def test_big_photos_are_shrunk_and_formats_preserved(vendor_c):
    big = io.BytesIO(); Image.new("RGB", (4000, 3000), (10, 120, 60)).save(big, "PNG")
    out = stored(up(vendor_c, big.getvalue(), "big.png").json()["logo_url"])
    im = Image.open(out)
    assert out.suffix == ".png" and max(im.size) == 2000 and im.size == (2000, 1500)
    rgba = io.BytesIO(); Image.new("RGBA", (10, 10), (1, 2, 3, 0)).save(rgba, "PNG")
    assert Image.open(stored(up(vendor_c, rgba.getvalue(), "a.png").json()["logo_url"])).mode == "RGBA"       # transparency kept
    web = io.BytesIO(); Image.new("RGB", (20, 20), (5, 5, 5)).save(web, "WEBP")
    assert stored(up(vendor_c, web.getvalue(), "w.webp").json()["logo_url"]).suffix == ".webp"


@pytest.mark.parametrize("name,data", [("fake.png", b"\x89PNG\r\n\x1a\n" + b"garbage" * 20), ("note.jpg", b"just some text"), ("empty.png", b""),
                                       ("html.png", b"<html><script>alert(1)</script></html>")])
def test_disguised_or_corrupt_images_are_refused(vendor_c, name, data):
    assert up(vendor_c, data, name).status_code == 415


def test_decompression_bombs_are_refused(vendor_c):
    bomb = io.BytesIO(); Image.new("1", (20000, 20000)).save(bomb, "PNG")                                  # 400 million pixels, a tiny file
    assert len(bomb.getvalue()) < 3 * 1024 * 1024
    assert up(vendor_c, bomb.getvalue(), "bomb.png").status_code == 415


def test_verification_documents_must_really_be_images_or_pdfs():
    from fastapi import HTTPException, UploadFile
    from app.core.uploads import save_private_document
    good = UploadFile(file=io.BytesIO(png_bytes()), filename="id.png")
    assert save_private_document(good)[0].endswith(".png")
    for bad in (b"\x89PNG\r\n\x1a\n" + b"x" * 30, b"hello"):
        with pytest.raises(HTTPException) as e:
            save_private_document(UploadFile(file=io.BytesIO(bad), filename="id.png"))
        assert e.value.status_code == 415
    assert save_private_document(UploadFile(file=io.BytesIO(b"%PDF-1.4 fine"), filename="r.pdf"))[0].endswith(".pdf")
