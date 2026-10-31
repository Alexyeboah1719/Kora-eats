from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.ai import RecommendationEvent
from app.models.catalog import MenuItem
from app.models.orders import OrderStatus as S
from app.services import settings_service
from app.services.recommend import engine, events
from app.services.recommend.scoring import weights
from tests.shop.conftest import OSU
from tests.shop.test_notifications import advance_to

AT_8AM = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
AT_8PM = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)
AT_NOON = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """Recommendations depend on the time of day, so every test starts at a fixed lunchtime (tests may override it)."""
    monkeypatch.setattr(engine, "utcnow", lambda: AT_NOON)


@pytest.fixture()
def rec(world, db):
    """Gives the shared 'world' dishes the structured attributes the engine reads."""
    w = world
    w.spicy.spice_level, w.spicy.traits, w.spicy.meal_types, w.spicy.dietary = 2, ["filling", "high_protein"], ["lunch", "dinner"], ["halal"]
    w.plain.traits, w.plain.meal_types, w.plain.dietary = [], ["lunch"], ["vegetarian"]
    w.cola.traits, w.cola.meal_types, w.cola.dietary = ["light", "sweet"], ["drink"], ["vegan"]
    w.farfood.traits, w.farfood.meal_types = ["filling"], ["lunch", "dinner"]
    db.commit()
    return w


def post(c, path, **body):
    return c.post(path, json=body)


def names(r):
    return [x["item"]["name"] for x in r.json()["results"]]


# ---------------------------------------------------------------- What Can I Afford?
def test_budget_is_the_total_including_delivery(client, rec):
    r = post(client, "/api/ai/afford", budget="50", fulfillment="DELIVERY", **OSU).json()
    got = {x["item"]["name"]: x for x in r["results"]}
    assert "Spicy Jollof Chicken" in got and got["Spicy Jollof Chicken"]["total_cost"] == 50.0      # 45 food + 5 delivery = exactly the budget
    r = post(client, "/api/ai/afford", budget="49", fulfillment="DELIVERY", **OSU).json()
    assert "Spicy Jollof Chicken" not in [x["item"]["name"] for x in r["results"]]                  # GH₵45 + GH₵5 > GH₵49
    assert all(x["total_cost"] <= 49 for x in r["results"]) and all(x["fulfillment"] == "DELIVERY" for x in r["results"])


def test_best_mode_suggests_pickup_when_it_is_cheaper_and_says_so(client, rec):
    r = post(client, "/api/ai/afford", budget="46", **OSU).json()                                    # BEST by default
    spicy = next(x for x in r["results"] if x["item"]["name"] == "Spicy Jollof Chicken")
    assert spicy["fulfillment"] == "PICKUP" and spicy["total_cost"] == 45.0                           # only pickup fits GH₵46
    assert spicy["pickup_message"] == "Pickup saves GH₵5.00 and your meal will be ready in 20 minutes."
    assert spicy["options"]["delivery"]["total"] == 50.0 and spicy["options"]["pickup"]["ready_minutes"] == 20
    assert "Pickup saves GH₵5.00" in spicy["why"]


def test_explanations_state_budget_distance_and_time(client, rec):
    r = post(client, "/api/ai/afford", budget="60", fulfillment="DELIVERY", **OSU).json()
    top = next(x for x in r["results"] if x["item"]["name"] == "Spicy Jollof Chicken")
    why = " | ".join(top["why"])
    assert "Within your GH₵60 budget (GH₵50.00 with delivery)" in why
    assert f"{top['item']['distance_km']:.1f} km away" in why
    assert "Delivery under" in why and "minutes" in why and top["match_score"] > 0
    assert len(top["why"]) <= 6


def test_without_a_location_delivery_cost_is_flagged_not_guessed(client, rec):
    r = post(client, "/api/ai/afford", budget="30", fulfillment="DELIVERY").json()
    assert r["note"].startswith("Set your delivery location") and r["results"]
    assert all(x["fee_unknown"] for x in r["results"])
    pk = post(client, "/api/ai/afford", budget="30", fulfillment="PICKUP").json()
    assert "note" not in pk and not any(x["fee_unknown"] for x in pk["results"])


def test_impossible_budget_gets_an_honest_hint(client, rec):
    r = post(client, "/api/ai/afford", budget="5", **OSU).json()
    assert r["results"] == [] and "couldn't find" in r["headline"]
    assert "Sobolo" in r["hint"] and "GH₵10.00" in r["hint"]                                          # the real cheapest option, from the database


def test_vendors_that_cannot_deliver_here_are_pickup_only(client, rec):
    d = post(client, "/api/ai/afford", budget="100", fulfillment="DELIVERY", **OSU).json()
    assert "Far Chicken Rice" not in [x["item"]["name"] for x in d["results"]]                         # ~21 km, outside its delivery zone
    best = post(client, "/api/ai/afford", budget="100", fulfillment="BEST", **OSU).json()
    far = next(x for x in best["results"] if x["item"]["name"] == "Far Chicken Rice")
    assert far["fulfillment"] == "PICKUP" and far["options"]["delivery"] is None


def test_unavailable_dishes_and_closed_vendors_never_appear(client, rec, db):
    assert "Sold Out Special" not in names(post(client, "/api/ai/afford", budget="500", **OSU))
    rec.near.is_open = False; db.commit()
    assert {x["vendor"]["name"] for x in post(client, "/api/ai/afford", budget="500", **OSU).json()["results"]} == {"Far Grill"}


@pytest.mark.parametrize("body", [{"budget": "0"}, {"budget": "-5"}, {"budget": "5001"}, {"budget": "abc"}, {"budget": "20", "limit": 0},
                                  {"budget": "20", "fulfillment": "TELEPORT"}, {"budget": "20", "meal_type": "brunch"}, {"budget": "20", "dietary": ["keto"]},
                                  {"budget": "20", "cravings": ["tasty"]}])
def test_afford_validates_input(client, rec, body):
    assert client.post("/api/ai/afford", json=body).status_code == 422


# ---------------------------------------------------------------- combos
def test_combos_fit_the_budget_and_share_one_delivery_fee(client, rec):
    r = post(client, "/api/ai/afford", budget="70", fulfillment="DELIVERY", **OSU).json()
    assert r["combos"], "a main + drink from one vendor fits GH₵70 with delivery"
    for c in r["combos"]:
        roles = [i["role"] for i in c["items"]]
        assert roles.count("main") == 1 and len(set(roles)) == len(roles) and c["total"] <= 70
        assert c["total"] == c["food_total"] + c["delivery_fee"] and round(c["left_over"], 2) == round(70 - c["total"], 2)
        assert any("One delivery fee" in w for w in c["why"])
    assert len({c["vendor"]["id"] for c in r["combos"]}) == len(r["combos"])                            # one combo per vendor
    assert post(client, "/api/ai/afford", budget="12", fulfillment="DELIVERY", **OSU).json()["combos"] == []


# ---------------------------------------------------------------- Craving Diagnosis
def test_cravings_filter_and_explain(client, rec):
    r = post(client, "/api/ai/craving", cravings=["spicy"], **OSU).json()
    assert [x["item"]["name"] for x in r["results"]] == ["Spicy Jollof Chicken"]
    assert any("Matches your craving: spicy" in w for w in r["results"][0]["why"])
    sweet = post(client, "/api/ai/craving", cravings=["sweet"], **OSU).json()
    assert [x["item"]["name"] for x in sweet["results"]] == ["Sobolo"]
    assert post(client, "/api/ai/craving", cravings=["light", "sweet"], **OSU).json()["results"][0]["item"]["name"] == "Sobolo"
    filling = {x["item"]["name"] for x in post(client, "/api/ai/craving", cravings=["filling"], **OSU).json()["results"]}
    assert filling == {"Spicy Jollof Chicken", "Far Chicken Rice"}


def test_modifier_cravings_rank_without_excluding(client, rec):
    cheap = post(client, "/api/ai/craving", cravings=["cheap"], **OSU).json()["results"]
    assert {"Plain Rice", "Sobolo"} <= {x["item"]["name"] for x in cheap}                                # nothing is filtered out, cheap things lead
    totals = [x["total_cost"] for x in cheap]
    assert totals.index(min(totals)) <= 2
    very = post(client, "/api/ai/craving", cravings=["very_hungry"], **OSU).json()["results"]
    assert very[0]["item"]["name"] in ("Spicy Jollof Chicken", "Far Chicken Rice")


def test_a_craving_nobody_sells_says_so(client, rec):
    r = post(client, "/api/ai/craving", cravings=["high_protein"], budget="10", **OSU).json()      # nothing this cheap is labelled high protein
    assert r["results"] == [] and "couldn't find" in r["headline"]
    mixed = post(client, "/api/ai/craving", cravings=["high_protein", "sweet"], budget="10", **OSU).json()
    assert [x["item"]["name"] for x in mixed["results"]] == ["Sobolo"]                              # matching ANY craving is enough; more matches rank higher


@pytest.mark.parametrize("body", [{"cravings": []}, {"cravings": ["tasty"]}, {"cravings": ["spicy"] * 10}])
def test_craving_validates_input(client, body):
    assert client.post("/api/ai/craving", json=body).status_code in (422,)


# ---------------------------------------------------------------- dietary safety
def test_dietary_needs_are_respected_and_unknown_is_never_safe(client, rec, db):
    veg = post(client, "/api/ai/afford", budget="100", dietary=["vegetarian"], **OSU).json()
    assert {x["item"]["name"] for x in veg["results"]} == {"Plain Rice", "Sobolo"}                      # vegetarian + vegan(implies vegetarian)
    assert any("Meets your dietary needs: vegetarian" in w for x in veg["results"] for w in x["why"])
    safe = post(client, "/api/ai/afford", budget="100", avoid_allergens=["peanuts"], **OSU).json()
    assert safe["results"] == []                                                                         # nobody confirmed allergens yet
    rec.plain.dietary_declared = True; db.commit()
    safe = post(client, "/api/ai/afford", budget="100", avoid_allergens=["peanuts"], **OSU).json()
    assert [x["item"]["name"] for x in safe["results"]] == ["Plain Rice"] and "Vendor confirmed allergen information" in safe["results"][0]["why"]


def test_saved_food_profile_applies_automatically(shopper, rec, db):
    assert shopper.put("/api/me/food-profile", json={"dietary": ["halal"], "spice_preference": "hot"}).status_code == 200
    got = shopper.post("/api/ai/afford", json={"budget": "100", **OSU}).json()
    assert {x["item"]["name"] for x in got["results"]} == {"Spicy Jollof Chicken"}                       # only halal-labelled dishes
    shopper.put("/api/me/food-profile", json={"dietary": [], "spice_preference": "none"})
    mild = {x["item"]["name"] for x in shopper.get("/api/ai/feed", params=OSU).json()["results"]}
    assert "Spicy Jollof Chicken" not in mild                                                              # "no spice" excludes spicy dishes
    assert shopper.put("/api/me/food-profile", json={"dietary": ["keto"]}).status_code == 422
    assert shopper.put("/api/me/food-profile", json={"spice_preference": "volcanic"}).status_code == 422
    assert shopper.get("/api/me/food-profile").json()["spice_preference"] == "none"


# ---------------------------------------------------------------- Personal Food Memory
def make_history(shopper, db, item, times=2):
    for _ in range(times):
        shopper.post("/api/cart/items", json={"menu_item_id": item.id, "quantity": 1})
        o = shopper.post("/api/orders", json={"address_id": shopper.address_id}).json()
        advance_to(db, o["id"], S.DELIVERED)


def rank_of(r, name):
    return names(r).index(name)


def test_memory_learns_from_orders_and_explains_it(shopper, client, rec, db):
    guest = client.get("/api/ai/feed", params=OSU)
    make_history(shopper, db, rec.plain, 2)
    me = shopper.get("/api/ai/feed", params=OSU)
    assert rank_of(me, "Plain Rice") <= rank_of(guest, "Plain Rice")                                      # moved up (or already top)
    plain = next(x for x in me.json()["results"] if x["item"]["name"] == "Plain Rice")
    assert "You've ordered this before" in plain["why"]
    assert me.json()["meta"]["personalised"] is True and "based on your usual choices" in me.json()["headline"]
    assert guest.json()["meta"]["personalised"] is False and "usual choices" not in guest.json()["headline"]
    other = [x for x in me.json()["results"] if x["item"]["name"] == "Sobolo"]
    assert other and "You've ordered this before" not in other[0]["why"]                                  # no false claims about dishes never ordered


def test_headline_matches_the_example_style(shopper, rec, db, monkeypatch):
    make_history(shopper, db, rec.plain, 3)
    monkeypatch.setattr(engine, "utcnow", lambda: datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc))
    h = shopper.get("/api/ai/feed", params=OSU).json()["headline"]
    assert h.startswith("Good afternoon, Yaw, here are") and " lunch options" in h and " under GH₵" in h and h.endswith("based on your usual choices.")


def test_food_memory_is_transparent_and_forgettable(shopper, rec, db):
    make_history(shopper, db, rec.plain, 2)
    shopper.get("/api/ai/feed", params=OSU)
    mem = shopper.get("/api/me/food-memory").json()
    assert mem["orders_considered"] == 2 and mem["favorite_categories"] == ["Jollof & Rice"] and mem["favorite_vendors"] == ["Near Kitchen"]
    assert mem["typical_dish_price"] == 20.0 and mem["personalization_enabled"] is True
    assert db.scalar(select(func.count()).select_from(RecommendationEvent)) > 0
    assert shopper.delete("/api/me/food-memory").status_code == 200
    forgotten = shopper.get("/api/me/food-memory").json()
    assert forgotten["orders_considered"] == 0 and forgotten["forgotten_before"]
    assert db.scalar(select(func.count()).select_from(RecommendationEvent)) == 0                         # tracking data erased too
    feed = shopper.get("/api/ai/feed", params=OSU).json()
    assert feed["meta"]["personalised"] is False and all("ordered this before" not in " ".join(x["why"]) for x in feed["results"])


def test_personalisation_can_be_switched_off(shopper, rec, db):
    make_history(shopper, db, rec.plain, 2)
    shopper.put("/api/me/food-profile", json={"personalization_enabled": False})
    feed = shopper.get("/api/ai/feed", params=OSU).json()
    assert feed["meta"]["personalised"] is False
    assert db.scalar(select(func.count()).select_from(RecommendationEvent)) == 0                         # nothing is tracked while it's off
    assert "turned off" in shopper.get("/api/me/food-memory").json()["message"]


def test_one_customers_history_never_affects_another(shopper, rec, db, make_user, login):
    make_history(shopper, db, rec.plain, 3)
    other = login(make_user("other@example.com"))
    res = other.get("/api/ai/feed", params=OSU).json()
    assert res["meta"]["personalised"] is False and all("ordered this before" not in " ".join(x["why"]) for x in res["results"])


def test_a_vendor_you_rated_badly_is_flagged_and_ranked_lower(shopper, rec, db):
    base = shopper.get("/api/ai/feed", params=OSU).json()["results"]
    make_history(shopper, db, rec.cola, 1)
    from app.models.orders import Order
    oid = db.scalar(select(Order.id).order_by(Order.id.desc()))
    shopper.post("/api/reviews", json={"order_id": oid, "food_rating": 1, "vendor_rating": 1})
    after = shopper.get("/api/ai/feed", params=OSU).json()["results"]
    assert any("You rated Near Kitchen 1 stars before." in x["warnings"] for x in after)


# ---------------------------------------------------------------- time of day
def test_the_right_meal_for_the_time_of_day(client, rec, db, monkeypatch):
    from app.models.catalog import MenuItem as M
    db.add(M(vendor_id=rec.near.id, category_id=rec.jollof.id, name="Koko & Koose", price=Decimal("12.00"), prep_minutes=8,
             meal_types=["breakfast"], traits=["filling"])); db.commit()
    monkeypatch.setattr(engine, "utcnow", lambda: AT_8AM)
    morning = client.get("/api/ai/feed", params=OSU).json()
    assert "Good morning" in morning["headline"] and "breakfast" in morning["headline"]
    assert morning["results"][0]["item"]["name"] == "Koko & Koose" and "Good for breakfast" in morning["results"][0]["why"]
    monkeypatch.setattr(engine, "utcnow", lambda: AT_8PM)
    evening = client.get("/api/ai/feed", params=OSU).json()
    assert "Good evening" in evening["headline"] and "dinner" in evening["headline"]
    assert evening["results"][0]["item"]["name"] != "Koko & Koose"
    assert [x["item"]["name"] for x in client.get("/api/ai/feed", params={**OSU, "meal_type": "breakfast"}).json()["results"]] == ["Koko & Koose"]
    assert client.get("/api/ai/feed", params={"meal_type": "brunch"}).status_code == 422


# ---------------------------------------------------------------- honesty of the explanations
def test_every_claim_in_why_is_backed_by_data(client, rec, db):
    rec.near.rating_count, rec.near.rating_avg = 12, 4.6
    rec.far.rating_count = 0
    db.commit()
    for x in post(client, "/api/ai/afford", budget="100", **OSU).json()["results"]:
        why = " | ".join(x["why"])
        if x["vendor"]["name"] == "Near Kitchen":
            assert "Highly rated vendor (4.6 from 12 reviews)" in why
        else:
            assert "Highly rated" not in why and "New on KORA EATS" in why                                 # a vendor with no reviews isn't called highly rated
        if x["item"]["name"] in ("Plain Rice", "Sobolo", "Far Chicken Rice"):                              # ordered 2, 0 and 0 times
            assert "Popular with other customers" not in why
        assert "ordered this before" not in why and "You saved" not in why                                  # guests have no memory to claim


# ---------------------------------------------------------------- the learning loop
def test_impressions_clicks_and_orders_are_tracked(shopper, client, rec, db):
    client.get("/api/ai/feed", params=OSU)
    assert db.scalar(select(func.count()).select_from(RecommendationEvent)) == 0                         # guests aren't tracked
    r = shopper.get("/api/ai/feed", params=OSU).json()["results"]
    kinds = lambda k: db.scalar(select(func.count()).select_from(RecommendationEvent).where(RecommendationEvent.kind == k))
    assert kinds("impression") == len(r)
    item_id = r[0]["item"]["id"]
    assert shopper.post("/api/ai/events", json={"surface": "feed", "kind": "click", "menu_item_id": item_id, "rank": 1}).status_code == 200
    assert shopper.post("/api/ai/events", json={"surface": "nowhere", "kind": "click", "menu_item_id": item_id}).status_code == 422
    assert kinds("click") == 1
    shopper.post("/api/cart/items", json={"menu_item_id": item_id})
    assert shopper.post("/api/orders", json={"address_id": shopper.address_id}).status_code == 201
    assert kinds("order") == 1                                                                             # credited to the recommendation they saw


def test_old_tracking_data_is_pruned(shopper, rec, db):
    shopper.get("/api/ai/feed", params=OSU)
    for e in db.scalars(select(RecommendationEvent)):
        e.created_at = utcnow() - timedelta(days=get_settings().rec_event_retention_days + 1)
    db.commit()
    assert events.prune(db) > 0 and db.scalar(select(func.count()).select_from(RecommendationEvent)) == 0


# ---------------------------------------------------------------- tuning, scale and home page
def test_admin_can_tune_the_weights(rec, db, monkeypatch, make_user):
    s = get_settings()
    monkeypatch.setattr(s, "rec_w_craving", s.rec_w_craving)
    admin = make_user("admin@example.com", __import__("app.models.user", fromlist=["UserRole"]).UserRole.ADMIN)
    settings_service.update(db, "rec_w_craving", 3.0, admin.id)
    assert weights()["craving"] == 3.0
    from app.core.errors import HTTPException
    with pytest.raises(HTTPException):
        settings_service.update(db, "rec_w_craving", 9, admin.id)                                          # outside 0..5


def test_retrieval_is_capped_so_the_engine_scales(client, rec, db, monkeypatch):
    db.add_all([MenuItem(vendor_id=rec.near.id, category_id=rec.jollof.id, name=f"Dish {i}", price=Decimal("15.00"), meal_types=["lunch"]) for i in range(150)])
    db.commit()
    monkeypatch.setattr(get_settings(), "rec_retrieval_limit", 50)
    assert post(client, "/api/ai/afford", budget="200", **OSU).json()["meta"]["considered"] <= 50


def test_query_count_stays_small(client, rec, db, test_engine):
    from sqlalchemy import event
    db.add_all([MenuItem(vendor_id=rec.near.id, category_id=rec.jollof.id, name=f"Dish {i}", price=Decimal("15.00"), meal_types=["lunch"]) for i in range(100)])
    db.commit()
    n = {"q": 0}
    listener = lambda *a, **k: n.__setitem__("q", n["q"] + 1)
    event.listen(test_engine, "before_cursor_execute", listener)
    try:
        assert post(client, "/api/ai/afford", budget="200", **OSU).status_code == 200
    finally:
        event.remove(test_engine, "before_cursor_execute", listener)
    assert n["q"] <= 15                                                                                    # does not grow with the number of dishes


def test_home_page_uses_the_engine(client, shopper, rec):
    for c in (client, shopper):
        home = c.get("/api/home", params=OSU).json()
        assert home["headline"] and home["recommended_items"]
        assert all(i["why"] and isinstance(i["match_score"], int) for i in home["recommended_items"])
    assert shopper.get("/api/ai/options").json()["cravings"][0]["id"] == "very_hungry"
