from datetime import timedelta

import pytest

from app.core.timeutil import utcnow
from app.models.ai import CustomerPreference, RecommendationEvent
from app.models.user import UserRole
from tests.shop.test_dispatch import C_NEAR, put, raw_order


@pytest.fixture()
def admin(make_user, login):
    return login(make_user("ai-admin@example.com", UserRole.ADMIN, "AI Admin"))


def ev(db, user, surface, kind, item, vendor, ago_days=1):
    db.add(RecommendationEvent(user_id=user.id, surface=surface, kind=kind, menu_item_id=item.id, vendor_id=vendor.id, rank=1, created_at=utcnow() - timedelta(days=ago_days)))


def test_overview_aggregates_the_learning_loop(admin, world, db, make_user):
    u = make_user("c@example.com")
    for _ in range(10):
        ev(db, u, "feed", "impression", world.spicy, world.near)
    for _ in range(4):
        ev(db, u, "feed", "click", world.spicy, world.near)
    ev(db, u, "feed", "add_to_cart", world.spicy, world.near); ev(db, u, "feed", "order", world.spicy, world.near)
    for _ in range(5):
        ev(db, u, "afford", "impression", world.plain, world.near)
    ev(db, u, "afford", "order", world.plain, world.near)
    ev(db, u, "feed", "impression", world.spicy, world.near, ago_days=90)                       # outside the window: ignored
    db.commit()
    r = admin.get("/api/admin/ai/overview", params={"days": 30}).json()
    by = {s["surface"]: s for s in r["surfaces"]}
    assert (by["feed"]["impressions"], by["feed"]["clicks"], by["feed"]["add_to_cart"], by["feed"]["orders"]) == (10, 4, 1, 1)
    assert by["feed"]["click_rate"] == 40.0 and by["feed"]["add_rate"] == 10.0 and by["feed"]["conversion_rate"] == 10.0
    assert by["afford"]["conversion_rate"] == 20.0 and by["craving"]["conversion_rate"] is None            # no data: not a fake 0%
    assert r["totals"] == {"impressions": 15, "orders_from_recommendations": 2, "conversion_rate": 13.3}
    assert r["top_dishes"][0]["dish"] in ("Spicy Jollof Chicken", "Plain Rice") and r["top_dishes"][0]["orders"] == 1
    assert r["ranking_weights"]["craving"] > 0 and "Aggregated and anonymous" in r["note"]
    assert "c@example.com" not in str(r) and "user_id" not in str(r)                                       # nothing identifies a customer


def test_overview_reports_privacy_forecast_and_dispatch(admin, world, db, make_user, make_rider):
    a, b = make_user("p1@example.com"), make_user("p2@example.com")
    db.add_all([CustomerPreference(user_id=a.id, budget_samples=0, personalization_enabled=False, dietary=["halal"], avoid_allergens=["peanuts"]),
                CustomerPreference(user_id=b.id, budget_samples=0, memory_reset_at=utcnow())])
    r1 = make_rider("rr@example.com")[1]; put(r1, 5.558, -0.183)
    cust = make_user("cc@example.com")
    raw_order(db, world.near, cust, C_NEAR, ready_ago=12); raw_order(db, world.near, cust, C_NEAR, ready_ago=2); db.commit()
    r = admin.get("/api/admin/ai/overview").json()
    assert r["customer_controls"] == {"customers_with_a_food_profile": 2, "personalisation_switched_off": 1, "with_dietary_needs": 1, "avoiding_allergens": 1, "who_forgot_their_history": 1}
    assert r["forecast_coverage"]["approved_vendors"] == 2 and r["forecast_coverage"]["insufficient"] == 2                   # no order history yet
    d = r["dispatch"]
    assert d["orders_waiting_for_a_rider"] == 2 and d["urgent"] == 1 and d["average_wait_minutes"] == 7.0 and d["riders_online"] == 1 and d["riders_online_with_location"] == 1


def test_overview_is_admin_only_and_validated(admin, shopper, client):
    assert admin.get("/api/admin/ai/overview", params={"days": 0}).status_code == 422
    assert admin.get("/api/admin/ai/overview", params={"days": 400}).status_code == 422
    assert shopper.get("/api/admin/ai/overview").status_code == 403 and client.get("/api/admin/ai/overview").status_code == 401
