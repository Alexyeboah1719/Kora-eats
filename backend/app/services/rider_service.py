"""Rider side of the order flow: who may take a delivery and what each step does."""
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import api_error
from app.core.geo import haversine_km
from app.core.timeutil import utcnow
from app.models.orders import FulfillmentType, Order, OrderStatus as S
from app.models.riders import Delivery, DeliveryRider, RiderDecline
from app.services import order_events, order_service
from app.services.pricing_service import money

MAX_ACTIVE = 2
ACTIVE_STATUSES = [S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY, S.RIDER_ARRIVED, S.RETURNED, S.ACCEPTED_RETURN]
MAX_WAIT_MINUTES = 5          # the longest a rider may wait at the door for the customer


def earning_for(order: Order) -> Decimal:
    return money(Decimal(order.delivery_fee) * get_settings().rider_share_percent / 100)


def _active_count(db: Session, rider: DeliveryRider) -> int:
    return db.scalar(select(func.count()).select_from(Delivery).join(Order, Order.id == Delivery.order_id)
                     .where(Delivery.rider_id == rider.id, Order.status.in_(ACTIVE_STATUSES)))


def rider_view(db: Session, o: Order, d: Delivery | None = None) -> dict:
    """What a rider needs to see: where to pick up, where to deliver, what they earn."""
    return {
        "order_id": o.id, "code": o.code, "status": o.status.value, "total": float(o.total),
        "earning": float(d.rider_earning if d and d.rider_earning else earning_for(o)),
        "item_count": sum(i.quantity for i in o.items),
        "vendor": {"name": o.vendor.name, "phone": o.vendor.phone, "address": o.vendor.address_text,
                   "lat": o.vendor.latitude, "lng": o.vendor.longitude},
        "customer": {"phone": o.customer_phone, "address": o.delivery_address, "notes": o.delivery_notes,
                     "lat": o.delivery_lat, "lng": o.delivery_lng},
        "trip_km": round(haversine_km(o.vendor.latitude, o.vendor.longitude, o.delivery_lat, o.delivery_lng), 2),
        "arrived_at": d.arrived_at.isoformat() if d and d.arrived_at else None,
        "delivered_at": d.delivered_at.isoformat() if d and d.delivered_at else None,
        "returned_at": d.returned_at.isoformat() if d and d.returned_at else None,
        "wait_minutes": d.wait_minutes if d else 0,
        "wait_until": d.wait_until.isoformat() if d and d.wait_until else None,
    }


def available(db: Session, rider: DeliveryRider) -> list[dict]:
    """Ready orders, smartest first: urgent ones, then the ones best suited to THIS rider (see dispatch_service)."""
    from app.services import dispatch_service as dsp          # imported here: dispatch_service itself uses this module
    now = utcnow()
    mine = dsp._active_by_rider(db, [rider.id])[rider.id]
    out = []
    for o in dsp.waiting_orders(db):
        v = rider_view(db, o)
        v["pickup_km"] = (round(haversine_km(rider.current_lat, rider.current_lng, o.vendor.latitude, o.vendor.longitude), 2)
                          if rider.current_lat is not None else None)
        plan = dsp.evaluate(rider, o, mine, now)
        v["priority"] = dsp.priority(o, now)
        v["recommended"] = any(p.rider.id == rider.id for p in dsp.recommended(db, o, now=now))
        v["smart"] = None if plan is None else {"score": round(plan.score * 100), "pickup_eta_minutes": plan.pickup_eta, "delivery_eta_minutes": plan.delivery_eta,
                                                "batching": plan.batching, "reasons": plan.reasons, "stale_location": plan.stale}
        out.append(v)
    out.sort(key=lambda v: (not v["priority"]["urgent"], not v["recommended"], -(v["smart"]["score"] if v["smart"] else -1), v["pickup_km"] is None, v["pickup_km"] or 0))
    return out


def _my_delivery(db: Session, rider: DeliveryRider, order_id: int) -> tuple[Order, Delivery]:
    o = order_service.lock_order(db, order_id)
    d = db.scalar(select(Delivery).where(Delivery.order_id == order_id, Delivery.rider_id == rider.id)) if o else None
    if o is None or d is None:
        raise api_error(404, "not_found", "Delivery not found.")
    return o, d


def accept(db: Session, rider: DeliveryRider, order_id: int, background: BackgroundTasks) -> dict:
    if not rider.is_online:
        raise api_error(409, "offline", "Go online to accept deliveries.")
    if _active_count(db, rider) >= MAX_ACTIVE:
        raise api_error(409, "too_many_active", f"Finish your current deliveries first (max {MAX_ACTIVE} at a time).")
    o = order_service.lock_order(db, order_id)       # row lock: two riders can't take the same order
    if o is None:
        raise api_error(404, "not_found", "Delivery not found.")
    if o.fulfillment_type == FulfillmentType.PICKUP:
        raise api_error(409, "pickup_order", "This is a pickup order. The customer collects it from the vendor.")
    if o.status != S.READY_FOR_PICKUP or db.scalar(select(Delivery.id).where(Delivery.order_id == o.id)):
        raise api_error(409, "already_taken", "Another rider already took this delivery.")
    d = Delivery(order_id=o.id, rider_id=rider.id, rider_earning=earning_for(o))
    db.add(d)
    order_service.change_status(db, o, S.RIDER_ASSIGNED, "rider", rider.user_id, f"Accepted by {rider.user.profile.full_name}", background)
    db.commit()
    return rider_view(db, o, d)


def _step(db, rider, order_id, new: S, stamp: str, background) -> dict:
    o, d = _my_delivery(db, rider, order_id)
    order_service.change_status(db, o, new, "rider", rider.user_id, None, background)
    setattr(d, stamp, utcnow())
    db.commit()
    return rider_view(db, o, d)


def pickup(db, rider, order_id, background):
    return _step(db, rider, order_id, S.PICKED_UP, "picked_up_at", background)


def out_for_delivery(db, rider, order_id, background):
    return _step(db, rider, order_id, S.OUT_FOR_DELIVERY, "out_for_delivery_at", background)


def deliver(db, rider, order_id, background):
    o, d = _my_delivery(db, rider, order_id)
    if d.arrived_at is None:
        d.arrived_at = utcnow()
    order_service.change_status(db, o, S.DELIVERED, "rider", rider.user_id, None, background)
    d.delivered_at = utcnow()
    if o.payment_mode == "COD":
        o.cod_collected_at = utcnow()          # the rider confirms they collected the cash
    db.commit()
    return rider_view(db, o, d)


REPORT_OUTCOMES = {
    "COMPLETED_PAID": "Delivered — customer paid (cash on delivery)",
    "ARRIVED_NOT_PAID": "Arrived — customer did not pay (cash on delivery)",
    "CUSTOMER_NO_SHOW": "Customer was not there / did not answer",
    "WRONG_ADDRESS": "Address was wrong or unreachable",
    "OTHER": "Other problem",
}


def report_issue(db: Session, rider: DeliveryRider, order_id: int, outcome: str, message: str) -> dict:
    """Rider reports a delivery problem. Files a support ticket (admin sees it) and, for COD,
    marks the order's cash as refused so the customer's COD privileges can be limited."""
    from app.models.admin import SupportTicket
    from app.models.ai import CustomerPreference
    o, d = _my_delivery(db, rider, order_id)
    if o.status in (S.DELIVERED, S.COLLECTED, S.CANCELLED, S.REFUNDED):
        raise api_error(409, "already_done", "This delivery is already finished.")
    label = REPORT_OUTCOMES.get(outcome, outcome)
    t = SupportTicket(user_id=rider.user_id, order_id=o.id,
                      subject=f"[Rider report] {o.code}: {label}"[:120],
                      message=f"Rider {rider.user.profile.full_name} reported: {label}.\n\nDetails: {message}")
    db.add(t)
    if outcome in ("ARRIVED_NOT_PAID", "CUSTOMER_NO_SHOW") and o.payment_mode == "COD":
        o.cod_failed_at = o.cod_failed_at or utcnow()
        pref = db.get(CustomerPreference, o.customer_id)
        if pref is not None:
            pref.cod_failures = (pref.cod_failures or 0) + 1
            s = get_settings()
            if pref.cod_failures >= s.cod_max_failures and (pref.cod_blocked_until is None or pref.cod_blocked_until <= utcnow()):
                pref.cod_blocked_until = utcnow() + timedelta(days=s.cod_block_days)
    db.commit()
    return {"ticket_id": t.id, "message": "Report sent to KORA EATS support. They'll review it and contact the customer."}


def return_to_vendor(db, rider, order_id, reason, note, background):
    """Rider starts a return: order -> RETURNED, the vendor must accept before the rider sets off."""
    from app.services import return_service
    o, d = _my_delivery(db, rider, order_id)
    return_service.start(db, o, reason or "OTHER", note, rider.user_id, "rider", background)
    db.commit()
    return rider_view(db, o, d)


def handed_back(db, rider, order_id, background):
    """The rider physically handed the food to the vendor; the vendor still has to confirm receipt."""
    o, d = _my_delivery(db, rider, order_id)
    if o.status not in (S.ACCEPTED_RETURN, S.RETURNED_TO_VENDOR):
        raise api_error(409, "return_not_ready", "The vendor hasn't accepted this return yet.")
    if d.returned_at is None:
        d.returned_at = utcnow()
        if o.status == S.ACCEPTED_RETURN:
            vendor_owner = getattr(o.vendor, "owner_user_id", None)
            if vendor_owner:
                from app.services import notification_service as ns
                ns.notify(db, vendor_owner, "return_handed_back", "Food is back",
                          f"Rider {rider.user.profile.full_name} says order {o.code} is back with you. Confirm receipt to close the return.",
                          "/vendor/dashboard.html#orders", background=background)
    db.commit()
    return rider_view(db, o, d)


def arrived(db, rider, order_id, wait_minutes, background) -> dict:
    """Rider reaches the door. They may optionally wait up to MAX_WAIT_MINUTES for the customer;
    the same endpoint also lets them set/adjust the wait while already RIDER_ARRIVED."""
    o, d = _my_delivery(db, rider, order_id)
    if wait_minutes is not None and not (0 <= wait_minutes <= MAX_WAIT_MINUTES):
        raise api_error(422, "bad_wait", f"Wait time must be between 0 and {MAX_WAIT_MINUTES} minutes.")
    if o.status == S.OUT_FOR_DELIVERY:
        if d.arrived_at is None:
            d.arrived_at = utcnow()
        order_service.change_status(db, o, S.RIDER_ARRIVED, "rider", rider.user_id, "Rider arrived at the delivery address", background)
    elif not (o.status == S.RIDER_ARRIVED and d.arrived_at is not None):
        raise api_error(409, "not_out_for_delivery", "Start the delivery before marking arrival.")
    if wait_minutes is not None:
        old_until = d.wait_until
        d.wait_minutes = wait_minutes
        d.wait_until = (d.arrived_at + timedelta(minutes=wait_minutes)) if wait_minutes else None
        if wait_minutes and d.wait_until != old_until:
            from app.services import notification_service as ns
            ns.notify(db, o.customer_id, "rider_waiting", "Your rider is waiting",
                      f"Your rider for order {o.code} has arrived and will wait for you until {d.wait_until.strftime('%H:%M')} "
                      f"({wait_minutes} minute(s)). Please come to the door.",
                      f"/order.html?id={o.id}", background=background)
    db.commit()
    return rider_view(db, o, d)


def release(db, rider, order_id, background):
    """Rider changes their mind before pickup: the order goes back to the pool and auto-dispatch
    skips this rider for it, so the NEXT best rider gets the offer."""
    o, d = _my_delivery(db, rider, order_id)
    order_service.change_status(db, o, S.READY_FOR_PICKUP, "rider", rider.user_id, "Rider released the delivery", background)
    db.add(RiderDecline(order_id=o.id, rider_id=rider.id))
    db.delete(d)
    db.commit()


def my_deliveries(db: Session, rider: DeliveryRider, view: str) -> list[dict]:
    q = (select(Order, Delivery).join(Delivery, Delivery.order_id == Order.id).where(Delivery.rider_id == rider.id))
    q = q.where(Order.status.in_(ACTIVE_STATUSES)) if view == "active" else q.where(Order.status.not_in(ACTIVE_STATUSES))
    return [rider_view(db, o, d) for o, d in db.execute(q.order_by(Delivery.id.desc()).limit(100)).unique()]


def earnings(db: Session, rider: DeliveryRider) -> dict:
    now = utcnow()
    # Delivered orders AND returns that were settled (the platform paid the rider's share anyway).
    from sqlalchemy import or_
    done = (select(Delivery).join(Order, Order.id == Delivery.order_id)
            .where(Delivery.rider_id == rider.id,
                   or_(Delivery.delivered_at.is_not(None), Delivery.returned_at.is_not(None),
                       Order.status.in_([S.RETURNED_TO_VENDOR, S.RETURN_DECLINED]))))
    rows = db.scalars(done.order_by(Delivery.id.desc())).unique().all()

    def stamp(d: Delivery):
        return d.delivered_at or d.returned_at

    def total(since: datetime | None) -> float:
        return float(sum((d.rider_earning for d in rows if since is None or (stamp(d) and stamp(d) >= since)), Decimal("0")))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = midnight - timedelta(days=midnight.weekday())
    return {"today": total(midnight), "this_week": total(week_start), "all_time": total(None), "deliveries": len(rows),
            "recent": [{"order_id": d.order_id, "earning": float(d.rider_earning), "at": stamp(d).isoformat() if stamp(d) else None} for d in rows[:30]]}


@order_service.register_serializer
def _rider_info(db: Session, order: Order) -> dict:
    d = db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    if d is None:
        return {"rider": None}
    r = d.rider
    return {"rider": {"name": r.user.profile.full_name, "phone": r.phone, "vehicle": r.vehicle_type.value,
                      "plate": r.plate_number, "arrived": d.arrived_at is not None,
                      "wait_minutes": d.wait_minutes or 0, "wait_until": d.wait_until.isoformat() if d.wait_until else None,
                      "returned_at": d.returned_at.isoformat() if d.returned_at else None}}
