from datetime import datetime, time, timezone

import pytest

from app.core.config import get_settings
from app.models.catalog import Vendor, VendorHour
from app.models.user import UserRole
from app.services.vendor_hours import is_open_at

from tests.images import PNG  # noqa: E402
APPLY = {"name": "Mama Put", "owner_name": "Efua Owusu", "phone": "+233201112222", "address_text": "Madina Market",
         "latitude": 5.67, "longitude": -0.17, "description": "Local food", "opens_at": "08:00:00", "closes_at": "20:00:00"}


@pytest.fixture(autouse=True)
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))


@pytest.fixture()
def applicant(make_user, login):
    u = make_user("owner@example.com", name="Efua Owusu")
    return u, login(u)


@pytest.fixture()
def admin(make_user, login):
    return login(make_user("admin@example.com", UserRole.ADMIN, "Site Admin"))


def upload(c, path="/api/vendor/documents", data=PNG, name="id.png", **form):
    return c.post(path, files={"file": (name, data, "image/png")}, data=form)


def approved_vendor(applicant, admin):
    u, c = applicant
    vid = c.post("/api/vendor/apply", json=APPLY).json()["id"]
    upload(c, doc_type="BUSINESS_REGISTRATION")
    upload(c, doc_type="ID_CARD")                      # restaurants must show an ID and a business registration
    assert admin.post(f"/api/admin/vendors/{vid}/approve").status_code == 200
    return vid


def test_application_is_pending_hidden_and_blocked(client, world, applicant, admin):
    u, c = applicant
    r = c.post("/api/vendor/apply", json=APPLY)
    assert r.status_code == 201 and r.json()["approval_status"] == "PENDING"
    vid = r.json()["id"]
    assert client.get(f"/api/vendors/{vid}").status_code == 404                # invisible to customers
    assert vid not in [v["id"] for v in client.get("/api/vendors").json()]
    assert c.get("/api/vendor/menu").status_code == 403                        # dashboard locked
    assert c.post("/api/vendor/apply", json=APPLY).status_code == 409          # one application only


def test_admin_must_see_the_right_documents_before_approval(applicant, admin, db):
    u, c = applicant
    vid = c.post("/api/vendor/apply", json=APPLY).json()["id"]                       # a restaurant
    r = admin.post(f"/api/admin/vendors/{vid}/approve")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "no_documents"
    assert upload(c, doc_type="ID_CARD").status_code == 201
    r = admin.post(f"/api/admin/vendors/{vid}/approve")                               # an ID alone isn't enough for a restaurant
    assert r.json()["detail"]["code"] == "missing_documents" and "Business registration" in r.json()["detail"]["message"]
    assert upload(c, doc_type="BUSINESS_REGISTRATION").status_code == 201
    assert admin.post(f"/api/admin/vendors/{vid}/approve").status_code == 200
    db.refresh(u)
    assert u.role == UserRole.VENDOR


def test_approval_emails_owner_and_vendor_goes_public(client, world, applicant, admin, emails):
    vid = approved_vendor(applicant, admin)
    assert emails[-1]["subject"].startswith("Your KORA EATS vendor account is approved")
    assert client.get(f"/api/vendors/{vid}").status_code == 200


def test_reject_with_reason_then_reapply(applicant, admin, emails):
    u, c = applicant
    vid = c.post("/api/vendor/apply", json=APPLY).json()["id"]
    assert admin.post(f"/api/admin/vendors/{vid}/reject", json={"reason": "no"}).status_code == 422   # reason required
    assert admin.post(f"/api/admin/vendors/{vid}/reject", json={"reason": "Blurry documents"}).status_code == 200
    assert "Blurry documents" in emails[-1]["html"]
    app = c.get("/api/vendor/application").json()
    assert app["approval_status"] == "REJECTED" and app["rejection_reason"] == "Blurry documents"
    assert c.post("/api/vendor/apply", json={**APPLY, "name": "Mama Put 2"}).json()["approval_status"] == "PENDING"


def test_only_admins_can_use_admin_api(applicant, client):
    u, c = applicant
    assert c.get("/api/admin/vendors").status_code == 403
    assert client.get("/api/admin/vendors").status_code == 401
    assert c.post("/api/admin/vendors/1/approve").status_code == 403


def test_only_customers_can_apply(make_user, login):
    rider = login(make_user("r@example.com", UserRole.RIDER))
    assert rider.post("/api/vendor/apply", json=APPLY).status_code == 403


def test_menu_crud_and_isolation(client, world, applicant, admin, make_user, login):
    vid = approved_vendor(applicant, admin)
    u, c = applicant
    r = c.post("/api/vendor/menu", json={"name": "Kelewele", "price": "12.50", "tags": "snack,spicy", "is_spicy": True})
    assert r.status_code == 201
    item = r.json()
    assert c.post("/api/vendor/menu", json={"name": "Bad", "price": "-5"}).status_code == 422
    assert c.put(f"/api/vendor/menu/{item['id']}", json={"name": "Kelewele", "price": "15.00"}).json()["price"] == 15.0
    names = [i["name"] for i in client.get(f"/api/vendors/{vid}").json()["menu"][0]["items"]]
    assert names == ["Kelewele"]
    c.patch(f"/api/vendor/menu/{item['id']}/availability", params={"is_available": False})
    assert client.get("/api/menu-items", params={"vendor_id": vid}).json() == []     # hidden when unavailable
    # another approved vendor cannot touch this item
    other_user = make_user("owner2@example.com", name="Kojo B")
    oc = login(other_user)
    ovid = oc.post("/api/vendor/apply", json={**APPLY, "name": "Other Place", "business_type": "HOME_COOK"}).json()["id"]
    upload(oc, doc_type="ID_CARD")                    # a home cook only needs an ID
    admin.post(f"/api/admin/vendors/{ovid}/approve")
    oc = login(other_user)  # role changed, new token
    assert oc.put(f"/api/vendor/menu/{item['id']}", json={"name": "Hacked", "price": "1"}).status_code == 404
    assert oc.delete(f"/api/vendor/menu/{item['id']}").status_code == 404
    assert c.delete(f"/api/vendor/menu/{item['id']}").status_code == 200


def test_upload_validation(applicant):
    u, c = applicant
    c.post("/api/vendor/apply", json=APPLY)
    assert upload(c, "/api/vendor/me/logo", PNG).status_code == 200
    r = upload(c, "/api/vendor/me/logo", b"<?php evil ?>", "shell.png")           # fake extension, wrong bytes
    assert r.status_code == 415
    assert upload(c, "/api/vendor/me/logo", b"%PDF-1.4 hi", "x.pdf").status_code == 415   # PDFs aren't images
    assert upload(c, doc_type="NOPE").status_code == 422
    assert upload(c, data=b"%PDF-1.4 fine", name="reg.pdf", doc_type="BUSINESS_REGISTRATION").status_code == 201
    get_settings().max_upload_mb = 0
    try:
        assert upload(c, "/api/vendor/me/cover", PNG).status_code == 413
    finally:
        get_settings().max_upload_mb = 3


def test_documents_are_private_but_admin_can_read(applicant, admin, client):
    u, c = applicant
    vid = c.post("/api/vendor/apply", json=APPLY).json()["id"]
    doc = upload(c, doc_type="ID_CARD").json()
    assert client.get(f"/api/admin/vendors/{vid}/documents/{doc['id']}").status_code == 401
    assert c.get(f"/api/admin/vendors/{vid}/documents/{doc['id']}").status_code == 403
    r = admin.get(f"/api/admin/vendors/{vid}/documents/{doc['id']}")
    assert r.status_code == 200 and r.content == PNG
    assert client.get(f"/uploads/{doc['id']}").status_code == 404                    # not in the public folder


# ---------------------------------------------------------------- opening hours
def vendor_with(hours, is_open=True):
    v = Vendor(is_open=is_open)
    v.hours = [VendorHour(weekday=d, opens_at=o, closes_at=c, is_closed=x) for d, o, c, x in hours]
    return v


def at(weekday_date, hh, mm=0):
    return datetime(2026, 10, weekday_date, hh, mm, tzinfo=timezone.utc)   # 5 Oct 2026 is a Monday (weekday 0)


def test_hours_logic():
    day = vendor_with([(0, time(8), time(20), False)])
    assert is_open_at(day, at(5, 12)) and not is_open_at(day, at(5, 7, 59)) and not is_open_at(day, at(5, 20))
    assert not is_open_at(day, at(6, 12))                                  # Tuesday not listed -> closed
    night = vendor_with([(0, time(18), time(2), False)])
    assert is_open_at(night, at(5, 23)) and is_open_at(night, at(6, 1)) and not is_open_at(night, at(6, 3))
    assert not is_open_at(vendor_with([(0, time(8), time(20), True)]), at(5, 12))   # marked closed
    assert not is_open_at(vendor_with([(0, time(0), time(23, 59), False)], is_open=False), at(5, 12))
    assert is_open_at(vendor_with([]), at(5, 3))                           # no hours = always open


def test_hours_api_roundtrip(applicant, admin, login, db):
    approved_vendor(applicant, admin)
    c = login(applicant[0])                                   # new token now carries role VENDOR
    h = c.get("/api/vendor/hours").json()
    assert len(h) == 7
    r = c.put("/api/vendor/hours", json=[{"weekday": 0, "opens_at": "09:00:00", "closes_at": "17:00:00", "is_closed": False}])
    assert r.status_code == 200 and len(r.json()) == 1
    assert c.put("/api/vendor/hours", json=[{"weekday": 1, "opens_at": "09:00", "closes_at": "17:00"}] * 2).status_code == 422
    assert c.put("/api/vendor/hours", json=[{"weekday": 9, "opens_at": "09:00", "closes_at": "17:00"}]).status_code == 422
    assert c.post("/api/vendor/open", params={"is_open": False}).json()["is_open"] is False
