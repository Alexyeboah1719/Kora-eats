"""One continuous story through the real HTTP API: a home cook joins, a customer finds her food with the AI, orders it for delivery and
for pickup, pays, the rider delivers, everyone reviews and sees their dashboards. If any link in this chain breaks, this test says so."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.ai import RecommendationEvent
from app.models.user import UserRole
from tests.images import PNG
from tests.shop.conftest import OSU
from tests.shop.test_notifications import titles
from tests.shop.test_payments import pay, simulate


@pytest.fixture(autouse=True)
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))


def test_the_whole_journey(client, make_user, login, make_rider, db):
    # ---- 1. A home cook applies; an admin verifies her; she builds her menu
    cook_user = make_user("efua@example.com", name="Efua Owusu")
    admin = login(make_user("admin@example.com", UserRole.ADMIN, "Site Admin"))
    cook = login(cook_user)
    app = cook.post("/api/vendor/apply", json={"name": "Efua's Home Kitchen", "owner_name": "Efua Owusu", "phone": "024 123 4567", "address_text": "Osu, Accra",
                                               "latitude": 5.5565, "longitude": -0.1825, "business_type": "HOME_COOK", "region": "Greater Accra", "area": "Osu",
                                               "opens_at": "00:00:00", "closes_at": "23:59:00", "avg_prep_minutes": 15})
    assert app.status_code == 201, app.text
    assert admin.post(f"/api/admin/vendors/{app.json()['id']}/approve").json()["detail"]["code"] == "no_documents"
    assert cook.post("/api/vendor/documents", files={"file": ("id.png", PNG, "image/png")}, data={"doc_type": "ID_CARD"}).status_code == 201
    assert admin.post(f"/api/admin/vendors/{app.json()['id']}/approve").status_code == 200                      # a home cook needs only an ID
    cook = login(cook_user)                                                                                     # role changed: sign in again
    for body in ({"name": "Jollof & Chicken", "price": "40.00", "traits": ["filling"], "meal_types": ["lunch", "dinner"], "dietary": ["halal"], "spice_level": 1, "prep_minutes": 15, "tags": "chicken,rice"},
                 {"name": "Sobolo", "price": "8.00", "traits": ["light", "sweet"], "meal_types": ["drink"], "dietary": ["vegan"], "prep_minutes": 3}):
        assert cook.post("/api/vendor/menu", json=body).status_code == 201

    # ---- 2. A customer finds it with the AI, checking the budget includes delivery
    kofi_user = make_user("kofi@example.com", name="Kofi Mensah")
    kofi = login(kofi_user)
    addr = kofi.post("/api/me/addresses", json={"label": "Home", "address_line": "12 Oxford St", "city": "Accra", "latitude": 5.5570, "longitude": -0.1820, "is_default": True}).json()
    kofi.put("/api/me/profile", json={"full_name": "Kofi Mensah", "phone": "0244 000 111"})
    found = kofi.post("/api/ai/afford", json={"budget": "50", **OSU}).json()
    mine = next(x for x in found["results"] if x["item"]["name"] == "Jollof & Chicken")
    assert mine["total_cost"] <= 50 and mine["why"] and mine["vendor"]["business_label"] == "Home cook"
    dish_id = mine["item"]["id"]

    # ---- 3. Order for DELIVERY and pay
    kofi.post("/api/cart/items", json={"menu_item_id": dish_id, "quantity": 1})
    cart = kofi.get("/api/cart", params={"address_id": addr["id"]}).json()
    assert cart["options"]["pickup"]["total"] < cart["options"]["delivery"]["total"] and cart["options"]["pickup_message"].startswith("Pickup saves")
    order = kofi.post("/api/orders", json={"address_id": addr["id"]}).json()
    assert order["status"] == "PENDING_PAYMENT" and order["code"].startswith("KE-") and order["fulfillment_type"] == "DELIVERY"
    ref = pay(kofi, order["id"]).json()["reference"]
    assert simulate(kofi, ref).status_code in (302, 303, 200)
    assert kofi.get(f"/api/payments/orders/{order['id']}/verify").json()["status"] == "PAID"

    # ---- 4. The cook prepares it; a rider close by is told first and delivers
    rider_user, rider = make_rider("rider@example.com", name="Kwesi Rider")
    login(rider_user).post("/api/rider/location", json={"lat": 5.5575, "lng": -0.1825})
    for st in ("CONFIRMED", "PREPARING", "READY_FOR_PICKUP"):
        assert cook.post(f"/api/vendor/orders/{order['id']}/status", json={"status": st}).status_code == 200, st
    rider_c = login(rider_user)
    assert "Recommended for you" in titles(db, rider_user)
    listing = rider_c.get("/api/rider/available").json()
    assert listing[0]["recommended"] is True and listing[0]["smart"]["delivery_eta_minutes"] >= 1
    for step in ("accept", "pickup", "out-for-delivery", "arrived", "delivered"):
        assert rider_c.post(f"/api/rider/deliveries/{order['id']}/{step}").status_code == 200, step
    assert kofi.get(f"/api/orders/{order['id']}").json()["status"] == "DELIVERED"
    assert rider_c.get("/api/rider/earnings").json()["all_time"] > 0

    # ---- 5. Review it
    assert kofi.post("/api/reviews", json={"order_id": order["id"], "food_rating": 5, "vendor_rating": 5, "comment": "Delicious, still hot!"}).status_code == 201
    rv = cook.get("/api/vendor/reviews").json()
    assert rv["count"] == 1 and rv["average"] == 5.0 and rv["reviews"][0]["comment"] == "Delicious, still hot!"

    # ---- 6. A second order, for PICKUP with the QR code
    kofi.post("/api/cart/items", json={"menu_item_id": dish_id, "quantity": 2})
    pk = kofi.post("/api/orders", json={"fulfillment_type": "PICKUP"}).json()
    assert pk["delivery_fee"] == 0 and len(pk["pickup_code"]) == 6
    simulate(kofi, pay(kofi, pk["id"], key="pickup-pay-0001").json()["reference"])
    assert kofi.get(f"/api/payments/orders/{pk['id']}/verify").json()["status"] == "PAID"
    for st in ("CONFIRMED", "PREPARING", "READY_FOR_PICKUP"):
        cook.post(f"/api/vendor/orders/{pk['id']}/status", json={"status": st})
    qr = kofi.get(f"/api/orders/{pk['id']}/pickup-qr.svg")
    assert qr.status_code == 200 and qr.content.startswith(b"<svg")
    assert cook.post(f"/api/vendor/orders/{pk['id']}/collect", json={"code": "WRONG1"}).status_code == 422
    assert cook.post(f"/api/vendor/orders/{pk['id']}/collect", json={"code": pk["pickup_code"]}).json()["status"] == "COLLECTED"

    # ---- 7. Everyone's dashboards tell the truth
    intel = cook.get("/api/vendor/intelligence").json()
    assert intel["confidence"]["level"] == "insufficient" and "Not enough history" in intel["confidence"]["message"]       # honest: 2 orders is not a forecast
    ai = admin.get("/api/admin/ai/overview").json()
    assert ai["totals"]["impressions"] >= 1 and ai["totals"]["orders_from_recommendations"] >= 1                         # the learning loop saw the AI lead to an order
    assert db.scalar(select(func.count()).select_from(RecommendationEvent).where(RecommendationEvent.kind == "order")) >= 1
    assert admin.get("/api/admin/dispatch").json()["orders"] == []                                                          # nothing is left waiting
    share = client.get(f"/v/{app.json()['id']}")
    assert share.status_code == 200 and "Home Kitchen on KORA EATS" in share.text                                           # and the vendor's page is shareable
