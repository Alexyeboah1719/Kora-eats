"""Vendor Intelligence: demand forecasting, peak periods, popular meals, preparation and food-waste advice.

METHOD (deliberately simple and explainable): for a target day we take the SAME WEEKDAY over the last 8 weeks, weight recent weeks
more (x0.85 per week back), count orders in 30-minute slots, and adjust gently for the recent trend (last 14 days vs the 14 before).
The range shown is roughly the middle 50% (mean +/- 0.7 standard deviations). It is a statistical estimate from the vendor's OWN history,
never a guarantee, and it says so. With too little history it does NOT invent a forecast."""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models.catalog import ApprovalStatus, MenuItem, Vendor, WasteLog
from app.models.orders import Order, OrderItem, OrderStatus as S
from app.services.analytics_service import COUNTED

LOOKBACK_WEEKS = 8
DECAY = 0.85
SLOTS = 48                       # 30-minute slots in a day
MIN_DAYS, MIN_ORDERS = 14, 20    # below this we refuse to forecast
Z = 0.7                          # half-width of the displayed range in standard deviations (about the middle 50%)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ------------------------------------------------------------------ small helpers
def slot_of(dt: datetime) -> int:
    return dt.hour * 2 + (1 if dt.minute >= 30 else 0)


def slot_label(slot: int) -> str:
    h, m = divmod((slot % SLOTS) * 30, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def window_label(start: int, end: int) -> str:
    return f"{slot_label(start)} - {slot_label(end)}"        # end is exclusive


@dataclass
class History:
    """Everything the model needs, as plain numbers."""
    today: date
    first_date: date | None = None
    orders: int = 0
    by_date: dict[date, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0] * SLOTS))
    dish_by_date: dict[str, dict[date, list[float]]] = field(default_factory=dict)

    @property
    def days(self) -> int:
        return 0 if self.first_date is None else (self.today - self.first_date).days + 1


def confidence(h: History) -> dict:
    if h.days < MIN_DAYS or h.orders < MIN_ORDERS:
        level, msg = "insufficient", (f"Not enough history for a reliable forecast yet ({h.orders} orders over {h.days} day{'s' if h.days != 1 else ''}). "
                                       f"KORA EATS needs about {MIN_DAYS} days and {MIN_ORDERS} orders; keep taking orders and this will appear automatically.")
    elif h.orders < 60 or h.days < 28:
        level, msg = "low", "Early estimate: based on a few weeks of orders. It will get more accurate as you take more orders."
    elif h.orders < 200 or h.days < 56:
        level, msg = "medium", "Reasonable estimate based on several weeks of your orders."
    else:
        level, msg = "high", "Solid estimate based on two months of your orders."
    return {"level": level, "message": msg, "history_days": h.days, "orders_analysed": h.orders}


def sample_dates(h: History, target: date) -> list[date]:
    """Same weekday in past complete days, most recent first, only from when this vendor had started taking orders."""
    out = []
    for k in range(1, LOOKBACK_WEEKS + 1):
        d = target - timedelta(days=7 * k)
        if d < h.today and h.first_date is not None and d >= h.first_date:
            out.append(d)
    return out


def _weights(n: int) -> list[float]:
    return [DECAY ** k for k in range(n)]


def trend_factor(h: History) -> float:
    """Gentle correction when the last two weeks are busier/quieter than the two before (damped by a square root)."""
    def total(a: int, b: int) -> int:
        return sum(sum(h.by_date[h.today - timedelta(days=i)]) for i in range(a, b) if (h.today - timedelta(days=i)) in h.by_date)
    recent, previous = total(1, 15), total(15, 29)
    if previous < 10:
        return 1.0
    return math.sqrt(min(1.4, max(0.7, recent / previous)))


def slot_means(h: History, target: date) -> tuple[list[float], list[float], int]:
    """-> (expected orders per 30-minute slot, daily totals of the samples used, number of samples). Falls back to ALL recent days
    when the weekday has fewer than 2 samples (the weekly pattern is still being learned)."""
    dates = sample_dates(h, target)
    if len(dates) < 2:
        dates = [h.today - timedelta(days=i) for i in range(1, 29) if h.first_date and (h.today - timedelta(days=i)) >= h.first_date]
    if not dates:
        return [0.0] * SLOTS, [], 0
    w = _weights(len(dates))
    tw = sum(w)
    means = [sum(w[k] * h.by_date[d][s] for k, d in enumerate(dates)) / tw for s in range(SLOTS)]
    return means, [float(sum(h.by_date[d])) for d in dates], len(dates)


def interval(mean: float, totals: list[float]) -> tuple[float, int, int]:
    """(sd, low, high) around the mean for a day total. With under 3 samples we assume Poisson-like noise."""
    if len(totals) >= 3:
        w = _weights(len(totals))
        m = sum(wi * t for wi, t in zip(w, totals)) / sum(w)
        sd = math.sqrt(sum(wi * (t - m) ** 2 for wi, t in zip(w, totals)) / sum(w))
    else:
        sd = math.sqrt(max(mean, 1.0))
    hw = max(1, round(Z * sd))
    return sd, max(0, round(mean - hw)), (max(0, round(mean + hw)) if mean > 0 else 0)


def peak_windows(means: list[float], top: int = 2) -> list[tuple[int, int, float]]:
    """Busy stretches: runs of 30-minute slots whose (smoothed) demand is at least 60% of the busiest slot."""
    if sum(means) < 3:
        return []
    sm = [(means[max(0, i - 1)] + 2 * means[i] + means[min(SLOTS - 1, i + 1)]) / 4 for i in range(SLOTS)]
    if max(sm) < 1.5 * (sum(means) / SLOTS):          # demand is spread evenly: there is no real peak to report
        return []
    thr = 0.6 * max(sm)
    runs, start = [], None
    for i in range(SLOTS + 1):
        on = i < SLOTS and sm[i] >= thr
        if on and start is None:
            start = i
        if not on and start is not None:
            runs.append((start, i, sum(means[start:i])))
            start = None
    return sorted(runs, key=lambda r: -r[2])[:top]


def forecast_day(h: History, target: date, now: datetime | None = None) -> dict | None:
    """Expected orders for one day (None when there isn't enough history)."""
    if confidence(h)["level"] == "insufficient":
        return None
    means, totals, n = slot_means(h, target)
    tf = trend_factor(h)
    means = [m * tf for m in means]
    mean = sum(means)
    sd, low, high = interval(mean, [t * tf for t in totals])
    out = {"date": target.isoformat(), "weekday": WEEKDAYS[target.weekday()], "mean": round(mean, 1), "low": low, "high": high, "samples": n, "slots": means}
    if now is not None and target == h.today:
        so_far = sum(h.by_date[h.today]) if h.today in h.by_date else 0
        cs = slot_of(now)
        frac = 1 - (now.minute % 30) / 30
        remaining = sum(means[cs + 1:]) + means[cs] * frac
        hw = max(1, round(Z * sd * math.sqrt(remaining / mean))) if mean > 0 else 1
        out["so_far"] = so_far
        out["remaining"] = {"mean": round(remaining, 1), "low": max(0, round(remaining - hw)), "high": round(remaining + hw)}
        out["expected_total"] = {"mean": round(so_far + remaining, 1), "low": so_far + out["remaining"]["low"], "high": so_far + out["remaining"]["high"]}
    return out


def dish_forecasts(h: History, target: date, tf: float, top: int = 5) -> list[dict]:
    dates = sample_dates(h, target) or [h.today - timedelta(days=i) for i in range(1, 29) if h.first_date and (h.today - timedelta(days=i)) >= h.first_date]
    if not dates:
        return []
    w = _weights(len(dates))
    tw = sum(w)
    rows = []
    for name, per_day in h.dish_by_date.items():
        slots = [sum(w[k] * (per_day[d][s] if d in per_day else 0.0) for k, d in enumerate(dates)) / tw * tf for s in range(SLOTS)]
        qty = sum(slots)
        if qty >= 0.5:
            rows.append((name, qty, slots))
    total = sum(q for _, q, _ in rows) or 1.0
    out = []
    for name, qty, slots in sorted(rows, key=lambda r: -r[1])[:top]:
        hw = max(1, round(Z * math.sqrt(qty)))
        peak = peak_windows(slots, top=1)
        out.append({"name": name, "expected": {"mean": round(qty, 1), "low": max(0, round(qty - hw)), "high": round(qty + hw)}, "share_percent": round(100 * qty / total),
                    "peak": ({"label": window_label(peak[0][0], peak[0][1]), "start": slot_label(peak[0][0]), "end": slot_label(peak[0][1]),
                              "portions": round(peak[0][2])} if peak else None)})
    return out


# ------------------------------------------------------------------ loading
def load_history(db: Session, vendor_id: int, now: datetime) -> History:
    today = now.date()
    since = now - timedelta(days=LOOKBACK_WEEKS * 7 + 7)
    h = History(today=today)
    rows = db.execute(select(Order.id, Order.paid_at).where(Order.vendor_id == vendor_id, Order.paid_at.is_not(None), Order.paid_at >= since,
                                                            Order.status.in_(COUNTED))).all()
    when = {}
    for oid, paid in rows:
        d = paid.date()
        when[oid] = (d, slot_of(paid))
        h.by_date[d][slot_of(paid)] += 1
        h.orders += 1
        h.first_date = d if h.first_date is None or d < h.first_date else h.first_date
    if when:
        for oid, name, qty in db.execute(select(OrderItem.order_id, OrderItem.name, OrderItem.quantity).where(OrderItem.order_id.in_(list(when)))):
            d, s = when[oid]
            h.dish_by_date.setdefault(name, defaultdict(lambda: [0.0] * SLOTS))[d][s] += qty
    return h


def peer_benchmark(db: Session, vendor: Vendor, now: datetime) -> dict | None:
    """When a vendor is new: how busy are similar vendors? (A benchmark, NOT a forecast for this vendor.)"""
    since = now - timedelta(days=28)
    rows = db.execute(select(Order.vendor_id, func.count()).join(Vendor, Vendor.id == Order.vendor_id).where(
        Vendor.business_type == vendor.business_type, Vendor.id != vendor.id, Vendor.approval_status == ApprovalStatus.APPROVED,
        Order.paid_at >= since, Order.status.in_(COUNTED)).group_by(Order.vendor_id)).all()
    counts = [c for _, c in rows if c >= 10]
    if len(counts) < 3:
        return None
    return {"similar_vendors": len(counts), "avg_orders_per_day": round(sum(counts) / len(counts) / 28, 1)}


# ------------------------------------------------------------------ the full report
def report(db: Session, vendor: Vendor, target: date | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    target = target or now.date()
    h = load_history(db, vendor.id, now)
    conf = confidence(h)
    out = {"date": target.isoformat(), "weekday": WEEKDAYS[target.weekday()], "is_today": target == now.date(), "confidence": conf,
           "recommendations": [], "peak_periods": [], "popular_meals": [], "week_ahead": [], "hourly": None, "orders": None, "waste": [],
           "basis": "Recency-weighted average of the same weekday over the last 8 weeks, adjusted for the recent trend. A statistical estimate, not a guarantee."}
    booked = db.scalar(select(func.count()).select_from(Order).where(Order.vendor_id == vendor.id, Order.status.in_([S.PAID, S.CONFIRMED, S.PREPARING]),
                                                                    func.date(func.timezone("UTC", Order.scheduled_for)) == target))
    out["booked_scheduled_orders"] = booked or 0
    rec = out["recommendations"]
    fc = forecast_day(h, target, now if out["is_today"] else None)
    if fc is None:
        out["benchmark"] = peer_benchmark(db, vendor, now)
        out["orders"] = {"so_far": sum(h.by_date[h.today]) if h.today in h.by_date else 0}
        rec.append({"kind": "data", "text": conf["message"]})
        if out["benchmark"]:
            b = out["benchmark"]
            rec.append({"kind": "data", "text": f"For reference, {b['similar_vendors']} similar vendors on KORA EATS average about {b['avg_orders_per_day']} orders a day. This is a benchmark, not a forecast for you."})
    else:
        tf = trend_factor(h)
        means = fc.pop("slots")
        total = fc["expected_total"] if "expected_total" in fc else {"mean": fc["mean"], "low": fc["low"], "high": fc["high"]}
        out["orders"] = {**({"so_far": fc["so_far"], "remaining": fc["remaining"]} if "so_far" in fc else {}), "expected_total": total}
        day = "today" if out["is_today"] else f"on {out['weekday']}"
        rec.append({"kind": "orders", "text": f"Expected orders {day}: {total['low']}-{total['high']}"})
        out["hourly"] = {"labels": [slot_label(s) for s in range(SLOTS)], "values": [round(m, 2) for m in means]}
        for a, b, n in peak_windows(means):
            out["peak_periods"].append({"start": slot_label(a), "end": slot_label(b), "label": window_label(a, b), "expected_orders": round(n)})
        if out["peak_periods"]:
            rec.append({"kind": "peak", "text": "Peak period: " + out["peak_periods"][0]["label"] + (f" (then {out['peak_periods'][1]['label']})" if len(out["peak_periods"]) > 1 else "")})
        out["popular_meals"] = dish_forecasts(h, target, tf)
        for m in out["popular_meals"][:3]:
            if m["peak"] and m["peak"]["portions"] >= 3:
                rec.append({"kind": "prep", "text": f"Recommended: increase preparation capacity for {m['name']} between {m['peak']['start']} and {m['peak']['end']} (about {m['peak']['portions']} portions)."})
        for i in range(7):
            d = target + timedelta(days=i)
            wf = forecast_day(h, d)
            if wf:
                out["week_ahead"].append({"date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "mean": wf["mean"], "low": wf["low"], "high": wf["high"]})
        out["waste"] = waste_advice(db, vendor, h, target, tf, out["popular_meals"])
        rec.extend({"kind": "waste", "text": w["message"]} for w in out["waste"][:3])
    if out["booked_scheduled_orders"]:
        rec.append({"kind": "booked", "text": f"{out['booked_scheduled_orders']} scheduled order(s) already booked for {target.strftime('%d %b')}."})
    return out


def waste_advice(db: Session, vendor: Vendor, h: History, target: date, tf: float, popular: list[dict]) -> list[dict]:
    """Waste-reduction suggestions from planned portions, logged leftovers and slow sellers. Only says what the numbers support."""
    advice = []
    items = db.scalars(select(MenuItem).where(MenuItem.vendor_id == vendor.id)).unique().all()
    expected = {m["name"]: m["expected"] for m in dish_forecasts(h, target, tf, top=200)}
    for it in items:
        e = expected.get(it.name)
        if it.daily_prep_qty and e:
            if it.daily_prep_qty > e["high"] * 1.25 + 2:
                suggest = math.ceil(e["high"] * 1.1)
                advice.append({"item": it.name, "type": "overprep", "suggested_portions": suggest,
                               "message": f"You plan {it.daily_prep_qty} portions of {it.name} but usually sell about {e['low']}-{e['high']} on a {WEEKDAYS[target.weekday()]}. Consider preparing about {suggest} to reduce waste."})
            elif it.daily_prep_qty < e["low"] * 0.8:
                advice.append({"item": it.name, "type": "underprep", "suggested_portions": e["high"],
                               "message": f"You plan {it.daily_prep_qty} portions of {it.name} but expect {e['low']}-{e['high']}: it may sell out early."})
    since = h.today - timedelta(days=28)
    logs = db.execute(select(WasteLog.menu_item_id, func.sum(WasteLog.prepared_qty), func.sum(WasteLog.leftover_qty), func.count()).where(
        WasteLog.vendor_id == vendor.id, WasteLog.log_date >= since).group_by(WasteLog.menu_item_id)).all()
    names = {i.id: i.name for i in items}
    for item_id, prepared, leftover, n in logs:
        if n >= 2 and prepared and item_id in names and leftover / prepared >= 0.25:
            sold_avg = (prepared - leftover) / n
            advice.append({"item": names[item_id], "type": "leftovers", "suggested_portions": math.ceil(sold_avg * 1.1),
                           "message": f"About {round(100 * leftover / prepared)}% of {names[item_id]} was left over on the {n} days you logged. Try preparing about {math.ceil(sold_avg * 1.1)} per day."})
    if h.days >= 28:
        sold_28 = {}
        for name, per_day in h.dish_by_date.items():
            sold_28[name] = sum(sum(v) for d, v in per_day.items() if d >= since)
        for it in items:
            if it.is_available and sold_28.get(it.name, 0) <= 2:
                advice.append({"item": it.name, "type": "slow", "message": f"{it.name} sold only {int(sold_28.get(it.name, 0))} in the last 4 weeks. Consider making it to order, bundling it, or removing it."})
    return advice
