from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import HTTPException
from app.models.catalog import MenuItem
from app.models.orders import Order, OrderStatus as S
from app.models.user import UserRole
from app.services import order_service as svc


# ---------------------------------------------------------------- cart
def test_cart_basics(world, shopper):
    r = shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id, "quantity": 2, "special_instructions": "no pepper"})
    assert r.status_code == 200
    cart = r.json()
    assert cart["subtotal"] == 90.0 and cart["item_count"] == 2 and cart["delivery_fee"] is None    # no address yet
    line = cart["items"][0]
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id, "quantity": 1})            # merges
    assert shopper.get("/api/cart").json()["items"][0]["quantity"] == 3
    c = shopper.patch(f"/api/cart/items/{line['id']}", json={"quantity": 1}).json()
    assert c["subtotal"] == 45.0 and c["items"][0]["special_instructions"] == "no pepper"
    withaddr = shopper.get("/api/cart", params={"address_id": shopper.address_id}).json()
    assert withaddr["delivery_fee"] == 5.0 and withaddr["total"] == 50.0
    assert shopper.delete(f"/api/cart/items/{line['id']}").json()["items"] == []
    assert shopper.get("/api/cart").json()["vendor"] is None


def test_cart_rules(world, shopper):
    assert shopper.post("/api/cart/items", json={"menu_item_id": world.gone.id}).status_code == 409      # unavailable
    assert shopper.post("/api/cart/items", json={"menu_item_id": 9999}).status_code == 404
    assert shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id, "quantity": 99}).status_code == 422
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    r = shopper.post("/api/cart/items", json={"menu_item_id": world.farfood.id})                          # other vendor
    assert r.status_code == 409 and r.json()["detail"]["code"] == "different_vendor"
    r = shopper.post("/api/cart/items", json={"menu_item_id": world.farfood.id, "replace": True})
    assert r.status_code == 200 and r.json()["vendor"]["name"] == "Far Grill" and len(r.json()["items"]) == 1


def test_cart_requires_login_and_is_private(client, world, shopper, make_user, login):
    assert client.get("/api/cart").status_code == 401
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    other = login(make_user("o@example.com"))
    assert other.get("/api/cart").json()["items"] == []


# ---------------------------------------------------------------- checkout
def test_checkout_computes_prices_on_server_and_snapshots(world, shopper, place_order, db):
    o = place_order(qty=2)
    assert o["status"] == "PENDING_PAYMENT" and o["code"].startswith("KE-")
    assert (o["subtotal"], o["delivery_fee"], o["total"]) == (90.0, 5.0, 95.0)
    assert shopper.get("/api/cart").json()["items"] == []                       # cart emptied
    world.spicy.price = Decimal("99.00")                                        # vendor changes the price later
    db.commit()
    assert shopper.get(f"/api/orders/{o['id']}").json()["items"][0]["unit_price"] == 45.0   # past order unchanged
    assert [h["status"] for h in o["history"]] == ["PENDING_PAYMENT"]


def test_client_cannot_inject_prices(world, shopper):
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    r = shopper.post("/api/orders", json={"address_id": shopper.address_id, "total": 1, "subtotal": 1, "delivery_fee": 0})
    assert r.status_code == 201 and r.json()["total"] == 50.0                   # extra fields are ignored


def test_checkout_validation(world, shopper, make_user, login, db):
    assert shopper.post("/api/orders", json={"address_id": shopper.address_id}).json()["detail"]["code"] == "empty_cart"
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    assert shopper.post("/api/orders", json={"address_id": 9999}).status_code == 404
    other = login(make_user("o@example.com"))                                    # someone else's address
    assert other.post("/api/orders", json={"address_id": shopper.address_id}).status_code == 404
    far = shopper.post("/api/me/addresses", json={"label": "Tema", "address_line": "Comm 18", "latitude": 5.68, "longitude": -0.01}).json()
    r = shopper.post("/api/orders", json={"address_id": far["id"]})
    assert r.status_code == 409 and "doesn't deliver that far" in r.json()["detail"]["message"]
    world.near.is_open = False
    db.commit()
    assert shopper.post("/api/orders", json={"address_id": shopper.address_id}).status_code == 409   # vendor closed


def test_phone_required(world, make_user, login):
    c = login(make_user("nophone@example.com"))
    a = c.post("/api/me/addresses", json={"label": "H", "address_line": "Osu", "latitude": 5.557, "longitude": -0.182}).json()
    c.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    assert c.post("/api/orders", json={"address_id": a["id"]}).json()["detail"]["code"] == "phone_required"
    assert c.post("/api/orders", json={"address_id": a["id"], "phone": "+233200000000"}).status_code == 201


def test_customer_can_cancel_only_unpaid(world, shopper, place_order, db, make_user, login):
    o = place_order()
    assert login(make_user("x@example.com")).post(f"/api/orders/{o['id']}/cancel", json={}).status_code == 404
    assert shopper.post(f"/api/orders/{o['id']}/cancel", json={"reason": "Changed my mind"}).json()["status"] == "CANCELLED"
    o2 = place_order()
    order = db.get(Order, o2["id"]); order.status = S.PAID; db.commit()
    r = shopper.post(f"/api/orders/{o2['id']}/cancel", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cannot_cancel"


# ---------------------------------------------------------------- state machine
def advance(db, order_id, new, role, actor=None):
    o = svc.lock_order(db, order_id)
    svc.change_status(db, o, new, role, actor)
    db.commit()
    return o


def test_full_happy_path_and_history(world, place_order, db):
    o = place_order(qty=3)
    path = [(S.PAID, "system"), (S.CONFIRMED, "vendor"), (S.PREPARING, "vendor"), (S.READY_FOR_PICKUP, "vendor"),
            (S.RIDER_ASSIGNED, "rider"), (S.PICKED_UP, "rider"), (S.OUT_FOR_DELIVERY, "rider"), (S.DELIVERED, "rider")]
    for status, role in path:
        advance(db, o["id"], status, role)
    order = db.get(Order, o["id"])
    assert order.status == S.DELIVERED and order.paid_at and order.delivered_at
    assert [h.to_status for h in order.history][-1] == S.DELIVERED and len(order.history) == 9
    db.refresh(world.spicy)
    assert world.spicy.order_count == 9 + 3                                      # popularity bumped


@pytest.mark.parametrize("start,target", [
    (S.PENDING_PAYMENT, S.PREPARING), (S.PENDING_PAYMENT, S.DELIVERED), (S.PAID, S.PREPARING),
    (S.CONFIRMED, S.PAID), (S.DELIVERED, S.CONFIRMED), (S.CANCELLED, S.PAID), (S.REFUNDED, S.DELIVERED),
    (S.PICKED_UP, S.CANCELLED), (S.OUT_FOR_DELIVERY, S.PICKED_UP)])
def test_invalid_transitions_rejected_even_for_admin(world, place_order, db, start, target):
    o = place_order()
    order = db.get(Order, o["id"]); order.status = start; db.commit()
    with pytest.raises(HTTPException) as e:
        advance(db, o["id"], target, "admin")
    assert e.value.status_code == 409 and e.value.detail["code"] == "invalid_transition"


@pytest.mark.parametrize("start,target,role", [
    (S.PENDING_PAYMENT, S.PAID, "customer"), (S.PENDING_PAYMENT, S.PAID, "vendor"), (S.PAID, S.CONFIRMED, "customer"),
    (S.PAID, S.CONFIRMED, "rider"), (S.CONFIRMED, S.PREPARING, "rider"), (S.READY_FOR_PICKUP, S.RIDER_ASSIGNED, "vendor"),
    (S.OUT_FOR_DELIVERY, S.DELIVERED, "customer"), (S.PAID, S.CANCELLED, "rider")])
def test_wrong_actor_rejected(world, place_order, db, start, target, role):
    o = place_order()
    order = db.get(Order, o["id"]); order.status = start; db.commit()
    with pytest.raises(HTTPException) as e:
        advance(db, o["id"], target, role)
    assert e.value.status_code == 403


def test_payment_failed_can_retry_then_pay(world, place_order, db):
    o = place_order()
    advance(db, o["id"], S.PAYMENT_FAILED, "system")
    advance(db, o["id"], S.PENDING_PAYMENT, "customer")
    assert advance(db, o["id"], S.PAID, "system").status == S.PAID


# ---------------------------------------------------------------- vendor order handling
def paid(db, order_id):
    advance(db, order_id, S.PAID, "system")


def test_vendor_sees_only_own_paid_orders(world, shopper, place_order, vendor_user, login, db, make_user):
    o = place_order()
    v = login(vendor_user)
    assert v.get("/api/vendor/orders").json() == []                              # unpaid orders hidden
    paid(db, o["id"])
    assert [x["code"] for x in v.get("/api/vendor/orders").json()] == [o["code"]]
    assert v.get("/api/vendor/orders").json()[0]["customer_phone"] == "+233244000111"
    rival = make_user("rival@example.com", UserRole.VENDOR)
    world.far.owner_user_id = rival.id; db.commit()
    assert login(rival).get("/api/vendor/orders").json() == []
    assert login(rival).post(f"/api/vendor/orders/{o['id']}/status", json={"status": "CONFIRMED"}).status_code == 404


def test_vendor_accept_prepare_ready_and_reject(world, shopper, place_order, vendor_user, login, db):
    v = login(vendor_user)
    o = place_order(); paid(db, o["id"])
    for s in ["CONFIRMED", "PREPARING", "READY_FOR_PICKUP"]:
        assert v.post(f"/api/vendor/orders/{o['id']}/status", json={"status": s}).json()["status"] == s
    assert v.post(f"/api/vendor/orders/{o['id']}/status", json={"status": "PREPARING"}).status_code == 409   # can't go back
    o2 = place_order(); paid(db, o2["id"])
    assert v.post(f"/api/vendor/orders/{o2['id']}/status", json={"status": "CANCELLED"}).status_code == 422  # reason needed
    r = v.post(f"/api/vendor/orders/{o2['id']}/status", json={"status": "CANCELLED", "note": "Out of rice"}).json()
    assert r["status"] == "CANCELLED" and r["refund_required"] is True and r["cancelled_reason"] == "Out of rice"
    assert v.post(f"/api/vendor/orders/{o2['id']}/status", json={"status": "DELIVERED"}).status_code == 422  # not a vendor status
    assert {x["status"] for x in v.get("/api/vendor/orders", params={"view": "history"}).json()} == {"CANCELLED"}


def test_customers_cannot_use_vendor_endpoints(shopper):
    assert shopper.get("/api/vendor/orders").status_code in (403, 404)
