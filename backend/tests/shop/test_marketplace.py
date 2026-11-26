from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core import ghana
from app.core.config import get_settings
from app.models.audit import AuditLog
from app.models.catalog import ApprovalStatus, BusinessType, Category, MenuItem, Vendor
from app.models.user import UserRole
from app.services import food_taxonomy as tx

from tests.images import PNG  # noqa: E402
APPLY = {"name": "Mama Efua's Home Kitchen", "owner_name": "Efua Owusu", "phone": "024 123 4567", "address_text": "Madina Estate",
         "latitude": 5.67, "longitude": -0.17, "business_type": "HOME_COOK", "region": "greater accra", "area": "Madina",
         "digital_address": "ga 543 4531", "opens_at": "08:00:00", "closes_at": "20:00:00"}


@pytest.fixture(autouse=True)
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))


@pytest.fixture()
def admin(make_user, login):
    return login(make_user("admin@example.com", UserRole.ADMIN, "Site Admin"))


def doc(c, kind):
    return c.post("/api/vendor/documents", files={"file": ("d.png", PNG, "image/png")}, data={"doc_type": kind})


# ---------------------------------------------------------------- Ghana helpers
@pytest.mark.parametrize("raw,expected,network", [("024 123 4567", "+233241234567", "MTN"), ("0201234567", "+233201234567", "Telecel"),
    ("+233 26 123 4567", "+233261234567", "AirtelTigo"), ("233541234567", "+233541234567", "MTN"), ("030-123-4567", "+233301234567", None)])
def test_ghana_phone_normalisation(raw, expected, network):
    n = ghana.normalize_phone(raw)
    assert n == expected and ghana.phone_network(n) == network


@pytest.mark.parametrize("bad", ["", "12345", "+14155550123", "0111234567", "024 123 456", "+2332412345678", "abc"])
def test_foreign_or_malformed_phones_are_rejected(bad):
    assert ghana.normalize_phone(bad) is None


def test_ghana_bounds_and_digital_addresses():
    assert ghana.in_ghana(5.6, -0.18) and ghana.in_ghana(6.69, -1.62) and ghana.in_ghana(9.4, -0.85)      # Accra, Kumasi, Tamale
    assert not ghana.in_ghana(51.5, -0.12) and not ghana.in_ghana(5.6, 10.0) and not ghana.in_ghana(0, 0)
    assert ghana.normalize_digital_address(" ga 543 4531 ") == "GA-543-4531" and ghana.normalize_digital_address("AK-039-5028") == "AK-039-5028"
    assert ghana.normalize_digital_address("GA-54-4531") is None and ghana.normalize_digital_address("nope") is None
    assert len(ghana.REGIONS) == 16 and "Ashanti" in ghana.REGIONS


def test_phone_is_normalised_when_saved(make_user, login):
    c = login(make_user())
    assert c.put("/api/me/profile", json={"full_name": "Kofi Mensah", "phone": "0244 000 111"}).json()["phone"] == "+233244000111"
    assert c.put("/api/me/profile", json={"full_name": "Kofi Mensah", "phone": "+44 7700 900123"}).status_code == 422
    assert c.post("/api/rider/apply", json={"phone": "+1 415 555 0123", "vehicle_type": "BICYCLE"}).status_code == 422
    assert c.post("/api/rider/apply", json={"phone": "020 111 2233", "vehicle_type": "BICYCLE"}).json()["phone"] == "+233201112233"


def test_addresses_must_be_in_ghana_and_keep_ghanaian_details(make_user, login):
    c = login(make_user())
    base = {"label": "Home", "address_line": "12 Oxford St", "latitude": 5.556, "longitude": -0.183}
    assert c.post("/api/me/addresses", json={**base, "latitude": 51.5, "longitude": -0.12}).status_code == 422       # London
    kumasi = c.post("/api/me/addresses", json={**base, "city": "Kumasi", "latitude": 6.6885, "longitude": -1.6244, "region": "ashanti",
                                                "area": "Adum", "digital_address": "ak 039 5028"})
    assert kumasi.status_code == 201
    a = kumasi.json()
    assert (a["region"], a["area"], a["digital_address"]) == ("Ashanti", "Adum", "AK-039-5028")
    assert c.post("/api/me/addresses", json={**base, "region": "Narnia"}).status_code == 422
    assert c.post("/api/me/addresses", json={**base, "digital_address": "12345"}).status_code == 422


def test_taxonomy_and_requirements_endpoints_are_public(client):
    t = client.get("/api/taxonomy").json()
    assert {x["id"] for x in t["business_types"]} == {"RESTAURANT", "CHOP_BAR", "HOME_COOK", "BAKER", "STREET_FOOD", "SMALL_BUSINESS", "CATERER"}
    assert "halal" in {x["id"] for x in t["dietary"]} and "high_protein" in {x["id"] for x in t["traits"]} and len(t["regions"]) == 16
    assert any(c["name"] == "Kumasi" for c in t["cities"])
    r = {x["id"]: x for x in client.get("/api/vendor-requirements").json()["types"]}
    assert r["HOME_COOK"]["required"] == ["ID_CARD"] and "BUSINESS_REGISTRATION" in r["RESTAURANT"]["required"] and r["CHOP_BAR"]["any_of"]


# ---------------------------------------------------------------- everyone can sell: type-specific verification
@pytest.fixture()
def applicant(make_user, login):
    return login(make_user("owner@example.com", name="Efua Owusu"))


@pytest.mark.parametrize("btype,docs,ok", [
    ("HOME_COOK", ["ID_CARD"], True), ("STREET_FOOD", ["ID_CARD"], True), ("HOME_COOK", ["FOOD_HANDLER_CERT"], False),
    ("CHOP_BAR", ["ID_CARD"], False), ("CHOP_BAR", ["ID_CARD", "FOOD_HANDLER_CERT"], True), ("BAKER", ["ID_CARD", "BUSINESS_REGISTRATION"], True),
    ("RESTAURANT", ["ID_CARD"], False), ("RESTAURANT", ["ID_CARD", "BUSINESS_REGISTRATION"], True),
    ("CATERER", ["BUSINESS_REGISTRATION"], False), ("CATERER", ["ID_CARD", "BUSINESS_REGISTRATION"], True), ("SMALL_BUSINESS", ["ID_CARD", "OTHER"], False)])
def test_each_seller_type_has_its_own_requirements(applicant, admin, btype, docs, ok):
    vid = applicant.post("/api/vendor/apply", json={**APPLY, "business_type": btype}).json()["id"]
    for d in docs:
        doc(applicant, d)
    r = admin.post(f"/api/admin/vendors/{vid}/approve")
    assert (r.status_code == 200) is ok, r.text


def test_admin_can_force_approve_but_it_is_audited(applicant, admin, db):
    vid = applicant.post("/api/vendor/apply", json={**APPLY, "business_type": "RESTAURANT"}).json()["id"]
    doc(applicant, "ID_CARD")
    listed = admin.get("/api/admin/vendors").json()[0]
    assert listed["business_type"] == "RESTAURANT" and listed["missing_documents"] == ["Business registration"]
    assert admin.post(f"/api/admin/vendors/{vid}/approve", params={"force": True}).status_code == 200
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "vendor_approved_with_missing_docs")) is not None


def test_application_stores_ghanaian_details_and_type_is_locked_after_approval(applicant, admin, login, db, make_user):
    r = applicant.post("/api/vendor/apply", json=APPLY).json()
    assert (r["business_type"], r["region"], r["area"], r["digital_address"]) == ("HOME_COOK", "Greater Accra", "Madina", "GA-543-4531")
    assert applicant.post("/api/vendor/apply", json={**APPLY, "latitude": 40.7, "longitude": -74.0}).status_code in (409, 422)
    doc(applicant, "ID_CARD"); admin.post(f"/api/admin/vendors/{r['id']}/approve")
    owner = db.scalar(select(Vendor).where(Vendor.id == r["id"])).owner_user_id
    from app.models.user import User
    v = login(db.get(User, owner))
    body = {k: APPLY[k] for k in ("name", "owner_name", "phone", "address_text", "latitude", "longitude")} | {"business_type": "RESTAURANT"}
    assert v.put("/api/vendor/me", json=body).json()["business_type"] == "HOME_COOK"       # can't upgrade/downgrade yourself


def test_new_vendor_types_are_discoverable(client, world, db):
    cat = db.scalar(select(Category))
    db.add_all([Vendor(name="Sweet Oven", business_type=BusinessType.BAKER, latitude=5.55, longitude=-0.2, area="Dansoman", region="Greater Accra",
                       address_text="Dansoman High St", approval_status=ApprovalStatus.APPROVED, category_id=cat.id),
                Vendor(name="Auntie Esi", business_type=BusinessType.HOME_COOK, latitude=6.69, longitude=-1.62, area="Adum", region="Ashanti",
                       address_text="Adum, Kumasi", approval_status=ApprovalStatus.APPROVED)])
    db.commit()
    bakers = client.get("/api/vendors", params={"business_type": "BAKER"}).json()
    assert [v["name"] for v in bakers] == ["Sweet Oven"] and bakers[0]["business_label"] == "Baker" and bakers[0]["area"] == "Dansoman"
    assert [v["name"] for v in client.get("/api/vendors", params={"q": "adum"}).json()] == ["Auntie Esi"]      # neighbourhood search
    assert client.get("/api/vendors", params={"business_type": "SPACESHIP"}).status_code == 422


# ---------------------------------------------------------------- structured food data
@pytest.fixture()
def vendor_client(world, vendor_user, login):
    return login(vendor_user)


def add_item(c, **kw):
    body = {"name": "Dish", "price": "20.00", **kw}
    return c.post("/api/vendor/menu", json=body)


def test_vendor_describes_food_with_controlled_options(vendor_client):
    r = add_item(vendor_client, name="Waakye Special", traits=["Filling", "high protein"], meal_types=["lunch", "dinner"], dietary=["halal"],
                 allergens=["eggs"], dietary_declared=True, spice_level=3)
    assert r.status_code == 201
    item = r.json()
    assert item["traits"] == ["filling", "high_protein"] and item["spice_level"] == 3 and item["dietary_declared"] is True
    listed = vendor_client.get("/api/vendor/menu").json()[-1]
    assert listed["meal_types"] == ["lunch", "dinner"] and listed["allergens"] == ["eggs"]
    for bad in ({"traits": ["delicious"]}, {"dietary": ["keto"]}, {"allergens": ["gluten", "unicorn"]}, {"meal_types": ["brunch"]}, {"spice_level": 4}):
        assert add_item(vendor_client, **bad).status_code == 422
    assert add_item(vendor_client, dietary=["vegan"], allergens=["dairy"]).status_code == 422             # contradictory labels are refused


def test_spice_flag_and_level_stay_consistent(vendor_client):
    assert add_item(vendor_client, is_spicy=True).json()["spice_level"] == 2
    hot = add_item(vendor_client, spice_level=3).json()
    assert hot["is_spicy"] if "is_spicy" in hot else True
    plain = add_item(vendor_client).json()
    assert plain["spice_level"] == 0


def test_public_api_shows_attributes(client, world, vendor_client):
    add_item(vendor_client, name="Fruit Salad", traits=["light", "sweet"], meal_types=["dessert"], dietary=["vegan"])
    item = next(i for i in client.get("/api/menu-items").json() if i["name"] == "Fruit Salad")
    assert item["traits"] == ["light", "sweet"] and item["dietary"] == ["vegan"] and item["dietary_declared"] is False


def test_attribute_filters(client, world, vendor_client):
    add_item(vendor_client, name="Fruit Salad", traits=["light", "sweet"], meal_types=["dessert", "snack"], dietary=["vegan"])
    add_item(vendor_client, name="Koko & Koose", traits=["filling"], meal_types=["breakfast"])
    add_item(vendor_client, name="Grilled Tilapia", traits=["high_protein", "filling"], meal_types=["lunch", "dinner"], dietary=["pescatarian", "halal"])
    names = lambda **p: sorted(i["name"] for i in client.get("/api/menu-items", params=p).json() if i["name"] in ("Fruit Salad", "Koko & Koose", "Grilled Tilapia"))
    assert names(trait="light") == ["Fruit Salad"] and names(trait=["filling", "high_protein"]) == ["Grilled Tilapia"]     # all traits must match
    assert names(meal_type="breakfast") == ["Koko & Koose"] and names(meal_type=["dessert", "dinner"]) == ["Fruit Salad", "Grilled Tilapia"]
    assert names(dietary="vegetarian") == ["Fruit Salad"]                       # vegan counts as vegetarian
    assert names(dietary="halal") == ["Grilled Tilapia"]
    assert names(dietary=["vegan", "halal"]) == []
    assert client.get("/api/menu-items", params={"trait": "tasty"}).status_code == 422


def test_allergen_filter_never_treats_unknown_as_safe(client, world, vendor_client):
    add_item(vendor_client, name="Mystery Stew")                                                              # nothing declared
    add_item(vendor_client, name="Safe Rice", dietary_declared=True, allergens=["eggs"])                      # declared: only eggs
    add_item(vendor_client, name="Peanut Soup", dietary_declared=True, allergens=["peanuts"])
    add_item(vendor_client, name="Nut-free Cake", dietary=["nut_free"])                                       # label guarantees no peanuts/tree nuts
    found = sorted(i["name"] for i in client.get("/api/menu-items", params={"avoid_allergen": "peanuts"}).json()
                   if i["name"] in ("Mystery Stew", "Safe Rice", "Peanut Soup", "Nut-free Cake"))
    assert found == ["Nut-free Cake", "Safe Rice"]


def test_food_taxonomy_rules():
    assert tx.clean(["Vegan", "gluten free", "vegan"], tx.DIETARY) == ["vegan", "gluten_free"]
    with pytest.raises(ValueError):
        tx.clean(["paleo"], tx.DIETARY)
    ok = lambda d, a, decl, need, avoid: tx.check_diet(d, a, decl, need, avoid)[0]
    assert ok(["vegan"], [], False, ["vegetarian"], []) and ok(["vegan"], [], False, ["dairy_free"], ["dairy"])
    assert not ok([], [], False, ["vegetarian"], []) and not ok([], [], False, ["halal"], [])                 # labels must be explicit
    assert not ok([], [], False, [], ["peanuts"]) and ok([], [], True, [], ["peanuts"]) and not ok([], ["peanuts"], True, [], ["peanuts"])
    assert ok([], [], True, ["gluten_free"], []) and not ok([], [], False, ["gluten_free"], []) and not ok([], ["gluten"], True, ["gluten_free"], [])
    assert ok(["pescatarian"], [], False, [], ["pork"]) and not ok(["halal"], [], False, [], ["pork"])        # halal alone doesn't prove it in our data
    assert tx.check_diet([], [], False, [], ["eggs"])[1] == ["eggs not confirmed by the vendor"]
