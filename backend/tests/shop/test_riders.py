import pytest

from app.core.config import get_settings
from app.models.orders import Order, OrderStatus as S
from app.models.user import UserRole

from tests.images import PNG  # noqa: E402
APPLY = {"phone": "+233201112233", "vehicle_type": "MOTORBIKE", "plate_number": "GR-1234-24"}


@pytest.fixture(autouse=True)
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))


@pytest.fixture()
def admin(make_user, login):
    return login(make_user("admin@example.com", UserRole.ADMIN, "Site Admin"))


# ---------------------------------------------------------------- onboarding
def test_rider_application_and_approval(make_user, login, admin, db, emails):
    u = make_user("applicant@example.com", name="Kojo Rider")
    c = login(u)
    r = c.post("/api/rider/apply", json=APPLY)
    assert r.status_code == 201 and r.json()["approval_status"] == "PENDING"
    rid = r.json()["id"]
    assert c.post("/api/rider/apply", json=APPLY).status_code == 409
    assert c.get("/api/rider/available").status_code == 403                                  # locked until approved
    assert admin.post(f"/api/admin/riders/{rid}/approve").json()["detail"]["code"] == "no_documents"
    assert c.post("/api/rider/id-document", files={"file": ("id.png", PNG, "image/png")}).json()["has_document"] is True
    assert c.post("/api/rider/id-document", files={"file": ("x.png", b"not an image", "image/png")}).status_code == 415
    assert admin.get(f"/api/admin/riders/{rid}/document").content == PNG
    assert admin.post(f"/api/admin/riders/{rid}/approve").status_code == 200
    assert "approved as a KORA EATS rider" in emails[-1]["subject"]
    db.refresh(u)
    assert u.role == UserRole.RIDER
    assert login(u).get("/api/rider/me").json()["approval_status"] == "APPROVED"


def test_rider_rejection_and_reapply(make_user, login, admin, emails):
    c = login(make_user("applicant@example.com"))
    rid = c.post("/api/rider/apply", json=APPLY).json()["id"]
    assert admin.post(f"/api/admin/riders/{rid}/reject", json={"reason": "ID unreadable"}).status_code == 200
    assert "ID unreadable" in emails[-1]["html"]
    assert c.get("/api/rider/application").json()["rejection_reason"] == "ID unreadable"
    assert c.post("/api/rider/apply", json={**APPLY, "vehicle_type": "BICYCLE"}).json()["approval_status"] == "PENDING"
    assert c.post("/api/rider/apply", json={**APPLY, "vehicle_type": "ROCKET"}).status_code in (409, 422)


def test_validation_and_role_limits(make_user, login):
    assert login(make_user("v@example.com", UserRole.VENDOR)).post("/api/rider/apply", json=APPLY).status_code == 403
    c = login(make_user("c@example.com"))
    assert c.post("/api/rider/apply", json={**APPLY, "phone": "abc"}).status_code == 422
    assert c.post("/api/rider/apply", json={**APPLY, "vehicle_type": "ROCKET"}).status_code == 422
    assert c.get("/api/rider/deliveries").status_code in (403, 404)


# ---------------------------------------------------------------- finding and taking deliveries
def test_available_lists_only_ready_orders_nearest_first(world, shopper, place_order, ready_order, make_rider, login, db):
    u, rider = make_rider()
    c = login(u)
    place_order()                                              # unpaid -> not offered
    ready = ready_order()
    got = c.get("/api/rider/available").json()
    assert [o["code"] for o in got] == [ready["code"]]
    assert got[0]["earning"] == 4.0 and got[0]["vendor"]["name"] == "Near Kitchen" and got[0]["trip_km"] < 2
    rider.is_online = False; db.commit()
    assert c.get("/api/rider/available").json() == []         # offline riders see nothing


def test_only_one_rider_can_take_a_delivery(world, ready_order, make_rider, login):
    o = ready_order()
    c1, c2 = login(make_rider("r1@example.com")[0]), login(make_rider("r2@example.com")[0])
    assert c1.post(f"/api/rider/deliveries/{o['id']}/accept").json()["status"] == "RIDER_ASSIGNED"
    r = c2.post(f"/api/rider/deliveries/{o['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_taken"
    assert c2.get("/api/rider/available").json() == []
    assert c2.post(f"/api/rider/deliveries/{o['id']}/pickup").status_code == 404        # not their delivery


def test_offline_rider_cannot_accept(ready_order, make_rider, login):
    o = ready_order()
    c = login(make_rider(online=False)[0])
    assert c.post(f"/api/rider/deliveries/{o['id']}/accept").json()["detail"]["code"] == "offline"


def test_full_delivery_flow_and_earnings(world, shopper, ready_order, make_rider, login, db):
    o = ready_order()
    u, rider = make_rider()
    c = login(u)
    base = f"/api/rider/deliveries/{o['id']}"
    assert c.post(f"{base}/delivered").status_code == 404                                # not their delivery yet
    assert c.post(f"{base}/pickup").status_code == 404
    assert c.post(f"{base}/accept").status_code == 200
    assert shopper.get(f"/api/orders/{o['id']}").json()["rider"]["name"] == "Kwesi Rider"   # customer can see the rider
    assert c.post(f"{base}/arrived").status_code == 409
    assert c.post(f"{base}/delivered").status_code == 409
    assert c.post(f"{base}/pickup").json()["status"] == "PICKED_UP"
    assert c.post(f"{base}/out-for-delivery").json()["status"] == "OUT_FOR_DELIVERY"
    assert c.post(f"{base}/arrived").json()["arrived_at"]
    d = c.post(f"{base}/delivered").json()
    assert d["status"] == "DELIVERED" and d["delivered_at"]
    assert db.get(Order, o["id"]).status == S.DELIVERED
    e = c.get("/api/rider/earnings").json()
    assert e["today"] == 4.0 and e["all_time"] == 4.0 and e["deliveries"] == 1
    assert [x["code"] for x in c.get("/api/rider/deliveries", params={"view": "history"}).json()] == [o["code"]]
    assert c.get("/api/rider/deliveries").json() == []


def test_max_two_active_and_cannot_go_offline_mid_delivery(ready_order, make_rider, login):
    orders = [ready_order() for _ in range(3)]
    c = login(make_rider()[0])
    for o in orders[:2]:
        assert c.post(f"/api/rider/deliveries/{o['id']}/accept").status_code == 200
    r = c.post(f"/api/rider/deliveries/{orders[2]['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "too_many_active"
    assert c.post("/api/rider/online", params={"is_online": False}).json()["detail"]["code"] == "active_deliveries"


def test_release_puts_order_back_in_the_pool(ready_order, make_rider, login):
    o = ready_order()
    c1, c2 = login(make_rider("r1@example.com")[0]), login(make_rider("r2@example.com")[0])
    c1.post(f"/api/rider/deliveries/{o['id']}/accept")
    assert c1.post(f"/api/rider/deliveries/{o['id']}/release").status_code == 200
    assert [x["code"] for x in c2.get("/api/rider/available").json()] == [o["code"]]
    assert c2.post(f"/api/rider/deliveries/{o['id']}/accept").status_code == 200


def test_customers_and_vendors_cannot_use_rider_api(shopper, vendor_user, login, ready_order):
    o = ready_order()
    assert shopper.post(f"/api/rider/deliveries/{o['id']}/accept").status_code in (403, 404)
    assert login(vendor_user).get("/api/rider/available").status_code in (403, 404)
