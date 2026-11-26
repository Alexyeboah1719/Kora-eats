import pytest

from app.services import order_events, tracking_service
from tests.shop.conftest import OSU


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    monkeypatch.setattr(tracking_service, "MIN_PING_SECONDS", 0)


# ---------------------------------------------------------------- delivery zones
def test_zone_check_and_vendor_flag(client, world):
    ok = client.get("/api/zone-check", params={"vendor_id": world.near.id, **OSU}).json()
    assert ok["deliverable"] is True and ok["distance_km"] < 1 and ok["delivery_fee"] == 5.0
    far = client.get("/api/zone-check", params={"vendor_id": world.far.id, **OSU}).json()
    assert far["deliverable"] is False and far["distance_km"] > far["radius_km"]
    cards = {v["name"]: v for v in client.get("/api/vendors", params=OSU).json()}
    assert cards["Near Kitchen"]["in_zone"] is True and cards["Far Grill"]["in_zone"] is False
    assert client.get("/api/vendors").json()[0]["in_zone"] is None            # unknown without a location
    assert client.get("/api/zone-check", params={"vendor_id": 999, **OSU}).status_code == 404


def test_vendor_specific_radius_blocks_checkout(world, shopper, db):
    world.near.delivery_radius_km = 0.05            # 50 metres: the shopper is ~150 m away
    db.commit()
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    r = shopper.post("/api/orders", json={"address_id": shopper.address_id})
    assert r.status_code == 409 and "doesn't deliver that far" in r.json()["detail"]["message"]


def test_vendor_can_set_zone_in_profile(world, vendor_user, login):
    v = login(vendor_user)
    body = {"name": "Near Kitchen", "owner_name": "Ama", "phone": "+233201112222", "address_text": "Osu",
            "latitude": 5.556, "longitude": -0.183, "delivery_radius_km": 6}
    assert v.put("/api/vendor/me", json=body).json()["delivery_radius_km"] == 6
    assert v.put("/api/vendor/me", json={**body, "delivery_radius_km": 500}).status_code == 422


# ---------------------------------------------------------------- rider location + customer tracking
def test_rider_location_requires_online_and_validates(make_rider, login):
    u, r = make_rider(online=False)
    c = login(u)
    assert c.post("/api/rider/location", json={"lat": 5.55, "lng": -0.18}).json()["detail"]["code"] == "offline"
    c.post("/api/rider/online", params={"is_online": True})
    assert c.post("/api/rider/location", json={"lat": 5.55, "lng": -0.18}).json() == {"stored": True}
    assert c.post("/api/rider/location", json={"lat": 999, "lng": -0.18}).status_code == 422


def test_pings_are_throttled(make_rider, login, monkeypatch):
    monkeypatch.setattr(tracking_service, "MIN_PING_SECONDS", 60)
    c = login(make_rider()[0])
    assert c.post("/api/rider/location", json={"lat": 5.55, "lng": -0.18}).json()["stored"] is True
    assert c.post("/api/rider/location", json={"lat": 5.56, "lng": -0.18}).json()["stored"] is False


def test_tracking_is_private_and_only_live_during_delivery(world, shopper, ready_order, make_rider, login, make_user):
    o = ready_order()
    assert shopper.get(f"/api/orders/{o['id']}/tracking").json()["live"] is False        # nobody assigned yet
    rider = login(make_rider()[0])
    rider.post(f"/api/rider/deliveries/{o['id']}/accept")
    rider.post("/api/rider/location", json={"lat": 5.5600, "lng": -0.1900})              # near the vendor, ~1 km from customer
    t = shopper.get(f"/api/orders/{o['id']}/tracking").json()
    assert t["live"] and t["rider"]["lat"] == 5.56 and t["rider"]["stale"] is False
    assert t["eta_minutes"] >= 1 and t["vendor"]["name"] == "Near Kitchen" and len(t["trail"]) == 1
    assert "phone" in t["rider"]
    assert login(make_user("snoop@example.com")).get(f"/api/orders/{o['id']}/tracking").status_code == 404
    base = f"/api/rider/deliveries/{o['id']}"
    rider.post(f"{base}/pickup"); rider.post(f"{base}/out-for-delivery"); rider.post(f"{base}/delivered")
    done = shopper.get(f"/api/orders/{o['id']}/tracking").json()
    assert done["live"] is False and done["rider"] is None                                # location hidden after delivery


def test_rider_nearby_event_fires_once(world, ready_order, make_rider, login):
    seen = []
    handler = lambda db, order, old, new, role, bg: seen.append(role) if role == "rider:nearby" else None
    order_events.subscribe(handler)
    try:
        o = ready_order()
        rider = login(make_rider()[0])
        base = f"/api/rider/deliveries/{o['id']}"
        rider.post(f"{base}/accept"); rider.post(f"{base}/pickup"); rider.post(f"{base}/out-for-delivery")
        rider.post("/api/rider/location", json={"lat": 5.60, "lng": -0.10})             # far away
        assert seen == []
        rider.post("/api/rider/location", json={"lat": 5.5571, "lng": -0.1821})          # ~20 m from the customer
        rider.post("/api/rider/location", json={"lat": 5.5570, "lng": -0.1820})
        assert seen == ["rider:nearby"]                                                   # once only
    finally:
        order_events._handlers.remove(handler)


def test_old_tracking_points_are_pruned(world, ready_order, make_rider, login, db):
    from datetime import timedelta
    from app.core.timeutil import utcnow
    from app.models.riders import DeliveryTracking
    o = ready_order()
    rider = login(make_rider()[0])
    rider.post(f"/api/rider/deliveries/{o['id']}/accept")
    rider.post("/api/rider/location", json={"lat": 5.56, "lng": -0.19})
    row = db.query(DeliveryTracking).one()
    row.recorded_at = utcnow() - timedelta(hours=72); db.commit()
    assert tracking_service.prune_old_tracking(db) == 1
