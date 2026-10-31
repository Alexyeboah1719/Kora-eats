import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.catalog import MenuItem, Vendor, WasteLog
from app.models.orders import Order, OrderItem, OrderStatus as S
from app.models.user import UserRole
from app.services import forecast_service as fs
from app.services.forecast_service import History

NOW = datetime(2026, 10, 16, 15, 30, tzinfo=timezone.utc)          # a Friday
TODAY = NOW.date()


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(fs, "utcnow", lambda: NOW)


_n = {"i": 0}


def add_order(db, vendor, customer, when, items, status=S.DELIVERED, scheduled_for=None, paid=True):
    _n["i"] += 1
    total = sum(Decimal(p) * q for _, p, q in items)
    o = Order(code=f"T-{_n['i']:06d}", customer_id=customer.id, vendor_id=vendor.id, status=status, subtotal=total, delivery_fee=Decimal(0), discount=Decimal(0),
              total=total, delivery_address="x", delivery_lat=5.55, delivery_lng=-0.18, customer_phone="+233244000111", created_at=when,
              paid_at=when if paid else None, scheduled_for=scheduled_for)
    o.items = [OrderItem(name=n, unit_price=Decimal(p), quantity=q, line_total=Decimal(p) * q) for n, p, q in items]
    db.add(o)


def at(day: date, hour: int, minute: int = 0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture()
def busy(world, db, make_user, vendor_user):
    """Eight weeks of a clear pattern. Fridays: 4 breakfast orders + 12 each at 12:30, 13:00, 13:30 (= 40). Mondays: 10 at noon."""
    cust = make_user("regular@example.com")
    for k in range(1, 9):
        fri = TODAY - timedelta(days=7 * k)
        for _ in range(4):
            add_order(db, world.near, cust, at(fri, 9, 5), [("Waakye", "20.00", 1)])
        for hh, mm in ((12, 40), (13, 10), (13, 40)):
            for _ in range(12):
                add_order(db, world.near, cust, at(fri, hh, mm), [("Jollof", "45.00", 1)])
    for k in range(0, 8):                                      # Mondays, including THIS week's (so recent and earlier fortnights are equal)
        mon = TODAY - timedelta(days=4 + 7 * k)
        for _ in range(10):
            add_order(db, world.near, cust, at(mon, 12, 5), [("Plain Rice", "20.00", 1)])
    db.commit()
    return world


# ---------------------------------------------------------------- pure maths
def test_slot_labels_and_peak_windows():
    assert [fs.slot_label(s) for s in (0, 25, 27, 28, 47)] == ["12:00 AM", "12:30 PM", "1:30 PM", "2:00 PM", "11:30 PM"]
    assert fs.window_label(25, 28) == "12:30 PM - 2:00 PM"
    assert fs.peak_windows([0] * 25 + [12, 12, 12] + [0] * 20) == [(25, 28, 36)]
    assert fs.peak_windows([0.1] * 48) == []                                                     # too little demand to call anything a peak
    assert fs.peak_windows([0.5] * 48) == []                                                     # steady all day: no peak to report
    two = [0] * 48
    two[17:19], two[36:39] = [8, 8], [10, 10, 10]
    assert [w[:2] for w in fs.peak_windows(two)] == [(36, 39), (17, 19)]                         # lunch-ish and evening, busiest first


@pytest.mark.parametrize("days,orders,level", [(13, 500, "insufficient"), (14, 19, "insufficient"), (14, 20, "low"), (27, 300, "low"),
                                               (28, 60, "medium"), (55, 300, "medium"), (56, 200, "high")])
def test_confidence_levels(days, orders, level):
    h = History(today=TODAY, first_date=TODAY - timedelta(days=days - 1), orders=orders)
    assert fs.confidence(h)["level"] == level


def hist_with(per_day: dict[date, int]) -> History:
    h = History(today=TODAY)
    for d, n in per_day.items():
        h.by_date[d][25] = n
        h.orders += n
        h.first_date = d if h.first_date is None or d < h.first_date else h.first_date
    return h


def test_trend_factor_is_gentle_and_bounded():
    flat = {TODAY - timedelta(days=i): 5 for i in range(1, 29)}
    assert fs.trend_factor(hist_with(flat)) == 1.0
    up = {TODAY - timedelta(days=i): (10 if i <= 14 else 5) for i in range(1, 29)}
    assert fs.trend_factor(hist_with(up)) == pytest.approx(math.sqrt(1.4))                       # doubled, but clipped at +40% and damped
    down = {TODAY - timedelta(days=i): (1 if i <= 14 else 5) for i in range(1, 29)}
    assert fs.trend_factor(hist_with(down)) == pytest.approx(math.sqrt(0.7))
    assert fs.trend_factor(hist_with({TODAY - timedelta(days=20): 5})) == 1.0                    # not enough earlier data to claim a trend


def test_recent_weeks_count_more_than_old_ones():
    fridays = {TODAY - timedelta(days=7 * k): (20 if k <= 2 else 60) for k in range(1, 9)}        # busy long ago, quiet recently
    h = hist_with(fridays)
    h.by_date[TODAY - timedelta(days=1)][25] = 30                                                  # ensure >=20 orders & 14 days
    means, totals, n = fs.slot_means(h, TODAY + timedelta(days=7))
    assert n == 7 and sum(means) < sum(totals) / n                                                 # 7 same-weekday samples (today is incomplete); below the plain average
    assert sum(means) > 20


def test_interval_is_ordered_and_never_negative():
    sd, low, high = fs.interval(42.0, [38, 42, 40, 44, 36])
    assert low < 42 < high and sd > 0
    sd, low, high = fs.interval(0.0, [])
    assert (low, high) == (0, 0)
    _, low, high = fs.interval(40.0, [40] * 8)                                                     # perfectly steady days still get a +-1 band
    assert (low, high) == (39, 41)


# ---------------------------------------------------------------- the report
def test_future_friday_forecast_matches_the_pattern(busy, db, vendor_user):
    r = fs.report(db, busy.near, TODAY + timedelta(days=7))
    assert r["confidence"]["level"] == "high" and r["weekday"] == "Friday"
    t = r["orders"]["expected_total"]
    assert t["mean"] == 40.0 and t["low"] <= 40 <= t["high"] and (t["low"], t["high"]) == (39, 41)
    assert [p["label"] for p in r["peak_periods"]] == ["12:30 PM - 2:00 PM"] and r["peak_periods"][0]["expected_orders"] == 36
    jollof, waakye = r["popular_meals"][0], r["popular_meals"][1]
    assert jollof["name"] == "Jollof" and jollof["expected"]["mean"] == 36.0 and jollof["peak"]["start"] == "12:30 PM" and jollof["peak"]["end"] == "2:00 PM"
    assert waakye["name"] == "Waakye" and waakye["expected"]["mean"] == 4.0
    texts = [x["text"] for x in r["recommendations"]]
    assert "Expected orders on Friday: 39-41" in texts and "Peak period: 12:30 PM - 2:00 PM" in texts
    assert any(t.startswith("Recommended: increase preparation capacity for Jollof between 12:30 PM and 2:00 PM") for t in texts)
    assert len(r["hourly"]["labels"]) == len(r["hourly"]["values"]) == 48 and abs(sum(r["hourly"]["values"]) - 40) < 0.5
    week = {d["weekday"]: d for d in r["week_ahead"]}
    assert week["Friday"]["mean"] == 40.0 and week["Monday"]["mean"] == 10.0 and week["Saturday"]["mean"] == 0.0 and len(r["week_ahead"]) == 7
    assert "not a guarantee" in r["basis"]


def test_today_combines_what_has_happened_with_what_is_expected(busy, db, make_user, monkeypatch):
    cust = make_user("c2@example.com")
    for _ in range(4):
        add_order(db, busy.near, cust, at(TODAY, 9, 5), [("Waakye", "20.00", 1)])
    db.commit()
    monkeypatch.setattr(fs, "utcnow", lambda: at(TODAY, 11, 0))                                    # before the lunch rush
    r = fs.report(db, busy.near, TODAY, at(TODAY, 11, 0))
    o = r["orders"]
    assert o["so_far"] == 4 and o["remaining"]["mean"] == pytest.approx(36.0, abs=0.5)
    assert o["expected_total"]["mean"] == pytest.approx(40.0, abs=0.5) and "Expected orders today:" in r["recommendations"][0]["text"]
    late = fs.report(db, busy.near, TODAY, at(TODAY, 15, 30))["orders"]                            # after the rush: nothing left to expect
    assert late["remaining"]["mean"] == 0 and late["expected_total"]["mean"] == 4.0


def test_not_enough_history_is_said_plainly_with_a_peer_benchmark(world, db, make_user):
    cust = make_user("c@example.com")
    for d in range(3):
        for _ in range(3):
            add_order(db, world.near, cust, at(TODAY - timedelta(days=d + 1), 12), [("Jollof", "45.00", 1)])
    for i in range(3):                                                                              # three similar vendors with real volume
        v = Vendor(name=f"Peer {i}", latitude=5.5, longitude=-0.2, approval_status=world.near.approval_status, business_type=world.near.business_type, address_text="x")
        db.add(v); db.flush()
        for _ in range(14):
            add_order(db, v, cust, at(TODAY - timedelta(days=5), 13), [("Rice", "20.00", 1)])
    db.commit()
    r = fs.report(db, world.near)
    assert r["confidence"]["level"] == "insufficient" and "Not enough history" in r["confidence"]["message"]
    assert r["peak_periods"] == [] and r["popular_meals"] == [] and r["week_ahead"] == [] and "expected_total" not in r["orders"]     # no invented numbers
    assert r["benchmark"] == {"similar_vendors": 3, "avg_orders_per_day": 0.5}
    assert any("benchmark, not a forecast" in x["text"] for x in r["recommendations"])


def test_only_real_paid_orders_of_this_vendor_count(world, db, make_user):
    cust = make_user("c@example.com")
    for k in range(1, 7):
        d = TODAY - timedelta(days=k)
        for _ in range(4):
            add_order(db, world.near, cust, at(d, 12), [("Jollof", "45.00", 1)])
        add_order(db, world.near, cust, at(d, 12), [("Jollof", "45.00", 1)], status=S.CANCELLED)                 # cancelled: ignored
        add_order(db, world.near, cust, at(d, 12), [("Jollof", "45.00", 1)], status=S.PENDING_PAYMENT, paid=False)  # never paid: ignored
        add_order(db, world.far, cust, at(d, 12), [("Jollof", "45.00", 1)])                                      # another vendor: ignored
    db.commit()
    h = fs.load_history(db, world.near.id, NOW)
    assert h.orders == 24 and h.first_date == TODAY - timedelta(days=6)


def test_booked_scheduled_orders_are_surfaced(busy, db, make_user):
    cust = make_user("c3@example.com")
    target = TODAY + timedelta(days=7)
    add_order(db, busy.near, cust, at(TODAY, 10), [("Jollof", "45.00", 1)], status=S.PAID, scheduled_for=at(target, 13))
    db.commit()
    r = fs.report(db, busy.near, target)
    assert r["booked_scheduled_orders"] == 1 and any("scheduled order(s) already booked" in x["text"] for x in r["recommendations"])


# ---------------------------------------------------------------- food waste advice
def test_waste_advice_uses_planned_portions_logs_and_slow_sellers(busy, db):
    jollof = MenuItem(vendor_id=busy.near.id, category_id=busy.jollof.id, name="Jollof", price=Decimal("45.00"), daily_prep_qty=80)
    waakye = MenuItem(vendor_id=busy.near.id, category_id=busy.jollof.id, name="Waakye", price=Decimal("20.00"), daily_prep_qty=1)
    stew = MenuItem(vendor_id=busy.near.id, category_id=busy.jollof.id, name="Fish Stew", price=Decimal("30.00"))      # never sells
    db.add_all([jollof, waakye, stew]); db.flush()
    for d in range(3):
        db.add(WasteLog(vendor_id=busy.near.id, menu_item_id=stew.id, log_date=TODAY - timedelta(days=d + 1), prepared_qty=20, leftover_qty=8))
    db.commit()
    r = fs.report(db, busy.near, TODAY + timedelta(days=7))
    by = {(w["item"], w["type"]): w for w in r["waste"]}
    over = by[("Jollof", "overprep")]
    assert "plan 80 portions of Jollof" in over["message"] and over["suggested_portions"] < 80
    assert ("Waakye", "underprep") in by and "may sell out early" in by[("Waakye", "underprep")]["message"]
    assert "40% of Fish Stew was left over" in by[("Fish Stew", "leftovers")]["message"] and "Fish Stew" in by[("Fish Stew", "slow")]["message"]
    assert ("Plain Rice", "overprep") not in by                                                                        # no planned portions -> no claim


# ---------------------------------------------------------------- API
def test_intelligence_api_is_vendor_only_and_validated(busy, vendor_user, login, shopper, client, db, make_user):
    v = login(vendor_user)
    ok = v.get("/api/vendor/intelligence", params={"date": (TODAY + timedelta(days=7)).isoformat()})
    assert ok.status_code == 200 and ok.json()["orders"]["expected_total"]["mean"] == 40.0
    assert v.get("/api/vendor/intelligence").json()["is_today"] is True
    for bad in ((TODAY - timedelta(days=1)).isoformat(), (TODAY + timedelta(days=15)).isoformat(), "tomorrow", "2026-13-40"):
        assert v.get("/api/vendor/intelligence", params={"date": bad}).status_code == 422
    assert client.get("/api/vendor/intelligence").status_code == 401 and shopper.get("/api/vendor/intelligence").status_code in (403, 404)
    rival = make_user("rival@example.com", UserRole.VENDOR); busy.far.owner_user_id = rival.id; db.commit()
    assert login(rival).get("/api/vendor/intelligence").json()["confidence"]["level"] == "insufficient"          # sees only their own (empty) history


def test_planned_portions_and_leftover_logging(busy, vendor_user, login, db, make_user):
    v = login(vendor_user)
    item = busy.plain
    assert v.put(f"/api/vendor/menu/{item.id}/prep-qty", json={"daily_prep_qty": 25}).json()["daily_prep_qty"] == 25
    assert v.put(f"/api/vendor/menu/{item.id}/prep-qty", json={"daily_prep_qty": 0}).status_code == 422
    assert v.put(f"/api/vendor/menu/{item.id}/prep-qty", json={"daily_prep_qty": None}).json()["daily_prep_qty"] is None
    body = {"menu_item_id": item.id, "log_date": (TODAY - timedelta(days=1)).isoformat(), "prepared_qty": 20, "leftover_qty": 5}
    assert v.post("/api/vendor/waste", json=body).status_code == 200
    assert v.post("/api/vendor/waste", json={**body, "leftover_qty": 6}).status_code == 200                            # same day again: updated, not duplicated
    logs = v.get("/api/vendor/waste").json()
    assert len(logs) == 1 and logs[0]["leftover"] == 6 and logs[0]["leftover_percent"] == 30
    assert v.post("/api/vendor/waste", json={**body, "leftover_qty": 21}).status_code == 422                          # more left than made
    assert v.post("/api/vendor/waste", json={**body, "log_date": (TODAY + timedelta(days=1)).isoformat()}).status_code == 422
    rival = make_user("rival@example.com", UserRole.VENDOR); busy.far.owner_user_id = rival.id; db.commit()
    assert login(rival).post("/api/vendor/waste", json=body).status_code == 404                                       # someone else's dish
    assert login(rival).put(f"/api/vendor/menu/{item.id}/prep-qty", json={"daily_prep_qty": 5}).status_code == 404


def test_report_is_deterministic_and_query_count_is_small(busy, db, test_engine):
    from sqlalchemy import event
    a = fs.report(db, busy.near, TODAY + timedelta(days=7))
    n = {"q": 0}
    listener = lambda *x, **k: n.__setitem__("q", n["q"] + 1)
    event.listen(test_engine, "before_cursor_execute", listener)
    try:
        b = fs.report(db, busy.near, TODAY + timedelta(days=7))
    finally:
        event.remove(test_engine, "before_cursor_execute", listener)
    assert a == b and n["q"] <= 12
