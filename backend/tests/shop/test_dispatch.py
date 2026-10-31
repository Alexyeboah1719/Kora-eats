from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.geo import angle_diff, bearing_deg
from app.core.timeutil import utcnow
from app.models.audit import AuditLog
from app.models.catalog import ApprovalStatus, Vendor
from app.models.notifications import Notification
from app.models.orders import FulfillmentType, Order, OrderItem, OrderStatus as S, OrderStatusHistory
from app.models.riders import Delivery
from app.models.user import UserRole
from app.services import dispatch_service as dsp, quote_service
from tests.shop.test_notifications import advance_to, titles

V1 = (5.5560, -0.1830)          # "Near Kitchen" (world.near)
C_NEAR = (5.5570, -0.1820)      # a customer 150 m from V1
_n = {"i": 0}


def raw_order(db, vendor, customer, drop, status=S.READY_FOR_PICKUP, ready_ago=0, scheduled=None, fulfillment=FulfillmentType.DELIVERY):
    _n["i"] += 1
    o = Order(code=f"D-{_n['i']:05d}", customer_id=customer.id, vendor_id=vendor.id, status=status, fulfillment_type=fulfillment, subtotal=Decimal(50),
              delivery_fee=Decimal(5), discount=Decimal(0), total=Decimal(55), delivery_address="x", delivery_lat=drop[0], delivery_lng=drop[1],
              customer_phone="+233244000111", scheduled_for=scheduled)
    o.items = [OrderItem(name="Jollof", unit_price=Decimal(50), quantity=1, line_total=Decimal(50))]
    o.history = [OrderStatusHistory(from_status=None, to_status=S.READY_FOR_PICKUP, actor_role="vendor", created_at=utcnow() - timedelta(minutes=ready_ago))]
    db.add(o); db.flush()
    return o


def put(rider, lat, lng, minutes_ago=0):
    rider.current_lat, rider.current_lng, rider.last_location_at = lat, lng, utcnow() - timedelta(minutes=minutes_ago)


def assign(db, rider, order):
    db.add(Delivery(order_id=order.id, rider_id=rider.id, rider_earning=Decimal(4)))


@pytest.fixture()
def cust(make_user):
    return make_user("dispatch-cust@example.com")


@pytest.fixture()
def near_two(world, db):
    v = Vendor(name="Near Two", latitude=5.5600, longitude=-0.1790, approval_status=ApprovalStatus.APPROVED, address_text="Osu", avg_prep_minutes=15)
    db.add(v); db.commit()
    return v


# ---------------------------------------------------------------- geometry and batching rules
def test_bearings():
    assert [round(bearing_deg(0, 0, *p)) for p in ((1, 0), (0, 1), (-1, 0), (0, -1))] == [0, 90, 180, 270]
    assert angle_diff(350, 10) == 20 and angle_diff(90, 270) == 180 and angle_diff(45, 45) == 0


def test_when_two_orders_can_share_a_ride():
    v1, c1 = V1, (5.600, -0.150)
    assert dsp.similar_direction(v1, c1, (5.5600, -0.1790), (5.605, -0.145)) is True            # vendors 0.6 km apart, customers 0.8 km apart, same direction
    assert dsp.similar_direction(v1, c1, (5.5600, -0.1790), (5.520, -0.220)) is False          # customer on the opposite side of town
    assert dsp.similar_direction(v1, c1, (5.6000, -0.1500), (5.605, -0.145)) is False          # vendors 6 km apart
    assert dsp.similar_direction(v1, c1, (5.5600, -0.1790), (5.640, -0.110)) is False          # customers 6+ km apart


# ---------------------------------------------------------------- who is the best rider?
def test_recommended_means_nearly_as_good_as_the_best(world, db, make_rider, cust):
    near, mid, far = (make_rider(f"{n}@example.com", name=n.title())[1] for n in ("nearby", "middling", "faraway"))
    put(near, 5.5565, -0.1830); put(mid, 5.5580, -0.1830); put(far, 5.6500, -0.1800); db.commit()
    o = raw_order(db, world.near, cust, C_NEAR); db.commit()
    assert [p.rider.id for p in dsp.recommend(db, o)] == [near.id, mid.id, far.id]                  # all ranked...
    assert [p.rider.id for p in dsp.recommended(db, o)] == [near.id, mid.id]                       # ...but the 10 km rider isn't "recommended"
    assert dsp.recommended(db, raw_order(db, world.near, cust, C_NEAR, fulfillment=FulfillmentType.PICKUP)) == []


def test_recommends_the_nearest_available_riders_with_honest_etas(world, db, make_rider, cust):
    a, b, c, d = (make_rider(f"r{i}@example.com", online=(i != 3), name=n)[1] for i, n in enumerate(["Ama A", "Bea B", "Cy C", "Dee D"]))
    put(a, 5.5580, -0.1830)                  # ~0.2 km
    put(b, 5.6000, -0.1800)                  # ~4.9 km
    put(d, 5.5561, -0.1830)                  # closest of all but OFFLINE
    db.commit()                              # c has no location at all
    o = raw_order(db, world.near, cust, C_NEAR); db.commit()
    plans = dsp.recommend(db, o)
    assert [p.rider.id for p in plans] == [a.id, b.id]                                            # no-location and offline riders can't be recommended
    first = plans[0]
    assert first.pickup_eta == quote_service.travel_minutes(first.pickup_km) and first.delivery_eta == first.pickup_eta + quote_service.travel_minutes(first.trip_km)
    assert plans[1].pickup_eta > first.pickup_eta and plans[1].score < first.score
    assert any("Online with no other deliveries" in r for r in first.reasons) and first.stale is False
    out = dsp.plan_out(first)
    assert out["name"] == "Ama A" and out["score"] > plans[1].score * 100 - 1 and out["batching"] is None


def test_stale_gps_counts_against_a_rider(world, db, make_rider, cust):
    fresh, old = make_rider("fresh@example.com", name="Fresh F")[1], make_rider("old@example.com", name="Old O")[1]
    put(fresh, 5.5580, -0.1830); put(old, 5.5580, -0.1830, minutes_ago=15); db.commit()          # identical positions
    plans = dsp.recommend(db, raw_order(db, world.near, cust, C_NEAR))
    assert [p.rider.id for p in plans] == [fresh.id, old.id] and plans[1].stale and any("over 10 minutes ago" in r for r in plans[1].reasons)


def test_riders_at_capacity_are_skipped_and_busy_riders_rank_lower(world, db, make_rider, cust, near_two):
    idle, busy, full = (make_rider(f"{n}@example.com", name=n.title())[1] for n in ("idle", "busy", "full"))
    for r in (idle, busy, full):
        put(r, 5.5580, -0.1830)
    far_drop = (5.640, -0.110)
    assign(db, busy, raw_order(db, near_two, cust, (5.520, -0.220), status=S.PICKED_UP))                # busy: one delivery in the opposite direction
    for _ in range(2):
        assign(db, full, raw_order(db, near_two, cust, far_drop, status=S.PICKED_UP))                    # full: already carrying two
    db.commit()
    plans = dsp.recommend(db, raw_order(db, world.near, cust, C_NEAR))
    assert [p.rider.id for p in plans] == [idle.id, busy.id]
    assert plans[1].active == 1 and any("Busy with 1 delivery" in r for r in plans[1].reasons) and plans[1].batching is None


def test_a_rider_already_collecting_nearby_gets_the_batched_order(world, db, make_rider, cust, near_two):
    carrier, idle = make_rider("carrier@example.com", name="Carrier C")[1], make_rider("idle@example.com", name="Idle I")[1]
    put(carrier, 5.5565, -0.1825)                                      # about to collect order X at V1
    put(idle, 5.6050, -0.1800)                                         # idle but ~5.5 km from the vendor
    x = raw_order(db, world.near, cust, (5.600, -0.150), status=S.RIDER_ASSIGNED); assign(db, carrier, x)
    db.commit()
    y = raw_order(db, near_two, cust, (5.605, -0.145))                 # nearby vendor, nearby customer, same direction
    db.commit()
    plans = dsp.recommend(db, y)
    assert plans[0].rider.id == carrier.id and plans[0].batching and plans[0].batching["with_order"] == x.code
    assert plans[0].batching["detour_minutes"] <= 8 and any("going the same direction" in r for r in plans[0].reasons)
    assert plans[0].delivery_eta < plans[1].delivery_eta
    opposite = raw_order(db, near_two, cust, (5.520, -0.220)); db.commit()                              # same vendor, but the customer is the other way
    carrier_plan = next(p for p in dsp.recommend(db, opposite) if p.rider.id == carrier.id)
    assert carrier_plan.batching is None                                                                   # no false batching


def test_pickup_orders_are_never_dispatched(world, db, make_rider, cust):
    put(make_rider()[1], 5.5580, -0.1830); db.commit()
    assert dsp.recommend(db, raw_order(db, world.near, cust, V1, fulfillment=FulfillmentType.PICKUP)) == []


# ---------------------------------------------------------------- priority
def test_order_priority(world, db, cust):
    fresh, waiting = raw_order(db, world.near, cust, C_NEAR, ready_ago=2), raw_order(db, world.near, cust, C_NEAR, ready_ago=15)
    soon = raw_order(db, world.near, cust, C_NEAR, ready_ago=1, scheduled=utcnow() + timedelta(minutes=20))
    later = raw_order(db, world.near, cust, C_NEAR, ready_ago=1, scheduled=utcnow() + timedelta(hours=5))
    db.commit()
    now = utcnow()                                                # taken AFTER the orders exist, so waits are measured correctly
    assert dsp.priority(fresh, now)["urgent"] is False and dsp.priority(fresh, now)["waiting_minutes"] == 2
    p = dsp.priority(waiting, now)
    assert p["urgent"] and p["waiting_minutes"] == 15 and p["reason"] == "Ready 15 min ago" and p["level"] == "urgent"
    assert dsp.priority(soon, now)["urgent"] and dsp.priority(soon, now)["reason"] == "Scheduled delivery is due soon"
    assert dsp.priority(later, now)["urgent"] is False


# ---------------------------------------------------------------- who gets told when food is ready
def test_only_the_best_riders_are_alerted_first(shopper, place_order, make_rider, db):
    riders = []
    for i, lat in enumerate((5.5565, 5.5600, 5.5700, 5.6100, 5.6500)):                                  # increasingly far from the vendor
        u, r = make_rider(f"rider{i}@example.com", name=f"Rider{i}")
        put(r, lat, -0.1830); riders.append(u)
    db.commit()
    advance_to(db, place_order()["id"], S.READY_FOR_PICKUP)
    told = [any(t == "Recommended for you" for t in titles(db, u)) for u in riders]
    assert told == [True, True, True, False, False]                                                        # the three best, nobody else
    assert not any("New delivery available" in titles(db, u) for u in riders)
    n = db.scalars(select(Notification).where(Notification.user_id == riders[0].id)).first()
    assert "km away" in n.body and "You'd earn GH₵4.00" in n.body


def test_everyone_online_is_alerted_when_nobody_has_a_location(shopper, place_order, make_rider, db):
    riders = [make_rider(f"nr{i}@example.com", name=f"NoLoc{i}")[0] for i in range(2)]
    advance_to(db, place_order()["id"], S.READY_FOR_PICKUP)
    assert all("New delivery available" in titles(db, u) for u in riders)                                  # the old behaviour still protects against silence


# ---------------------------------------------------------------- the rider's own list
def test_available_list_is_smart_ranked_and_private(world, db, make_rider, login, cust, near_two):
    near_u, near_r = make_rider("near@example.com", name="Near N")
    far_u, far_r = make_rider("faraway@example.com", name="Far F")
    put(near_r, 5.5562, -0.1830); put(far_r, 5.6500, -0.1800); db.commit()
    fresh = raw_order(db, world.near, cust, C_NEAR, ready_ago=1)
    urgent = raw_order(db, near_two, cust, (5.5700, -0.1700), ready_ago=14)
    db.commit()
    mine = login(near_u).get("/api/rider/available").json()
    assert [o["code"] for o in mine] == [urgent.code, fresh.code]                                          # urgent first
    assert mine[0]["priority"]["urgent"] and mine[0]["priority"]["reason"] == "Ready 14 min ago"
    assert mine[1]["recommended"] is True and mine[1]["smart"]["delivery_eta_minutes"] >= mine[1]["smart"]["pickup_eta_minutes"] and mine[1]["smart"]["reasons"]
    theirs = login(far_u).get("/api/rider/available").json()
    assert not any(o["recommended"] for o in theirs if o["code"] == fresh.code)                           # the far rider isn't the best fit for the near order
    blob = str(mine).lower()
    assert "far f" not in blob and "faraway" not in blob and "recommended_riders" not in blob              # a rider never sees other riders' details


# ---------------------------------------------------------------- admin dispatch
@pytest.fixture()
def admin(make_user, login):
    u = make_user("dispatcher@example.com", UserRole.ADMIN, "Dee Spatcher")
    c = login(u); c.user = u
    return c


def test_dispatch_board(world, db, make_rider, cust, admin, shopper, login):
    r = make_rider("board@example.com", name="Board B")[1]; put(r, 5.5580, -0.1830)
    calm, hot = raw_order(db, world.near, cust, C_NEAR, ready_ago=1), raw_order(db, world.near, cust, C_NEAR, ready_ago=20)
    raw_order(db, world.near, cust, V1, fulfillment=FulfillmentType.PICKUP); db.commit()
    b = admin.get("/api/admin/dispatch").json()
    assert [x["order"]["code"] for x in b["orders"]] == [hot.code, calm.code]                              # most urgent first; pickup orders excluded
    top = b["orders"][0]
    assert top["priority"]["urgent"] and top["recommended"][0]["name"] == "Board B" and top["order"]["earning"] == 4.0
    assert b["online_riders"][0]["name"] == "Board B"
    assert login(cust).get("/api/admin/dispatch").status_code == 403


def test_admin_assigns_a_rider_safely(world, db, make_rider, cust, admin, login):
    ru, rider = make_rider("assign@example.com", name="Assign A")
    o = raw_order(db, world.near, cust, C_NEAR); db.commit()
    r = admin.post(f"/api/admin/dispatch/{o.id}/assign", json={"rider_id": rider.id})
    assert r.status_code == 200 and r.json()["status"] == "RIDER_ASSIGNED" and r.json()["rider"] == "Assign A"
    db.expire_all()
    order = db.get(Order, o.id)
    assert order.history[-1].actor_role == "admin" and "Assigned to Assign A by dispatch" in order.history[-1].note
    assert db.scalar(select(Delivery.rider_id).where(Delivery.order_id == o.id)) == rider.id
    assert "Delivery assigned to you" in titles(db, ru) and db.scalar(select(AuditLog.id).where(AuditLog.action == "dispatch_assigned"))
    assert admin.post(f"/api/admin/dispatch/{o.id}/assign", json={"rider_id": rider.id}).json()["detail"]["code"] == "already_taken"
    assert login(ru).post(f"/api/rider/deliveries/{o.id}/accept").json()["detail"]["code"] == "already_taken"   # and no rider can grab it too
    assert login(ru).post(f"/api/rider/deliveries/{o.id}/pickup").status_code == 200                            # but the assigned rider carries on normally


def test_admin_assignment_guards(world, db, make_rider, cust, admin):
    busy = make_rider("busy@example.com", name="Busy B")[1]
    for _ in range(2):
        assign(db, busy, raw_order(db, world.near, cust, (5.64, -0.11), status=S.PICKED_UP))
    pending = make_rider("pending@example.com", name="Pending P")[1]
    pending.approval_status = ApprovalStatus.PENDING
    o = raw_order(db, world.near, cust, C_NEAR); pick = raw_order(db, world.near, cust, V1, fulfillment=FulfillmentType.PICKUP); db.commit()
    url = lambda oid: f"/api/admin/dispatch/{oid}/assign"
    assert admin.post(url(o.id), json={"rider_id": busy.id}).json()["detail"]["code"] == "rider_busy"
    assert admin.post(url(o.id), json={"rider_id": pending.id}).json()["detail"]["code"] == "rider_not_approved"
    assert admin.post(url(o.id), json={"rider_id": 9999}).status_code == 404
    assert admin.post(url(pick.id), json={"rider_id": busy.id, "force": True}).json()["detail"]["code"] == "pickup_order"
    assert admin.post(url(99999), json={"rider_id": busy.id, "force": True}).status_code == 404
    assert admin.post(url(o.id), json={"rider_id": busy.id, "force": True}).status_code == 200                # an explicit override is allowed (and audited)
