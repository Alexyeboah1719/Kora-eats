import itertools
import json
import random
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.catalog import MenuItem
from app.services import food_taxonomy as tx
from app.services.recommend import group as solver
from app.services.recommend.retrieve import retrieve
from app.services.recommend.types import Request
from tests.shop.conftest import OSU


@pytest.fixture()
def gw(world, db):
    """A small marketplace with structured food data. Prices are whole cedis so the solver can be checked exactly."""
    w = world
    w.spicy.spice_level, w.spicy.dietary, w.spicy.tags, w.spicy.traits = 2, ["halal"], "chicken,rice,spicy", ["filling"]
    w.plain.dietary, w.plain.tags = ["vegetarian"], "rice,budget"
    w.cola.dietary, w.cola.meal_types = ["vegan"], ["drink"]
    w.farfood.tags = "chicken,rice"
    add = lambda **kw: db.add(MenuItem(vendor_id=w.near.id, category_id=w.jollof.id, prep_minutes=15, **kw))
    add(name="Chicken Burger", price=Decimal("38.00"), tags="chicken,burger", dietary_declared=True, allergens=["gluten", "eggs"])
    add(name="Veggie Wrap", price=Decimal("28.00"), tags="wrap", dietary=["vegetarian"], dietary_declared=True, allergens=["gluten"])
    add(name="Beans & Gari", price=Decimal("15.00"), tags="beans,cheap", dietary=["vegan"], dietary_declared=True, allergens=[], traits=["filling"])
    db.commit()
    return w


def split(c, **body):
    return c.post("/api/ai/group/split", json=body)


def dish(db, item_id):
    return db.get(MenuItem, item_id)


# ---------------------------------------------------------------- Split the Budget
def test_a_plan_that_suits_each_person_and_fits_the_budget(client, gw, db):
    people = [{"label": "Kofi", "terms": ["chicken"]}, {"label": "Ama", "cravings": ["cheap"], "max_price": "20"}, {"label": "Yaw", "no_spice": True}]
    r = split(client, people=3, budget="150", preferences=people, **OSU).json()
    plan = r["plans"][0]
    assert plan["vendor"]["name"] == "Near Kitchen" and len(plan["people"]) == 3
    by = {p["label"]: p for p in plan["people"]}
    assert "chicken" in (dish(db, by["Kofi"]["menu_item_id"]).tags + dish(db, by["Kofi"]["menu_item_id"]).name).lower() and by["Kofi"]["match"] == 100
    assert dish(db, by["Ama"]["menu_item_id"]).price <= 20 and dish(db, by["Yaw"]["menu_item_id"]).spice_level == 0
    assert plan["total"] == round(plan["food_total"] + plan["delivery_fee"], 2) and plan["total"] <= 150
    assert plan["cost_per_person"] == round(plan["total"] / 3, 2) and abs(sum(p["pays"] for p in plan["people"]) - plan["total"]) <= 0.03
    assert plan["left_over"] == round(150 - plan["total"], 2) and 0 <= plan["compatibility"] <= 100
    assert r["headline"].startswith("3 people, GH₵150 total") and any("within your GH₵150 budget" in w for w in plan["why"])
    assert "Far Grill" in [x["vendor"] for x in r["partial"]] and r["partial"][0]["no_dish_for"] == ["Ama"]       # nothing cheap enough there


def test_delivery_fee_is_shared_once(client, gw):
    plan = split(client, people=4, budget="300", fulfillment="DELIVERY", **OSU).json()["plans"][0]
    assert plan["delivery_fee"] == 5.0 and all(p["share_of_delivery"] == 1.25 for p in plan["people"])
    assert plan["fulfillment"] == "DELIVERY" and plan["pickup_message"]


def test_pickup_is_chosen_when_delivery_would_break_the_budget(client, gw):
    d = split(client, people=2, budget="20", fulfillment="DELIVERY", **OSU).json()
    assert d["plans"] == [] and "cheapest meal for everyone is about GH₵25.00" in d["hint"]                       # 10 + 10 + 5 delivery
    best = split(client, people=2, budget="20", fulfillment="BEST", **OSU).json()["plans"][0]
    assert best["fulfillment"] == "PICKUP" and best["delivery_fee"] == 0 and best["total"] <= 20


def test_no_location_flags_the_unknown_fee(client, gw):
    r = split(client, people=2, budget="100", fulfillment="DELIVERY").json()
    assert r["note"].startswith("Set your delivery location") and r["plans"][0]["fee_unknown"] is True


def test_dietary_and_allergen_needs_are_hard_rules(client, gw, db):
    r = split(client, people=2, budget="100", preferences=[{"label": "Efua", "dietary": ["vegan"], "avoid_allergens": ["peanuts"]}], **OSU).json()
    efua = next(p for p in r["plans"][0]["people"] if p["label"] == "Efua")
    assert efua["dish"] == "Beans & Gari"                                         # vegan + vendor-confirmed allergens (Sobolo isn't confirmed)
    assert "Vendor confirmed allergen information" in efua["why"]
    impossible = split(client, people=2, budget="100", preferences=[{"label": "Efua", "dietary": ["vegan", "halal"]}], **OSU).json()
    assert impossible["plans"] == [] and "No single vendor" in impossible["hint"] and impossible["partial"][0]["no_dish_for"] == ["Efua"]


def test_compatibility_reflects_unmet_wishes(client, gw):
    r = split(client, people=2, budget="200", preferences=[{"label": "A", "terms": ["pizza"]}], **OSU).json()
    plan = r["plans"][0]
    assert {p["label"]: p["match"] for p in plan["people"]} == {"A": 0, "Person 2": 100} and plan["compatibility"] == 50    # nobody sells pizza


@pytest.mark.parametrize("body,code", [({"people": 1, "budget": "50"}, 422), ({"people": 21, "budget": "50"}, 422), ({"people": 3, "budget": "0"}, 422),
    ({"people": 2, "budget": "5001"}, 422), ({"people": 2, "preferences": [{}, {}, {}]}, 422), ({"people": 2, "preferences": [{"cravings": ["tasty"]}]}, 422),
    ({"people": 2, "preferences": [{"dietary": ["keto"]}]}, 422), ({"people": 2, "fulfillment": "TELEPORT"}, 422)])
def test_split_validates_input(client, gw, body, code):
    assert client.post("/api/ai/group/split", json=body).status_code == code


def test_without_a_budget_it_just_finds_the_best_match(client, gw):
    plan = split(client, people=2, **OSU).json()["plans"][0]
    assert plan["left_over"] is None and len(plan["people"]) == 2


# ---------------------------------------------------------------- properties: hard rules and optimality
def _satisfies(db, person, item_id):
    it = dish(db, item_id)
    ok, _ = tx.check_diet(list(it.dietary or []), list(it.allergens or []), bool(it.dietary_declared), person["dietary"], person["avoid_allergens"])
    return ok and (not person["no_spice"] or it.spice_level == 0) and (not person["max_price"] or it.price <= Decimal(person["max_price"]))


def test_no_plan_ever_breaks_a_hard_rule_or_the_budget(client, gw, db):
    rnd = random.Random(7)
    for _ in range(40):
        n = rnd.randint(2, 5)
        prefs = [{"label": f"P{i}", "dietary": rnd.choice([[], [], ["vegetarian"], ["halal"], ["vegan"]]), "avoid_allergens": rnd.choice([[], [], ["peanuts"], ["gluten"]]),
                  "no_spice": rnd.random() < 0.4, "max_price": rnd.choice([None, None, "12", "20", "30", "50"]), "terms": rnd.choice([[], ["chicken"], ["rice"]])} for i in range(n)]
        budget = rnd.choice([None, str(rnd.randint(40, 300))])
        r = split(client, people=n, budget=budget, preferences=prefs, fulfillment=rnd.choice(["BEST", "DELIVERY", "PICKUP"]), **OSU)
        assert r.status_code == 200
        for plan in r.json()["plans"]:
            assert len(plan["people"]) == n
            for person, chosen in zip(prefs, plan["people"]):
                assert _satisfies(db, {"dietary": person["dietary"], "avoid_allergens": person["avoid_allergens"], "no_spice": person["no_spice"], "max_price": person["max_price"]}, chosen["menu_item_id"])
            if budget:
                assert plan["total"] <= float(budget) + 1e-9


def test_the_budget_solver_is_optimal_against_brute_force(gw, db):
    cands = [c for c in retrieve(db, Request(pos=(OSU["lat"], OSU["lng"]), surface="group"), None) if c.vendor.id == gw.near.id]
    prices = [float(c.price) for c in cands]
    import statistics
    med, maxpop = statistics.median(prices), max((c.item.order_count or 0) for c in cands)
    rnd = random.Random(3)
    checked = 0
    for _ in range(60):
        persons = [solver.Person(label=f"P{i}", terms=rnd.choice([[], ["chicken"], ["rice"], ["beans"]]), cravings=rnd.choice([[], [], ["cheap"], ["filling"]]),
                                 no_spice=rnd.random() < 0.3, max_price=rnd.choice([None, Decimal(30), Decimal(45)])) for i in range(3)]
        per = [[o for c in cands if (o := solver.option_for(p, c, med, maxpop))] for p in persons]
        if any(not x for x in per):
            continue
        per = [sorted(x, key=lambda o: (-o.sat, o.cand.price))[:solver.OPTIONS_PER_PERSON] for x in per]
        budget = Decimal(rnd.randint(30, 160))
        best = None
        for combo in itertools.product(*per):
            cost = sum((o.cand.price for o in combo), Decimal(0))
            if cost <= budget:
                score = (sum(o.sat for o in combo), -float(cost))
                best = score if best is None or score > best else best
        got = solver._knapsack(per, budget)
        if best is None:
            assert got is None
        else:
            assert got is not None and sum((o.cand.price for o in got), Decimal(0)) <= budget
            assert abs(sum(o.sat for o in got) - best[0]) < 1e-5                                                  # exactly as good as trying every combination
            checked += 1
    assert checked >= 20


# ---------------------------------------------------------------- group orders with AI
@pytest.fixture()
def crew(gw, shopper, make_user, login):
    friend, third = login(make_user("friend@example.com", name="Ama Friend")), login(make_user("third@example.com", name="Kwame Third"))
    code = shopper.post("/api/groups", json={"vendor_id": gw.near.id}).json()["code"]
    for c in (friend, third):
        c.post(f"/api/groups/{code}/join")
    return type("Crew", (), {"code": code, "org": shopper, "friend": friend, "third": third, "w": gw})


def test_members_set_private_wishes_and_only_the_owner_can_read_them(crew):
    r = crew.friend.put(f"/api/groups/{crew.code}/preferences", json={"terms": ["Chicken!!", "chicken"], "no_spice": True, "cravings": ["filling"], "max_price": "40"})
    assert r.status_code == 200 and r.json()["terms"] == ["chicken"] and r.json()["no_spice"] is True and r.json()["max_price"] == 40.0
    assert crew.third.get(f"/api/groups/{crew.code}/preferences").json()["terms"] == []                    # third sees only THEIR own (empty)
    view = crew.third.get(f"/api/groups/{crew.code}").json()
    assert "chicken" not in json.dumps(view).lower() and "terms" not in view and "no_spice" not in json.dumps(view)   # nobody else's wishes leak
    assert {m["name"]: m["has_preferences"] for m in view["members"]}["Ama Friend"] is True                # others can see THAT they've answered, not WHAT
    for bad in ({"cravings": ["tasty"]}, {"dietary": ["keto"]}, {"max_price": "0"}):
        assert crew.friend.put(f"/api/groups/{crew.code}/preferences", json=bad).status_code == 422


def test_only_members_can_set_wishes(crew, make_user, login):
    assert login(make_user("outsider@example.com")).put(f"/api/groups/{crew.code}/preferences", json={}).status_code == 403


def test_organizer_gets_a_plan_that_respects_every_members_profile_and_wishes(crew, db):
    crew.friend.put(f"/api/groups/{crew.code}/preferences", json={"terms": ["chicken"], "no_spice": True})
    crew.third.put("/api/me/food-profile", json={"dietary": ["halal"]})                                      # saved profile applies automatically
    assert crew.friend.post(f"/api/groups/{crew.code}/suggest", json={}).status_code == 403                  # organizer only
    r = crew.org.post(f"/api/groups/{crew.code}/suggest", json={"budget": "200", **OSU}).json()
    plan = r["plans"][0]
    by = {p["label"]: p for p in plan["people"]}
    assert set(by) == {"Yaw", "Ama", "Kwame"} and r["members_without_preferences"] == 2
    assert dish(db, by["Ama"]["menu_item_id"]).spice_level == 0 and by["Ama"]["match"] == 100
    assert "halal" in dish(db, by["Kwame"]["menu_item_id"]).dietary                                         # the halal member never gets a non-halal dish


def test_applying_a_plan_fills_the_group_basket_and_checkout_works(crew, db):
    crew.friend.put(f"/api/groups/{crew.code}/preferences", json={"terms": ["chicken"], "no_spice": True})
    plan = crew.org.post(f"/api/groups/{crew.code}/suggest", json={"budget": "200", **OSU}).json()["plans"][0]
    body = {"vendor_id": plan["vendor"]["id"], "assignments": [{"user_id": p["user_id"], "menu_item_id": p["menu_item_id"]} for p in plan["people"]]}
    assert crew.friend.post(f"/api/groups/{crew.code}/suggest/apply", json=body).status_code == 403
    view = crew.org.post(f"/api/groups/{crew.code}/suggest/apply", json=body).json()
    assert len(view["items"]) == 3 and view["subtotal"] == plan["food_total"] and {i["member"] for i in view["items"]} == {"Yaw Shopper", "Ama Friend", "Kwame Third"}
    cart = crew.org.post(f"/api/groups/{crew.code}/checkout").json()
    assert cart["subtotal"] == plan["food_total"]
    assert crew.org.post("/api/orders", json={"address_id": crew.org.address_id}).status_code == 201           # the organizer pays for everyone


def test_apply_revalidates_everything(crew, db):
    from app.models.user import User, UserProfile
    mid = lambda name: db.scalar(select(MenuItem.id).where(MenuItem.name == name))
    uid = lambda name: db.execute(select(User.id).join(UserProfile).where(UserProfile.full_name == name)).scalar()
    ok = {"user_id": str(uid("Ama Friend")), "menu_item_id": mid("Plain Rice")}
    post = lambda **b: crew.org.post(f"/api/groups/{crew.code}/suggest/apply", json={"vendor_id": crew.w.near.id, **b})
    assert post(assignments=[{"user_id": "00000000-0000-0000-0000-000000000000", "menu_item_id": mid("Plain Rice")}]).status_code == 422      # not a member
    assert post(assignments=[ok, ok]).json()["detail"]["code"] == "bad_assignment"                                                        # same person twice
    assert post(assignments=[{**ok, "menu_item_id": mid("Far Chicken Rice")}]).json()["detail"]["code"] == "wrong_vendor"                 # another vendor's dish
    assert post(assignments=[{**ok, "menu_item_id": mid("Sold Out Special")}]).status_code == 409                                         # unavailable
    assert post(assignments=[]).status_code == 422
    assert post(assignments=[ok]).status_code == 200
    crew.org.post(f"/api/groups/{crew.code}/close")
    assert post(assignments=[ok]).status_code == 409


def test_switching_vendor_starts_a_fresh_basket(crew, db):
    crew.org.post(f"/api/groups/{crew.code}/items", json={"menu_item_id": crew.w.cola.id})
    uid = crew.org.get(f"/api/groups/{crew.code}").json()["items"][0]["user_id"]
    far = crew.org.post(f"/api/groups/{crew.code}/suggest/apply", json={"vendor_id": crew.w.far.id, "assignments": [{"user_id": uid, "menu_item_id": crew.w.farfood.id}]}).json()
    assert far["vendor"]["name"] == "Far Grill" and [i["name"] for i in far["items"]] == ["Far Chicken Rice"]       # the old Sobolo is gone


def test_any_vendor_mode_looks_beyond_the_groups_vendor(crew):
    own = crew.org.post(f"/api/groups/{crew.code}/suggest", json={"budget": "300", **OSU}).json()
    wide = crew.org.post(f"/api/groups/{crew.code}/suggest", json={"budget": "300", "any_vendor": True, **OSU}).json()
    assert own["scope"] == "group_vendor" and {p["vendor"]["name"] for p in own["plans"]} == {"Near Kitchen"}
    assert wide["scope"] == "any_vendor"
