from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.orders import FulfillmentType, Order, OrderStatus as S
from app.models.reviews import Review
from app.services import order_service as svc
from app.services import quote_service as q
from tests.shop.test_notifications import advance_to, titles
from tests.shop.test_payments import pay, simulate


def place_pickup(shopper, item, qty=2):
    shopper.post("/api/cart/items", json={"menu_item_id": item.id, "quantity": qty})
    r = shopper.post("/api/orders", json={"fulfillment_type": "PICKUP"})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- the shared quote engine
def test_delivery_fee_formula():
    assert q.delivery_fee(None) is None
    assert q.delivery_fee(0.5) == Decimal("5.00") and q.delivery_fee(2.0) == Decimal("5.00")       # inside the free distance
    assert q.delivery_fee(6.0) == Decimal("11.00")                                                 # 5 + 4km x 1.5


def test_preparation_and_eta():
    assert q.prep_minutes(20, [10, 25, 15]) == 25 and q.prep_minutes(20, []) == 20 and q.prep_minutes(2, [1]) == 5
    assert q.travel_minutes(5.0) == 15                                                             # 5 km at 20 km/h
    assert q.delivery_eta(20, 5.0) == 20 + get_settings().rider_assign_buffer_minutes + 15


def test_pickup_message_matches_the_product_wording():
    assert q.pickup_message(Decimal("7"), 15, 40) == "Pickup saves GH₵7.00 and your meal will be ready in 15 minutes."
    assert q.pickup_message(Decimal("5"), 1, 30) == "Pickup saves GH₵5.00 and your meal will be ready in 1 minute."
    assert q.pickup_message(Decimal("0"), 15, 30) == "Pickup is about 15 minutes faster: your meal will be ready in 15 minutes."
    assert q.pickup_message(Decimal("0"), 15, 17) is None                                          # not meaningfully faster: stay quiet
    assert q.pickup_message(None, 15, None) is None


def test_compare_without_and_with_distance():
    assert q.compare(20, [15], None)["delivery"] is None and q.compare(20, [15], None)["pickup"]["ready_minutes"] == 15
    c = q.compare(20, [15], 3.0)
    assert c["delivery"]["fee"] == 6.5 and c["delivery"]["eta_minutes"] > c["pickup"]["ready_minutes"] and c["pickup_saving"]["amount"] == 6.5


# ---------------------------------------------------------------- cart shows both options + the saving
def test_cart_view_compares_delivery_and_pickup(world, shopper):
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id, "quantity": 2})
    no_addr = shopper.get("/api/cart").json()
    assert no_addr["options"]["delivery"] is None and no_addr["options"]["pickup"]["total"] == 90.0
    cart = shopper.get("/api/cart", params={"address_id": shopper.address_id}).json()
    o = cart["options"]
    assert o["delivery"]["fee"] == 5.0 and o["delivery"]["total"] == 95.0 and o["pickup"]["total"] == 90.0
    assert o["pickup"]["ready_minutes"] == 20 and o["pickup"]["address"] == "Osu"
    assert o["pickup_message"] == "Pickup saves GH₵5.00 and your meal will be ready in 20 minutes."
    assert cart["fulfillment"] == "DELIVERY" and cart["total"] == 95.0                              # the selected option drives the headline numbers
    pick = shopper.get("/api/cart", params={"fulfillment": "PICKUP"}).json()
    assert pick["fulfillment"] == "PICKUP" and pick["delivery_fee"] == 0.0 and pick["total"] == 90.0
    assert shopper.get("/api/cart", params={"fulfillment": "TELEPORT"}).status_code == 422


# ---------------------------------------------------------------- ordering for pickup
def test_pickup_order_needs_no_address_and_has_no_fee(world, shopper):
    o = place_pickup(shopper, world.spicy)
    assert o["fulfillment_type"] == "PICKUP" and o["delivery_fee"] == 0.0 and o["total"] == 90.0
    assert len(o["pickup_code"]) == 6 and o["delivery_address"] == "Osu"                            # the "address" is the vendor
    assert (o["delivery_lat"], o["delivery_lng"]) == (world.near.latitude, world.near.longitude)


def test_pickup_works_even_when_the_vendor_is_too_far_to_deliver(world, shopper):
    shopper.post("/api/cart/items", json={"menu_item_id": world.farfood.id})
    assert shopper.post("/api/orders", json={"address_id": shopper.address_id}).status_code == 409   # delivery refused (out of zone)
    assert shopper.post("/api/orders", json={"fulfillment_type": "PICKUP"}).status_code == 201       # pickup is fine


def test_delivery_still_requires_an_address(world, shopper):
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    assert shopper.post("/api/orders", json={}).json()["detail"]["code"] == "address_not_found"
    assert shopper.post("/api/orders", json={"fulfillment_type": "DELIVERY"}).status_code == 404
    assert shopper.post("/api/orders", json={"fulfillment_type": "ROBOT"}).status_code == 422


def test_coupons_with_pickup(world, shopper, db):
    from app.models.promos import Coupon, CouponType
    db.add_all([Coupon(code="FREEDEL", type=CouponType.FREE_DELIVERY, value=0), Coupon(code="TEN", type=CouponType.PERCENT, value=Decimal(10))]); db.commit()
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id, "quantity": 2})
    shopper.post("/api/cart/coupon", json={"code": "FREEDEL"})
    pickup = shopper.get("/api/cart", params={"fulfillment": "PICKUP"}).json()
    assert pickup["discount"] == 0 and "only applies to delivery" in pickup["coupon_message"]
    shopper.post("/api/cart/coupon", json={"code": "TEN"})
    assert shopper.get("/api/cart", params={"fulfillment": "PICKUP"}).json()["total"] == 81.0


def test_pickup_order_can_be_paid(world, shopper):
    o = place_pickup(shopper, world.spicy)
    simulate(shopper, pay(shopper, o["id"]).json()["reference"])
    assert shopper.get(f"/api/payments/orders/{o['id']}/verify").json()["status"] == "PAID"


# ---------------------------------------------------------------- the pickup journey
def test_full_pickup_flow(world, shopper, vendor_user, make_rider, login, db):
    rider_user, _ = make_rider()
    o = place_pickup(shopper, world.spicy)
    advance_to(db, o["id"], S.READY_FOR_PICKUP)
    code = shopper.get(f"/api/orders/{o['id']}").json()["pickup_code"]
    assert "Ready for pickup" in titles(db, shopper.user)
    assert "New delivery available" not in titles(db, rider_user)                                    # riders are NOT alerted
    r = login(rider_user)
    assert r.get("/api/rider/available").json() == []
    assert r.post(f"/api/rider/deliveries/{o['id']}/accept").json()["detail"]["code"] == "pickup_order"
    v = login(vendor_user)
    listed = v.get("/api/vendor/orders").json()[0]
    assert listed["fulfillment_type"] == "PICKUP" and listed["pickup_code"] is None                  # the vendor can't see the code
    assert v.post(f"/api/vendor/orders/{o['id']}/collect", json={"code": "WRONG1"}).json()["detail"]["code"] == "wrong_code"
    done = v.post(f"/api/vendor/orders/{o['id']}/collect", json={"code": code.lower()})              # case-insensitive
    assert done.status_code == 200 and done.json()["status"] == "COLLECTED" and done.json()["pickup_code"] is None
    assert v.post(f"/api/vendor/orders/{o['id']}/collect", json={"code": code}).status_code == 409   # already collected
    db.expire_all()
    assert db.get(Order, o["id"]).delivered_at is not None
    assert "Order collected" in titles(db, shopper.user)
    assert [x["code"] for x in v.get("/api/vendor/orders", params={"view": "history"}).json()] == [o["code"]]
    assert shopper.get(f"/api/orders/{o['id']}").json()["can_review"] is True                        # collected orders can be reviewed
    assert shopper.post("/api/reviews", json={"order_id": o["id"], "food_rating": 5, "vendor_rating": 5}).status_code == 201
    db.refresh(world.spicy)
    assert world.spicy.order_count == 9 + 2                                                          # popularity counts pickups too


def test_collect_is_guarded(world, shopper, vendor_user, make_user, login, db, place_order):
    pick = place_pickup(shopper, world.spicy)
    v = login(vendor_user)
    assert v.post(f"/api/vendor/orders/{pick['id']}/collect", json={"code": "ABCDEF"}).json()["detail"]["code"] == "not_ready"   # not ready yet
    deliv = place_order()
    advance_to(db, deliv["id"], S.READY_FOR_PICKUP)
    assert v.post(f"/api/vendor/orders/{deliv['id']}/collect", json={"code": "ABCDEF"}).status_code == 404       # delivery orders can't be collected
    from app.models.user import UserRole
    rival = make_user("rival@example.com", UserRole.VENDOR); world.far.owner_user_id = rival.id; db.commit()
    advance_to(db, pick["id"], S.READY_FOR_PICKUP)
    code = shopper.get(f"/api/orders/{pick['id']}").json()["pickup_code"]
    assert login(rival).post(f"/api/vendor/orders/{pick['id']}/collect", json={"code": code}).status_code == 404  # another vendor's order
    assert shopper.post(f"/api/vendor/orders/{pick['id']}/collect", json={"code": code}).status_code in (403, 404)  # customers can't collect


def test_state_machine_keeps_the_two_journeys_apart(world, shopper, place_order, db):
    pick, deliv = place_pickup(shopper, world.spicy), place_order()
    for o in (pick, deliv):
        advance_to(db, o["id"], S.READY_FOR_PICKUP)
    from fastapi import HTTPException
    for oid, bad, role in [(pick["id"], S.RIDER_ASSIGNED, "rider"), (pick["id"], S.RIDER_ASSIGNED, "admin"),
                           (deliv["id"], S.COLLECTED, "vendor"), (deliv["id"], S.COLLECTED, "admin")]:
        with pytest.raises(HTTPException) as e:
            o = svc.lock_order(db, oid); svc.change_status(db, o, bad, role)
        assert e.value.status_code == 409
        db.rollback()
    o = svc.lock_order(db, pick["id"]); svc.change_status(db, o, S.COLLECTED, "admin"); svc.change_status(db, o, S.REFUNDED, "admin"); db.commit()


def test_customer_cannot_see_someone_elses_pickup_code(world, shopper, make_user, login):
    o = place_pickup(shopper, world.spicy)
    assert login(make_user("snoop@example.com")).get(f"/api/orders/{o['id']}").status_code == 404


def test_admin_sees_fulfillment_and_sales_include_collected(world, shopper, make_user, login, db):
    from app.models.user import UserRole
    admin = login(make_user("admin@example.com", UserRole.ADMIN))
    o = place_pickup(shopper, world.spicy)
    advance_to(db, o["id"], S.READY_FOR_PICKUP)
    order = svc.lock_order(db, o["id"]); svc.change_status(db, order, S.COLLECTED, "vendor"); db.commit()
    assert admin.get("/api/admin/orders").json()[0]["fulfillment"] == "PICKUP"
    assert admin.get("/api/admin/overview").json()["cards"]["total_revenue"] == 90.0
