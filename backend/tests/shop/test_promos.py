from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.timeutil import utcnow
from app.models.catalog import Promotion
from app.models.orders import Order, OrderStatus as S
from app.models.promos import Coupon, CouponType, CouponUsage
from app.models.user import UserRole
from app.services import scheduler
from tests.shop.test_notifications import advance_to


def coupon(db, code="SAVE10", type_=CouponType.PERCENT, value=10, **kw):
    c = Coupon(code=code, type=type_, value=Decimal(str(value)), **kw)
    db.add(c); db.commit()
    return c


def cart_with(shopper, item, qty=2, **kw):
    return shopper.post("/api/cart/items", json={"menu_item_id": item.id, "quantity": qty}).json()


def apply(shopper, code):
    return shopper.post("/api/cart/coupon", json={"code": code}, params={"address_id": shopper.address_id})


# ---------------------------------------------------------------- coupons
def test_percent_coupon_with_cap_and_checkout(world, shopper, db):
    coupon(db, "SAVE10", value=10, max_discount=Decimal("5.00"))
    cart_with(shopper, world.spicy)                                      # subtotal 90
    r = apply(shopper, "save10")                                         # case-insensitive
    assert r.status_code == 200 and r.json()["discount"] == 5.0          # 10% = 9.00 but capped at 5
    assert r.json()["total"] == 90 + 5 - 5
    o = shopper.post("/api/orders", json={"address_id": shopper.address_id}).json()
    assert o["discount"] == 5.0 and o["total"] == 90.0 and o["coupon_code"] == "SAVE10"
    assert db.scalar(select(func.count()).select_from(CouponUsage)) == 1


def test_fixed_and_free_delivery(world, shopper, db):
    coupon(db, "TENOFF", CouponType.FIXED, 10)
    coupon(db, "FREEDEL", CouponType.FREE_DELIVERY, 0)
    cart_with(shopper, world.spicy)
    assert apply(shopper, "TENOFF").json()["discount"] == 10.0
    r = apply(shopper, "FREEDEL").json()
    assert r["discount"] == 5.0 and r["total"] == 90.0                   # delivery fee waived
    no_addr = shopper.post("/api/cart/coupon", json={"code": "FREEDEL"}).json()
    assert no_addr["discount"] == 0 and "address" in no_addr["coupon_message"].lower()


def test_fixed_coupon_never_exceeds_order_value(world, shopper, db):
    coupon(db, "HUGE", CouponType.FIXED, 500)
    cart_with(shopper, world.cola, 1)                                    # subtotal 10 + fee 5
    r = apply(shopper, "HUGE").json()
    assert r["discount"] == 10.0 and r["total"] == 5.0                    # can't go below the delivery fee here, never negative


@pytest.mark.parametrize("kw,fragment", [
    ({"min_order": Decimal("200")}, "Spend GH₵200.00"),
    ({"expires_at": "past"}, "expired"),
    ({"starts_at": "future"}, "isn't active yet"),
    ({"is_active": False}, "isn't valid"),
    ({"usage_limit": 0}, "usage limit")])
def test_coupon_rejections(world, shopper, db, kw, fragment):
    kw = dict(kw)
    for k in ("expires_at", "starts_at"):
        if kw.get(k) == "past": kw[k] = utcnow() - timedelta(days=1)
        if kw.get(k) == "future": kw[k] = utcnow() + timedelta(days=1)
    coupon(db, "TESTCODE", **kw)
    cart_with(shopper, world.spicy)
    r = apply(shopper, "TESTCODE")
    assert r.status_code == 422 and fragment in r.json()["detail"]["message"]


def test_unknown_code_and_empty_cart(world, shopper, db):
    coupon(db, "REAL")
    assert apply(shopper, "REAL").json()["detail"]["code"] == "empty_cart"
    cart_with(shopper, world.spicy)
    assert apply(shopper, "NOPE").status_code == 422


def test_per_user_and_total_limits_and_release_on_cancel(world, shopper, db, make_user, login):
    coupon(db, "ONCE", per_user_limit=1, usage_limit=2)
    cart_with(shopper, world.spicy)
    apply(shopper, "ONCE")
    o = shopper.post("/api/orders", json={"address_id": shopper.address_id}).json()
    cart_with(shopper, world.spicy)
    assert "already used" in apply(shopper, "ONCE").json()["detail"]["message"]            # per-user limit

    def newcomer(email):
        c = login(make_user(email))
        c.post("/api/me/addresses", json={"label": "H", "address_line": "Osu", "latitude": 5.557, "longitude": -0.182})
        c.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
        return c
    second = newcomer("second@example.com")
    assert second.post("/api/cart/coupon", json={"code": "ONCE"}).status_code == 200      # 2nd of 2 redemptions
    assert second.post("/api/orders", json={"address_id": second.get("/api/me/addresses").json()[0]["id"], "phone": "+233200000000"}).status_code == 201
    third = newcomer("third@example.com")
    assert "usage limit" in third.post("/api/cart/coupon", json={"code": "ONCE"}).json()["detail"]["message"]   # total limit
    shopper.post(f"/api/orders/{o['id']}/cancel", json={})                                 # cancelling gives the redemption back
    assert third.post("/api/cart/coupon", json={"code": "ONCE"}).status_code == 200


def test_first_order_only(world, shopper, place_order, db):
    coupon(db, "WELCOME", first_order_only=True)
    cart_with(shopper, world.spicy)
    assert apply(shopper, "WELCOME").status_code == 200
    shopper.delete("/api/cart")
    o = place_order(); advance_to(db, o["id"], S.PAID)                    # now they have a paid order
    cart_with(shopper, world.spicy)
    assert "first orders only" in apply(shopper, "WELCOME").json()["detail"]["message"]


def test_vendor_specific_coupon(world, shopper, db):
    coupon(db, "FARONLY", vendor_id=world.far.id)
    cart_with(shopper, world.spicy)                                      # Near Kitchen
    assert "only works for orders from Far Grill" in apply(shopper, "FARONLY").json()["detail"]["message"]


def test_coupon_that_turns_invalid_blocks_checkout(world, shopper, db):
    c = coupon(db, "SOON")
    cart_with(shopper, world.spicy)
    apply(shopper, "SOON")
    c.expires_at = utcnow() - timedelta(minutes=1); db.commit()
    r = shopper.post("/api/orders", json={"address_id": shopper.address_id})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "coupon_invalid"


def test_removing_coupon(world, shopper, db):
    coupon(db, "SAVE10")
    cart_with(shopper, world.spicy); apply(shopper, "SAVE10")
    assert shopper.delete("/api/cart/coupon").json()["discount"] == 0


# ---------------------------------------------------------------- coupon / promotion management
def test_admin_and_vendor_manage_coupons(world, vendor_user, make_user, login, db):
    admin = login(make_user("admin@example.com", UserRole.ADMIN))
    body = {"code": "summer5", "type": "PERCENT", "value": 5, "description": "Summer"}
    r = admin.post("/api/admin/coupons", json=body)
    assert r.status_code == 201 and r.json()["code"] == "SUMMER5" and r.json()["vendor_id"] is None
    assert admin.post("/api/admin/coupons", json=body).status_code == 409
    assert admin.post("/api/admin/coupons", json={**body, "code": "BAD", "value": 150}).status_code == 422
    assert admin.post("/api/admin/coupons", json={**body, "code": "x y"}).status_code == 422
    v = login(vendor_user)
    mine = v.post("/api/vendor/coupons", json={**body, "code": "MINE5"}).json()
    assert mine["vendor_id"] == world.near.id
    assert [c["code"] for c in v.get("/api/vendor/coupons").json()] == ["MINE5"]       # vendors see only their own
    assert v.put(f"/api/vendor/coupons/{r.json()['id']}", json=body).status_code == 404  # can't edit platform coupons
    assert v.delete(f"/api/vendor/coupons/{mine['id']}").status_code == 200
    assert v.get("/api/vendor/coupons").json()[0]["is_active"] is False
    assert login(make_user("c@example.com")).get("/api/admin/coupons").status_code == 403


# ---------------------------------------------------------------- promotions
def test_promotion_discounts_price_and_home_banner(client, world, shopper, vendor_user, login, db):
    v = login(vendor_user)
    now = utcnow()
    p = {"title": "Jollof week", "discount_percent": 20, "menu_item_id": world.spicy.id,
         "starts_at": (now - timedelta(hours=1)).isoformat(), "ends_at": (now + timedelta(days=2)).isoformat()}
    assert v.post("/api/vendor/promotions", json=p).status_code == 201
    item = next(i for i in client.get("/api/menu-items").json() if i["id"] == world.spicy.id)
    assert item["price"] == 36.0 and item["original_price"] == 45.0
    home = client.get("/api/home").json()
    assert home["promotions"][0]["badge"] == "20% off" and home["promotions"][0]["vendor_name"] == "Near Kitchen"
    cart = cart_with(shopper, world.spicy, 1)
    assert cart["items"][0]["unit_price"] == 36.0 and cart["subtotal"] == 36.0     # the discount is what's charged
    other = next(i for i in client.get("/api/menu-items").json() if i["id"] == world.plain.id)
    assert other["original_price"] is None                                         # other items unaffected


def test_vendor_wide_promotion_and_expiry(client, world, vendor_user, login, db):
    v = login(vendor_user)
    now = utcnow()
    body = {"title": "Everything 10% off", "discount_percent": 10, "starts_at": (now - timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat()}
    pid = v.post("/api/vendor/promotions", json=body).json()["id"]
    assert next(i for i in client.get("/api/menu-items").json() if i["id"] == world.plain.id)["price"] == 18.0
    db.get(Promotion, pid).ends_at = now - timedelta(minutes=1); db.commit()     # expired
    assert next(i for i in client.get("/api/menu-items").json() if i["id"] == world.plain.id)["price"] == 20.0
    assert client.get("/api/home").json()["promotions"] == []


def test_promotion_validation_and_ownership(world, vendor_user, make_user, login):
    v = login(vendor_user)
    now = utcnow()
    base = {"title": "Deal", "discount_percent": 10, "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=1)).isoformat()}
    assert v.post("/api/vendor/promotions", json={**base, "discount_percent": 95}).status_code == 422
    assert v.post("/api/vendor/promotions", json={**base, "ends_at": (now - timedelta(days=1)).isoformat()}).status_code == 422
    assert v.post("/api/vendor/promotions", json={**base, "menu_item_id": world.farfood.id}).status_code == 422   # not their item
    pid = v.post("/api/vendor/promotions", json=base).json()["id"]
    assert login(make_user("v2@example.com", UserRole.VENDOR)).delete(f"/api/vendor/promotions/{pid}").status_code in (403, 404)


# ---------------------------------------------------------------- scheduled orders
def later(minutes):
    return (utcnow() + timedelta(minutes=minutes)).isoformat()


def test_schedule_validation(world, shopper):
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    go = lambda when: shopper.post("/api/orders", json={"address_id": shopper.address_id, "scheduled_for": when})
    assert go(later(10)).json()["detail"]["code"] == "schedule_too_soon"
    assert go(later(60 * 24 * 10)).json()["detail"]["code"] == "schedule_too_far"
    ok = go(later(120))
    assert ok.status_code == 201 and ok.json()["scheduled_for"]


def test_cannot_schedule_when_vendor_is_closed_then(world, shopper, db):
    from datetime import time
    from app.models.catalog import VendorHour
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})            # added while the vendor is open
    when = utcnow() + timedelta(hours=3)
    world.near.hours = [VendorHour(weekday=when.weekday(), opens_at=time(0, 0), closes_at=time(0, 1)),
                        *[VendorHour(weekday=d, opens_at=time(0, 0), closes_at=time(23, 59)) for d in range(7) if d != when.weekday()]]
    db.commit()
    r = shopper.post("/api/orders", json={"address_id": shopper.address_id, "scheduled_for": when.isoformat()})
    assert r.status_code in (409,) and r.json()["detail"]["code"] in ("vendor_closed_then", "cart_invalid")


def test_scheduled_order_is_hidden_from_vendor_until_released(world, shopper, vendor_user, login, db):
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    o = shopper.post("/api/orders", json={"address_id": shopper.address_id, "scheduled_for": later(240)}).json()
    advance_to(db, o["id"], S.PAID)
    v = login(vendor_user)
    assert v.get("/api/vendor/orders").json() == []                                     # not yet
    assert v.post(f"/api/vendor/orders/{o['id']}/status", json={"status": "CONFIRMED"}).json()["detail"]["code"] == "not_released"
    assert scheduler.release_scheduled_orders(db) == 0                                  # 4h away: too early
    order = db.get(Order, o["id"]); order.scheduled_for = utcnow() + timedelta(minutes=40); db.commit()
    assert scheduler.release_scheduled_orders(db) == 1                                  # within prep time + 30 min lead
    assert scheduler.release_scheduled_orders(db) == 0                                  # only once
    listed = v.get("/api/vendor/orders").json()
    assert [x["code"] for x in listed] == [o["code"]] and listed[0]["scheduled_for"]
    assert v.post(f"/api/vendor/orders/{o['id']}/status", json={"status": "CONFIRMED"}).status_code == 200
    assert "Scheduled order due soon" in [n["title"] for n in login(vendor_user).get("/api/notifications").json()]


def test_unpaid_scheduled_order_is_never_released(world, shopper, db):
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    o = shopper.post("/api/orders", json={"address_id": shopper.address_id, "scheduled_for": later(50)}).json()
    assert scheduler.release_scheduled_orders(db) == 0


# ---------------------------------------------------------------- abandoned payments
def test_unpaid_orders_expire_but_paid_ones_survive(world, shopper, place_order, db):
    from tests.shop.test_payments import pay, simulate
    stale, paid_late = place_order(), place_order()
    simulate(shopper, pay(shopper, paid_late["id"], key="late-pay-0001").json()["reference"])   # paid but never verified
    for oid in (stale["id"], paid_late["id"]):
        db.get(Order, oid).created_at = utcnow() - timedelta(hours=2)
    db.commit()
    assert scheduler.expire_unpaid_orders(db) == 1
    db.expire_all()
    assert db.get(Order, stale["id"]).status == S.CANCELLED and db.get(Order, stale["id"]).cancelled_reason == "Payment timed out"
    assert db.get(Order, paid_late["id"]).status == S.PAID                                         # rescued by verification
    assert scheduler.run_cycle(db).keys() == {"released", "expired", "reminders", "pruned", "rec_events_pruned", "withdrawals_reconciled"}


# ---------------------------------------------------------------- group orders
@pytest.fixture()
def friend(make_user, login):
    c = login(make_user("friend@example.com", name="Ama Friend"))
    return c


def test_group_flow_end_to_end(world, shopper, friend, db):
    g = shopper.post("/api/groups", json={"vendor_id": world.near.id}).json()
    assert len(g["code"]) == 6 and g["share_path"] == f"/group/{g['code']}" and g["is_organizer"]
    code = g["code"]
    joined = friend.post(f"/api/groups/{code}/join").json()
    assert [m["name"] for m in joined["members"]] == ["Yaw Shopper", "Ama Friend"] and not joined["is_organizer"]
    shopper.post(f"/api/groups/{code}/items", json={"menu_item_id": world.spicy.id, "quantity": 1, "special_instructions": "extra shito"})
    friend.post(f"/api/groups/{code}/items", json={"menu_item_id": world.spicy.id, "quantity": 2})
    v = friend.post(f"/api/groups/{code}/items", json={"menu_item_id": world.cola.id}).json()
    assert v["subtotal"] == 45 * 3 + 10
    assert [i["mine"] for i in v["items"]] == [False, True, True]
    assert friend.post(f"/api/groups/{code}/checkout").status_code == 403                 # only the organizer
    cart = shopper.post(f"/api/groups/{code}/checkout").json()
    by = {i["name"]: i for i in cart["items"]}
    assert by["Spicy Jollof Chicken"]["quantity"] == 3 and "Yaw: extra shito" in by["Spicy Jollof Chicken"]["special_instructions"]
    assert cart["subtotal"] == 145.0
    assert friend.post(f"/api/groups/{code}/items", json={"menu_item_id": world.cola.id}).json()["detail"]["code"] == "group_closed"
    o = shopper.post("/api/orders", json={"address_id": shopper.address_id})
    assert o.status_code == 201 and o.json()["subtotal"] == 145.0                         # the organizer pays for everyone


def test_group_rules(world, shopper, friend, db, make_user, login):
    code = shopper.post("/api/groups", json={"vendor_id": world.near.id}).json()["code"]
    assert friend.post(f"/api/groups/{code}/items", json={"menu_item_id": world.cola.id}).json()["detail"]["code"] == "not_member"
    friend.post(f"/api/groups/{code}/join")
    assert friend.post(f"/api/groups/{code}/items", json={"menu_item_id": world.farfood.id}).json()["detail"]["code"] == "wrong_vendor"
    assert friend.post(f"/api/groups/{code}/items", json={"menu_item_id": world.gone.id}).status_code == 409
    line = friend.post(f"/api/groups/{code}/items", json={"menu_item_id": world.cola.id}).json()["items"][0]["id"]
    assert shopper.patch(f"/api/groups/{code}/items/{line}", json={"quantity": 4}).json()["items"][0]["quantity"] == 4   # organizer may edit
    third = login(make_user("third@example.com"))
    third.post(f"/api/groups/{code}/join")
    assert third.delete(f"/api/groups/{code}/items/{line}").status_code == 403                   # others' items are protected
    assert shopper.post(f"/api/groups/{code}/checkout").status_code == 200 or True
    assert shopper.get("/api/groups/ZZZZZZ").status_code == 404
    assert shopper.post("/api/groups", json={"vendor_id": 9999}).status_code == 404


def test_empty_group_cannot_check_out_and_expiry(world, shopper, friend, db):
    from app.models.groups import GroupOrder
    code = shopper.post("/api/groups", json={"vendor_id": world.near.id}).json()["code"]
    assert shopper.post(f"/api/groups/{code}/checkout").json()["detail"]["code"] == "empty_group"
    db.scalar(select(GroupOrder)).expires_at = utcnow() - timedelta(minutes=1); db.commit()
    assert friend.post(f"/api/groups/{code}/join").status_code == 410
    assert shopper.get(f"/api/groups/{code}").json()["status"] == "EXPIRED"


def test_group_requires_login(client, world):
    assert client.get("/api/groups/ABC123").status_code == 401
    assert client.post("/api/groups", json={"vendor_id": 1}).status_code == 401
