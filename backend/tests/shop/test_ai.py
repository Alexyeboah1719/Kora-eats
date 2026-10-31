import pytest
from sqlalchemy import select

from app.models.ai import AiMessage, CustomerPreference
from app.models.catalog import MenuItem
from app.models.orders import OrderStatus as S
from app.services import nl_query, sentiment
from tests.shop.conftest import OSU
from tests.shop.test_notifications import advance_to

VOCAB = nl_query.build_vocab([("Jollof Rice with Chicken", "filling,chicken,rice,spicy"), ("Waakye Special", "rice,beans"), ("Sobolo", "drink")])


# ---------------------------------------------------------------- the parser
def test_parser_understands_the_example_requests():
    p = nl_query.parse("I have GHS 40 and want something filling", VOCAB)
    assert p.max_price == 40 and p.filling and p.terms == []
    p = nl_query.parse("I want spicy food near me", VOCAB)
    assert p.spicy is True and p.near_me and p.max_km == 5 and p.sort == "distance"
    p = nl_query.parse("I want chicken and rice under GHS 50", VOCAB)
    assert p.terms == ["chicken", "rice"] and p.max_price == 50


@pytest.mark.parametrize("text,field,value", [
    ("waakye under 20 minutes", "max_prep", 20), ("waakye under 20 minutes", "max_price", None),
    ("rice within 3 km", "max_km", 3), ("rice within 3.5 km", "max_price", None), ("rice 4 stars", "min_rating", 4),
    ("top rated rice", "min_rating", 4.3), ("rice 45 cedis", "max_price", 45), ("rice GH₵ 30", "max_price", 30),
    ("I need something quick", "max_prep", 15), ("not spicy please", "spicy", False), ("cheap rice", "sort", "price_asc"),
    ("budget of 25", "max_price", 25), ("rice under GH₵35", "max_price", 35), ("rice ₵20", "max_price", 20), ("rice GH₵ 18.50", "max_price", 18.5), ("jollof under 45.50", "max_price", 45.5)])
def test_parser_edge_cases(text, field, value):
    assert getattr(nl_query.parse(text, VOCAB), field) == value


def test_parser_ignores_words_that_are_not_on_the_menu_and_plain_chat():
    assert nl_query.parse("pizza please", VOCAB).terms == []
    assert nl_query.parse("hello there", VOCAB).understood is False


# ---------------------------------------------------------------- answers come from the database
def chat(c, msg, **kw):
    r = c.post("/api/ai/chat", json={"message": msg, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_filling_on_a_budget(client, world):
    r = chat(client, "I have GHS 40 and want something filling")
    assert [i["name"] for i in r["items"]] == ["Far Chicken Rice"]                       # the only filling dish <= GHS 40
    assert r["items"][0]["price"] == 40.0 and "Budget: up to GH₵40" in r["filters"]
    assert "Far Chicken Rice" in r["reply"] and "Far Grill" in r["reply"]


def test_spicy_near_me_uses_distance_only_when_location_known(client, world):
    r = chat(client, "I want spicy food near me", **{"lat": OSU["lat"], "lng": OSU["lng"]})
    assert [i["name"] for i in r["items"]] == ["Spicy Jollof Chicken"] and r["items"][0]["distance_km"] < 1
    no_loc = chat(client, "I want spicy food near me")
    assert no_loc["items"] and "Set your delivery location" in no_loc["reply"]            # honest about the missing location


def test_chicken_and_rice_under_budget(client, world):
    r = chat(client, "I want chicken and rice under GHS 50")
    assert {i["name"] for i in r["items"]} == {"Spicy Jollof Chicken", "Far Chicken Rice"}
    assert all(i["price"] <= 50 for i in r["items"])


def test_never_invents_and_never_offers_unavailable_food(client, world, db):
    for q in ["rice", "I have GHS 100 and want something filling", "spicy", "cheap rice", "chicken"]:
        r = chat(client, q)
        for item in r["items"]:
            row = db.get(MenuItem, item["id"])
            assert row is not None and row.is_available and float(row.price) == item["price"]   # real, available, same price
            assert item["name"] == row.name and item["vendor_name"] == row.vendor.name
        assert "Sold Out Special" not in r["reply"]
        assert "minute delivery" not in r["reply"] and "delivery time" not in r["reply"].lower()   # no made-up ETAs


def test_closed_vendor_is_not_recommended(client, world, db):
    world.near.is_open = False; db.commit()
    assert {i["vendor_name"] for i in chat(client, "rice")["items"]} == {"Far Grill"}


def test_no_match_explains_with_real_numbers(client, world):
    r = chat(client, "I have GHS 5")
    assert r["items"] == [] and "couldn't find" in r["reply"]
    assert "Sobolo" in r["reply"] and "GH₵10.00" in r["reply"]                           # cheapest real item is suggested


def test_small_talk_gets_help_not_invented_food(client, world):
    r = chat(client, "hi there")
    assert r["items"] == [] and "Try:" in r["reply"]


def test_conversations_are_stored_and_private(client, world, make_user, login, db):
    me = login(make_user())
    r1 = chat(me, "spicy food")
    r2 = chat(me, "something filling under 60", conversation_id=r1["conversation_id"])
    assert r2["conversation_id"] == r1["conversation_id"]
    hist = me.get(f"/api/ai/conversations/{r1['conversation_id']}").json()
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "assistant"]
    assert hist[1]["item_ids"], "assistant messages remember which real items were shown"
    other = login(make_user("other@example.com"))
    assert other.get(f"/api/ai/conversations/{r1['conversation_id']}").status_code == 404
    assert other.post("/api/ai/chat", json={"message": "x", "conversation_id": r1["conversation_id"]}).status_code == 404
    assert chat(client, "spicy food")["conversation_id"] is None                          # guests: nothing stored
    assert db.scalar(select(AiMessage.id).where(AiMessage.content == "spicy food", AiMessage.role == "user")) is not None


def test_preferences_are_learned(world, make_user, login):
    me = login(make_user())
    chat(me, "not spicy under GHS 40"); chat(me, "spicy under GHS 60")
    pref = me.get("/api/ai/preferences").json()
    assert pref["spicy_preference"] is True and pref["avg_budget"] == 50.0


def test_delivered_orders_teach_favourite_categories(world, shopper, place_order, db):
    advance_to(db, place_order()["id"], S.DELIVERED)
    assert shopper.get("/api/ai/preferences").json()["top_categories"] == ["Drinks", "Jollof & Rice"] or \
        "Jollof & Rice" in shopper.get("/api/ai/preferences").json()["top_categories"]


def test_natural_search_endpoint(client, world):
    r = client.get("/api/search/natural", params={"q": "chicken under GHS 42"}).json()
    assert r["understood"] and [i["name"] for i in r["items"]] == ["Far Chicken Rice"]
    assert "Budget: up to GH₵42" in r["filters"]
    assert client.get("/api/search/natural", params={"q": ""}).status_code == 422


# ---------------------------------------------------------------- review sentiment
def test_sentiment_basics():
    pos = sentiment.analyse("Delicious jollof, fast delivery!", 5, 5)
    assert pos["sentiment"] == "POSITIVE" and pos["aspects"] == {"food": "POSITIVE", "delivery": "POSITIVE"}
    neg = sentiment.analyse("The food was not good and the rider was late, but the packaging was great", 2, 3)
    assert neg["sentiment"] == "NEGATIVE"
    assert neg["aspects"] == {"food": "NEGATIVE", "delivery": "NEGATIVE", "packaging": "POSITIVE"}     # negation + "but" handled
    assert sentiment.analyse("overpriced and rude staff", 2, 1)["aspects"] == {"price": "NEGATIVE", "service": "NEGATIVE"}
    assert sentiment.analyse(None, 5, 5)["sentiment"] == "POSITIVE" and sentiment.analyse(None, 1, 1)["sentiment"] == "NEGATIVE"
    assert sentiment.analyse("ok", 3, 3)["sentiment"] == "NEUTRAL"


def test_reviews_are_analysed_and_vendor_gets_insights(world, shopper, place_order, vendor_user, login, db):
    o1, o2 = place_order(), place_order()
    for o in (o1, o2):
        advance_to(db, o["id"], S.DELIVERED)
    shopper.post("/api/reviews", json={"order_id": o1["id"], "food_rating": 2, "vendor_rating": 2, "comment": "Food was cold and the delivery was slow."})
    shopper.post("/api/reviews", json={"order_id": o2["id"], "food_rating": 5, "vendor_rating": 5, "comment": "Delicious!"})
    v = login(vendor_user)
    ins = v.get("/api/vendor/reviews/insights").json()
    assert ins["sentiment"] == {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": 1} and ins["method"].startswith("rule-based")
    assert {i["aspect"] for i in ins["top_issues"]} == {"food", "delivery"}
    listed = v.get("/api/vendor/reviews").json()["reviews"]
    assert {r["sentiment"] for r in listed} == {"POSITIVE", "NEGATIVE"}
