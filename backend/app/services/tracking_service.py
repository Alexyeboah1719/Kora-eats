"""Live delivery tracking: rider pings in, a privacy-aware view out."""
from datetime import timedelta

from fastapi import BackgroundTasks
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.geo import haversine_km
from app.core.timeutil import utcnow
from app.models.orders import Order, OrderStatus as S
from app.models.riders import Delivery, DeliveryRider, DeliveryTracking
from app.services import order_events, quote_service

NEARBY_KM = 0.4
MIN_PING_SECONDS = 4
LIVE = (S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY)


def record_location(db: Session, rider: DeliveryRider, lat: float, lng: float, background: BackgroundTasks | None = None) -> bool:
    """Stores the rider's position. Returns False when ignored for being too frequent."""
    now = utcnow()
    if rider.last_location_at and (now - rider.last_location_at).total_seconds() < MIN_PING_SECONDS:
        return False
    rider.current_lat, rider.current_lng, rider.last_location_at = lat, lng, now
    row = db.execute(select(Delivery, Order).join(Order, Order.id == Delivery.order_id)
                     .where(Delivery.rider_id == rider.id, Order.status.in_(LIVE)).order_by(Delivery.id.desc())).first()
    if row:
        d, o = row
        db.add(DeliveryTracking(delivery_id=d.id, latitude=lat, longitude=lng))
        if (o.status == S.OUT_FOR_DELIVERY and d.nearby_notified_at is None
                and haversine_km(lat, lng, o.delivery_lat, o.delivery_lng) <= NEARBY_KM):
            d.nearby_notified_at = now
            order_events.emit(db, o, o.status, o.status, "rider:nearby", background)   # notifications subscribe to this
    db.commit()
    return True


def _eta_minutes(km: float) -> int:
    return quote_service.travel_minutes(km)      # same speed assumption as checkout; ETAs are estimates


def tracking_view(db: Session, o: Order) -> dict:
    out = {"status": o.status.value, "live": o.status in LIVE, "rider": None, "eta_minutes": None, "trail": [],
           "vendor": {"name": o.vendor.name, "lat": o.vendor.latitude, "lng": o.vendor.longitude},
           "customer": {"lat": o.delivery_lat, "lng": o.delivery_lng, "address": o.delivery_address}, "distance_km": None}
    if o.status not in LIVE:
        return out
    d = db.scalar(select(Delivery).where(Delivery.order_id == o.id))
    if d is None:
        return out
    r = d.rider
    if r.current_lat is not None:
        age = (utcnow() - r.last_location_at).total_seconds() if r.last_location_at else None
        out["rider"] = {"name": r.user.profile.full_name, "phone": r.phone, "vehicle": r.vehicle_type.value,
                        "lat": r.current_lat, "lng": r.current_lng,
                        "updated_at": r.last_location_at.isoformat() if r.last_location_at else None,
                        "stale": age is None or age > 120}
        to_customer = haversine_km(r.current_lat, r.current_lng, o.delivery_lat, o.delivery_lng)
        out["distance_km"] = round(to_customer, 2)
        if o.status == S.OUT_FOR_DELIVERY:
            out["eta_minutes"] = _eta_minutes(to_customer)
        else:  # still heading to the vendor: rider -> vendor -> customer
            leg1 = haversine_km(r.current_lat, r.current_lng, o.vendor.latitude, o.vendor.longitude)
            leg2 = haversine_km(o.vendor.latitude, o.vendor.longitude, o.delivery_lat, o.delivery_lng)
            out["eta_minutes"] = _eta_minutes(leg1 + leg2)
    trail = db.scalars(select(DeliveryTracking).where(DeliveryTracking.delivery_id == d.id)
                       .order_by(DeliveryTracking.id.desc()).limit(40)).all()
    out["trail"] = [[t.latitude, t.longitude] for t in reversed(trail)]
    out["arrived"] = d.arrived_at is not None
    return out


def prune_old_tracking(db: Session, keep_hours: int = 48) -> int:
    res = db.execute(delete(DeliveryTracking).where(DeliveryTracking.recorded_at < utcnow() - timedelta(hours=keep_hours)))
    db.commit()
    return res.rowcount or 0
