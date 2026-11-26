import random
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.orders import Order, OrderStatus as S
from app.models.promos import Coupon, CouponType
from app.models.user import UserRole
from app.models.wallet import LedgerEntry, LedgerKind as K
from app.services import wallet_service as ws
from tests.shop.test_notifications import titles
from tests.shop.test_payments import pay, simulate

D = Decimal


def fake_order(subtotal, fee, discount=0, delivery_part=0, funded=None, coupon="X"):
    total = D(subtotal) + D(fee) - D(discount)
    return SimpleNamespace(subtotal=D(subtotal), delivery_fee=D(fee), discount=D(discount), discount_delivery_part=D(delivery_part), coupon_funded_by=funded,
                           coupon_code=coupon, total=total)


# ---------------------------------------------------------------- the arithmetic (pure)
def test_a_normal_order():
    e = ws.economics(fake_order(90, 5), vendor_pct=D(10), rider_keep_pct=D(80))
    assert (e.vendor_commission, e.vendor_net, e.rider_commission, e.rider_net, e.platform_net) == (D("9.00"), D("81.00"), D("1.00"), D("4.00"), D("10.00"))
    assert e.vendor_net + e.rider_net + e.platform_net == e.customer_paid == D("95.00")


@pytest.mark.parametrize("name,order,vendor_net,rider_net,platform_net", [
    ("platform coupon: the vendor is not charged", fake_order(90, 5, 9, 0, "PLATFORM"), "81.00", "4.00", "1.00"),                    # 9 + 1 commission - 9 discount
    ("vendor coupon: the vendor bears it", fake_order(90, 5, 9, 0, "VENDOR"), "72.90", "4.00", "9.10"),                              # base 81 -> 8.10 commission
    ("platform free delivery: rider still paid in full", fake_order(90, 5, 5, 5, "PLATFORM"), "81.00", "4.00", "5.00"),
    ("vendor free delivery: the vendor pays the fee", fake_order(90, 5, 5, 5, "VENDOR"), "76.00", "4.00", "10.00"),                 # 90 - 5 - 9
    ("unknown funder on an old order: never the vendor", fake_order(90, 5, 9, 0, None), "81.00", "4.00", "1.00"),
])
def test_who_pays_for_discounts(name, order, vendor_net, rider_net, platform_net):
    e = ws.economics(order, vendor_pct=D(10), rider_keep_pct=D(80))
    assert (e.vendor_net, e.rider_net, e.platform_net) == (D(vendor_net), D(rider_net), D(platform_net)), name
    assert e.vendor_net + e.rider_net + e.platform_net == e.customer_paid                                                          # conservation


def test_pickup_has_no_rider_share():
    e = ws.economics(fake_order(40, 0), rider_involved=False, vendor_pct=D(10), rider_keep_pct=D(80))
    assert (e.rider_net, e.rider_commission, e.vendor_net, e.platform_net) == (D("0.00"), D("0.00"), D("36.00"), D("4.00"))


def test_the_three_shares_always_add_up_to_what_the_customer_paid():
    rnd = random.Random(11)
    for _ in range(600):
        S_ = D(rnd.randint(500, 40000)) / 100
        F = D(rnd.randint(0, 3000)) / 100
        kind = rnd.choice(["none", "pct", "free_delivery"])
        disc = dd = D(0)
        if kind == "pct":
            disc = (S_ * D(rnd.randint(1, 50)) / 100).quantize(D("0.01"))
        elif kind == "free_delivery":
            disc = dd = F
        o = fake_order(S_, F, disc, dd, rnd.choice(["PLATFORM", "VENDOR", None]))
        e = ws.economics(o, rider_involved=F > 0 or rnd.random() < .5, vendor_pct=D(rnd.choice([0, 5, 7.5, 10, 12.5, 33])), rider_keep_pct=D(rnd.choice([0, 60, 75, 80, 100])))
        assert e.vendor_net + e.rider_net + e.platform_net == e.customer_paid, (S_, F, disc, kind)
        assert e.vendor_commission >= 0 and e.rider_commission >= 0 and e.rider_net >= 0
        if e.funded_by != "VENDOR":
            assert e.vendor_net == e.subtotal - e.vendor_commission                         # a platform coupon never reduces what the vendor keeps


# ---------------------------------------------------------------- a real order, end to end
@pytest.fixture()
def crew(world, vendor_user, make_rider, login, shopper):
    ru, rider = make_rider("wallet-rider@example.com", name="Kwesi Rider")
    return SimpleNamespace(w=world, vendor=login(vendor_user), rider_user=ru, rider=rider, shopper=shopper, login=login, vendor_user=vendor_user)


def deliver(c, order):
    """Pay, prepare, ride, deliver: through the real API."""
    simulate(c.shopper, pay(c.shopper, order["id"], key=f"k-{order['id']}-wallet").json()["reference"])
    assert c.shopper.get(f"/api/payments/orders/{order['id']}/verify").json()["status"] == "PAID"
    for st in ("CONFIRMED", "PREPARING", "READY_FOR_PICKUP"):
        assert c.vendor.post(f"/api/vendor/orders/{order['id']}/status", json={"status": st}).status_code == 200
    rc = c.login(c.rider_user)
    for step in ("accept", "pickup", "out-for-delivery", "arrived", "delivered"):
        assert rc.post(f"/api/rider/deliveries/{order['id']}/{step}").status_code == 200, step


def entries(db, order_id):
    return {(e.account_id, e.kind): e for e in db.scalars(select(LedgerEntry).where(LedgerEntry.order_id == order_id))}


def test_completing_an_order_pays_everyone_correctly(crew, place_order, db):
    order = place_order()
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0                                  # nothing is earned before delivery
    deliver(crew, order)
    v, r, p = ws.vendor_account(db, crew.w.near.id), ws.rider_account(db, crew.rider.id), ws.platform_account(db)
    e = entries(db, order["id"])
    assert e[(v.id, K.ORDER_SALE)].amount == D("90.00") and e[(v.id, K.ORDER_COMMISSION)].amount == D("-9.00")
    assert e[(r.id, K.DELIVERY_PAY)].amount == D("5.00") and e[(r.id, K.DELIVERY_COMMISSION)].amount == D("-1.00")
    assert e[(p.id, K.ORDER_COMMISSION)].amount == D("9.00") and e[(p.id, K.DELIVERY_COMMISSION)].amount == D("1.00")
    total = db.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.order_id == order["id"]))
    assert total == D("95.00") == D(str(order["total"]))                                                  # every cedi the customer paid is accounted for
    meta = e[(v.id, K.ORDER_COMMISSION)].meta
    assert meta["vendor_rate"] == "10.0" and meta["order"] == order["code"]
    assert "You earned money" in titles(db, crew.vendor_user) and "You earned money" in titles(db, crew.rider_user)


def test_earnings_are_held_then_become_available(crew, place_order, db):
    deliver(crew, place_order())
    v, r = ws.vendor_account(db, crew.w.near.id), ws.rider_account(db, crew.rider.id)
    b = ws.balances(db, v.id)
    assert (b["available"], b["pending"], b["withdrawable"], b["lifetime_earnings"], b["commission_paid"]) == (D("0.00"), D("81.00"), D("0.00"), D("81.00"), D("9.00"))
    later = utcnow() + timedelta(hours=get_settings().payout_hold_hours + 1)
    b2 = ws.balances(db, v.id, now=later)
    assert (b2["available"], b2["pending"], b2["withdrawable"]) == (D("81.00"), D("0.00"), D("81.00"))
    assert ws.balances(db, r.id, now=later)["withdrawable"] == D("4.00")


def test_posting_twice_never_pays_twice(crew, place_order, db):
    order = place_order(); deliver(crew, order)
    before = db.scalar(select(func.count()).select_from(LedgerEntry))
    o = db.get(Order, order["id"])
    assert ws.post_order(db, o) is False                                                                  # already posted
    o.earnings_posted_at = None                                                                          # even if the marker were lost, the database refuses a duplicate
    assert ws.post_order(db, o) is False
    db.rollback()
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == before


def test_each_person_sees_a_clear_statement_for_every_order(crew, place_order, db):
    deliver(crew, place_order())
    st = ws.statement(db, ws.vendor_account(db, crew.w.near.id))
    assert len(st) == 1 and st[0]["you_earn"] == 81.0 and st[0]["state"] == "HELD" and st[0]["order_status"] == "DELIVERED"
    labels = {p["kind"]: p["amount"] for p in st[0]["parts"]}
    assert labels == {"ORDER_SALE": 90.0, "ORDER_COMMISSION": -9.0} and st[0]["rates"]["vendor_commission"] == "10.0"
    assert any("Held until" in w for w in st[0]["warnings"])
    rs = ws.statement(db, ws.rider_account(db, crew.rider.id))
    assert rs[0]["you_earn"] == 4.0 and {p["kind"]: p["amount"] for p in rs[0]["parts"]} == {"DELIVERY_PAY": 5.0, "DELIVERY_COMMISSION": -1.0}
    assert rs[0]["rates"]["rider_deduction"] == "20"


def test_orders_in_progress_show_what_will_be_earned(crew, place_order, db):
    order = place_order()
    simulate(crew.shopper, pay(crew.shopper, order["id"]).json()["reference"]); crew.shopper.get(f"/api/payments/orders/{order['id']}/verify")
    up = ws.upcoming(db, vendor_id=crew.w.near.id)
    assert up[0]["code"] == order["code"] and up[0]["you_will_earn"] == 81.0
    assert ws.upcoming(db, rider_id=crew.rider.id) == []                                                  # no rider has accepted it yet


def test_a_refund_after_completion_takes_the_earnings_back(crew, place_order, db, make_user, login):
    order = place_order(); deliver(crew, order)
    admin = login(make_user("refunder@example.com", UserRole.ADMIN))
    assert admin.post(f"/api/admin/orders/{order['id']}/status", json={"status": "REFUNDED"}).status_code == 200
    v, r, p = ws.vendor_account(db, crew.w.near.id), ws.rider_account(db, crew.rider.id), ws.platform_account(db)
    for acct in (v, r, p):
        assert ws.balances(db, acct.id)["total"] == D("0.00")                                              # everyone is back to zero
    assert db.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.order_id == order["id"])) == D("0.00")     # the customer got everything back
    st = ws.statement(db, v)[0]
    assert st["state"] == "REFUNDED" and any("Refunded" in w for w in st["warnings"])
    assert ws.reverse_order(db, db.get(Order, order["id"])) is False                                       # reversing twice does nothing


def test_a_cancelled_order_earns_nothing(crew, place_order, db):
    order = place_order()
    crew.shopper.post(f"/api/orders/{order['id']}/cancel")
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_pickup_orders_pay_the_vendor_only(crew, shopper, db):
    from tests.shop.test_pickup import place_pickup
    o = place_pickup(shopper, crew.w.spicy)
    simulate(shopper, pay(shopper, o["id"]).json()["reference"]); shopper.get(f"/api/payments/orders/{o['id']}/verify")
    for st in ("CONFIRMED", "PREPARING", "READY_FOR_PICKUP"):
        crew.vendor.post(f"/api/vendor/orders/{o['id']}/status", json={"status": st})
    assert crew.vendor.post(f"/api/vendor/orders/{o['id']}/collect", json={"code": o["pickup_code"]}).status_code == 200
    kinds = {k for (_, k) in entries(db, o["id"])}
    assert K.ORDER_SALE in kinds and K.DELIVERY_PAY not in kinds
    assert db.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.order_id == o["id"])) == D("90.00")


# ---------------------------------------------------------------- who pays for coupons, for real
@pytest.mark.parametrize("coupon_kwargs,vendor_net,platform_net,funded", [
    ({"type": CouponType.PERCENT, "value": D(10)}, "81.00", "1.00", "PLATFORM"),                          # an admin coupon: platform pays the 9.00 discount
    ({"type": CouponType.PERCENT, "value": D(10), "vendor": True}, "72.90", "9.10", "VENDOR"),             # the vendor's own coupon: the vendor pays
    ({"type": CouponType.FREE_DELIVERY, "value": D(0)}, "81.00", "5.00", "PLATFORM"),
])
def test_coupon_costs_land_on_the_right_party(crew, shopper, db, coupon_kwargs, vendor_net, platform_net, funded):
    kw = dict(coupon_kwargs)
    own = kw.pop("vendor", False)
    db.add(Coupon(code="SAVE10", vendor_id=crew.w.near.id if own else None, **kw)); db.commit()
    shopper.post("/api/cart/items", json={"menu_item_id": crew.w.spicy.id, "quantity": 2})
    assert shopper.post("/api/cart/coupon", json={"code": "SAVE10"}).status_code == 200
    order = shopper.post("/api/orders", json={"address_id": shopper.address_id}).json()
    assert db.get(Order, order["id"]).coupon_funded_by == funded
    deliver(crew, order)
    v, p = ws.vendor_account(db, crew.w.near.id), ws.platform_account(db)
    assert ws.balances(db, v.id)["total"] == D(vendor_net)
    assert ws.balances(db, p.id)["total"] == D(platform_net)
    assert ws.balances(db, ws.rider_account(db, crew.rider.id).id)["total"] == D("4.00")               # the rider is paid in full whoever funds the coupon
    assert db.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.order_id == order["id"])) == D(str(order["total"]))


def test_changing_the_rates_only_affects_new_orders(crew, place_order, db, monkeypatch):
    first = place_order(); deliver(crew, first)
    monkeypatch.setattr(get_settings(), "vendor_commission_percent", 20.0)
    monkeypatch.setattr(get_settings(), "rider_share_percent", 70)
    crew.shopper.post("/api/cart/items", json={"menu_item_id": crew.w.spicy.id, "quantity": 2})
    second = crew.shopper.post("/api/orders", json={"address_id": crew.shopper.address_id}).json(); deliver(crew, second)
    st = {s["order_id"]: s for s in ws.statement(db, ws.vendor_account(db, crew.w.near.id))}
    assert (st[first["id"]]["you_earn"], st[first["id"]]["rates"]["vendor_commission"]) == (81.0, "10.0")      # history is untouched
    assert (st[second["id"]]["you_earn"], st[second["id"]]["rates"]["vendor_commission"]) == (72.0, "20.0")
    rs = {s["order_id"]: s for s in ws.statement(db, ws.rider_account(db, crew.rider.id))}
    assert rs[first["id"]]["you_earn"] == 4.0 and rs[second["id"]]["you_earn"] == 3.5 and rs[second["id"]]["rates"]["rider_deduction"] == "30"
