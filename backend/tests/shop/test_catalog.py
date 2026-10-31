from tests.shop.conftest import OSU


def test_categories_and_vendors_sorted_by_distance(client, world):
    assert {c["name"] for c in client.get("/api/categories").json()} == {"Jollof & Rice", "Drinks"}
    r = client.get("/api/vendors", params=OSU).json()
    assert [v["name"] for v in r] == ["Near Kitchen", "Far Grill"]
    assert r[0]["distance_km"] < 1 < r[1]["distance_km"]
    assert r[0]["delivery_fee"] == 5.0          # inside the free distance -> base fee only
    assert r[1]["delivery_fee"] > r[0]["delivery_fee"]


def test_vendor_radius_filter(client, world):
    r = client.get("/api/vendors", params={**OSU, "max_km": 3}).json()
    assert [v["name"] for v in r] == ["Near Kitchen"]


def test_vendor_detail_groups_menu(client, world):
    r = client.get(f"/api/vendors/{world.near.id}").json()
    sections = {s["category"]: [i["name"] for i in s["items"]] for s in r["menu"]}
    assert "Spicy Jollof Chicken" in sections["Jollof & Rice"] and sections["Drinks"] == ["Sobolo"]
    assert client.get("/api/vendors/99999").status_code == 404


def test_item_search_filters(client, world):
    names = lambda **p: [i["name"] for i in client.get("/api/menu-items", params=p).json()]
    assert "Sold Out Special" not in names()                       # unavailable items hidden
    assert names(max_price=25) == ["Plain Rice", "Sobolo"] or set(names(max_price=25)) == {"Plain Rice", "Sobolo"}
    assert names(spicy="true") == ["Spicy Jollof Chicken"]
    assert set(names(q="chicken")) == {"Spicy Jollof Chicken", "Far Chicken Rice"}
    assert names(q="chicken", **OSU, max_km=3) == ["Spicy Jollof Chicken"]
    assert names(q="chicken rice", sort="price_asc")[0] == "Far Chicken Rice"
    assert client.get("/api/menu-items", params={"sort": "evil"}).status_code == 422


def test_search_is_safe_from_sql_injection(client, world):
    r = client.get("/api/menu-items", params={"q": "x'; DROP TABLE menu_items;--"})
    assert r.status_code == 200 and r.json() == []
    assert len(client.get("/api/menu-items").json()) >= 4


def test_home_is_public_and_personalised(client, world, make_user, login):
    anon = client.get("/api/home", params=OSU).json()
    assert anon["greeting"].endswith("!") and anon["nearby_vendors"] and anon["popular_items"]
    assert anon["popular_items"][0]["name"] == "Spicy Jollof Chicken"  # highest order_count
    me = login(make_user())
    assert "Kofi" in me.get("/api/home").json()["greeting"]


def test_favorites_roundtrip(client, world, make_user, login):
    me = login(make_user())
    assert me.post("/api/me/favorites", json={"vendor_id": world.near.id}).status_code == 201
    assert me.post("/api/me/favorites", json={"vendor_id": world.near.id}).status_code == 201  # idempotent
    assert me.post("/api/me/favorites", json={"menu_item_id": world.plain.id}).status_code == 201
    fav = me.get("/api/me/favorites").json()
    assert [v["name"] for v in fav["vendors"]] == ["Near Kitchen"] and [i["name"] for i in fav["items"]] == ["Plain Rice"]
    assert me.get(f"/api/vendors/{world.near.id}").json()["is_favorite"] is True
    assert me.post("/api/me/favorites", json={"vendor_id": 1, "menu_item_id": 1}).status_code == 422
    me.delete("/api/me/favorites", params={"vendor_id": world.near.id})
    assert me.get("/api/me/favorites").json()["vendors"] == []
    assert client.get("/api/me/favorites").status_code == 401


ADDR = {"label": "Home", "address_line": "12 Oxford St", "latitude": 5.556, "longitude": -0.183}


def test_addresses_default_logic_and_isolation(client, make_user, login):
    me = login(make_user())
    a = me.post("/api/me/addresses", json=ADDR).json()
    assert a["is_default"] is True                                  # first one becomes default
    b = me.post("/api/me/addresses", json={**ADDR, "label": "Work", "is_default": True}).json()
    lst = me.get("/api/me/addresses").json()
    assert [x["label"] for x in lst] == ["Work", "Home"] and lst[0]["is_default"] and not lst[1]["is_default"]
    me.delete(f"/api/me/addresses/{b['id']}")
    assert me.get("/api/me/addresses").json()[0]["is_default"] is True   # default promoted
    other = login(make_user("other@example.com"))
    assert other.delete(f"/api/me/addresses/{a['id']}").status_code == 404  # can't touch others' data
    assert me.post("/api/me/addresses", json={**ADDR, "latitude": 999}).status_code == 422


def test_profile_update_validates(client, make_user, login):
    me = login(make_user())
    assert me.put("/api/me/profile", json={"full_name": "Kofi A. Mensah", "phone": "+233201234567"}).status_code == 200
    assert me.get("/api/me/profile").json()["phone"] == "+233201234567"
    assert me.put("/api/me/profile", json={"full_name": "K", "phone": "abc"}).status_code == 422
