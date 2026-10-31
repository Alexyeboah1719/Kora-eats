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
from app.models.riders import Delivery, DeliveryRider
from app.services import order_events, order_service
from app.services.pricing_service import money

MAX_ACTIVE = 2
ACTIVE_STATUSES = [S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY]


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
    if d.arrived_at is None and o.status == S.OUT_FOR_DELIVERY:
        d.arrived_at = utcnow()
    order_service.change_status(db, o, S.DELIVERED, "rider", rider.user_id, None, background)
    d.delivered_at = utcnow()
    db.commit()
    return rider_view(db, o, d)


def arrived(db, rider, order_id, background) -> dict:
    o, d = _my_delivery(db, rider, order_id)
    if o.status != S.OUT_FOR_DELIVERY:
        raise api_error(409, "not_out_for_delivery", "Start the delivery before marking arrival.")
    if d.arrived_at is None:
        d.arrived_at = utcnow()
        order_events.emit(db, o, S.OUT_FOR_DELIVERY, S.OUT_FOR_DELIVERY, "rider:arrived", background)
    db.commit()
    return rider_view(db, o, d)


def release(db, rider, order_id, background):
    """Rider changes their mind before pickup: the order goes back to the available pool."""
    o, d = _my_delivery(db, rider, order_id)
    order_service.change_status(db, o, S.READY_FOR_PICKUP, "rider", rider.user_id, "Rider released the delivery", background)
    db.delete(d)
    db.commit()


def my_deliveries(db: Session, rider: DeliveryRider, view: str) -> list[dict]:
    q = (select(Order, Delivery).join(Delivery, Delivery.order_id == Order.id).where(Delivery.rider_id == rider.id))
    q = q.where(Order.status.in_(ACTIVE_STATUSES)) if view == "active" else q.where(Order.status.not_in(ACTIVE_STATUSES))
    return [rider_view(db, o, d) for o, d in db.execute(q.order_by(Delivery.id.desc()).limit(100)).unique()]


def earnings(db: Session, rider: DeliveryRider) -> dict:
    now = utcnow()
    done = select(Delivery).where(Delivery.rider_id == rider.id, Delivery.delivered_at.is_not(None))
    rows = db.scalars(done.order_by(Delivery.delivered_at.desc())).unique().all()

    def total(since: datetime | None) -> float:
        return float(sum((d.rider_earning for d in rows if since is None or d.delivered_at >= since), Decimal("0")))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = midnight - timedelta(days=midnight.weekday())
    return {"today": total(midnight), "this_week": total(week_start), "all_time": total(None), "deliveries": len(rows),
            "recent": [{"order_id": d.order_id, "earning": float(d.rider_earning), "at": d.delivered_at.isoformat()} for d in rows[:30]]}


@order_service.register_serializer
def _rider_info(db: Session, order: Order) -> dict:
    d = db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    if d is None:
        return {"rider": None}
    r = d.rider
    return {"rider": {"name": r.user.profile.full_name, "phone": r.phone, "vehicle": r.vehicle_type.value,
                      "plate": r.plate_number, "arrived": d.arrived_at is not None}}
