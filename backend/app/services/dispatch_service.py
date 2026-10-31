"""Smart Rider Dispatch: which rider is best for an order, and which order suits a rider.

For every eligible rider (approved, online, a recent GPS position, spare capacity) we estimate when THIS order would reach the customer:
ride to the vendor, then to the customer; a rider already carrying orders first finishes them. If the new order's vendor is close to the
rider's current pickup and the drop-off is in a similar direction, the order can be BATCHED (a small detour instead of a whole new trip).
All times are estimates from straight-line distances and an average speed, and are labelled as estimates."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.geo import angle_diff, bearing_deg, haversine_km
from app.core.timeutil import utcnow
from app.models.catalog import ApprovalStatus
from app.models.orders import FulfillmentType, Order, OrderStatus as S
from app.models.riders import Delivery, DeliveryRider
from app.services import quote_service
from app.services.rider_service import ACTIVE_STATUSES, MAX_ACTIVE

STALE_MINUTES = 10          # a GPS position older than this is flagged (and counts against the rider)
URGENT_WAIT_MINUTES = 10    # food that has waited this long for a rider is urgent
BATCH_PICKUP_KM = 1.0       # vendors this close can share a trip...
BATCH_DROP_KM = 3.0         # ...if the customers are this close...
BATCH_ANGLE = 60.0          # ...and the two journeys head within 60 degrees of each other
HANDOVER_MINUTES = 2


@dataclass
class Plan:
    rider: DeliveryRider
    score: float
    pickup_km: float
    pickup_eta: int
    delivery_eta: int
    trip_km: float
    active: int
    batching: dict | None = None
    stale: bool = False
    reasons: list[str] = field(default_factory=list)


def _mins(km: float) -> int:
    return quote_service.travel_minutes(km) if km > 0 else 0


def similar_direction(v1: tuple, c1: tuple, v2: tuple, c2: tuple) -> bool:
    """Can two orders (vendor -> customer) share a ride? Vendors close, customers close, journeys pointing the same way."""
    if haversine_km(*v1, *v2) > BATCH_PICKUP_KM or haversine_km(*c1, *c2) > BATCH_DROP_KM:
        return False
    return angle_diff(bearing_deg(*v1, *c1), bearing_deg(*v2, *c2)) <= BATCH_ANGLE


def _pts(o: Order) -> tuple[tuple, tuple]:
    return (o.vendor.latitude, o.vendor.longitude), (o.delivery_lat, o.delivery_lng)


def _remaining(rider: DeliveryRider, o: Order) -> tuple[int, tuple]:
    """Minutes until the rider has finished this order, and where they'll be then (its customer)."""
    here = (rider.current_lat, rider.current_lng)
    v, c = _pts(o)
    km = haversine_km(*c, *v) + haversine_km(*here, *v) if o.status == S.RIDER_ASSIGNED else haversine_km(*here, *c)
    return _mins(km) + HANDOVER_MINUTES, c


def waiting_minutes(o: Order, now: datetime) -> int:
    """How long the food has been ready for a rider."""
    ready = next((h.created_at for h in reversed(o.history) if h.to_status == S.READY_FOR_PICKUP), None)
    return max(0, int((now - ready).total_seconds() // 60)) if ready else 0


def priority(o: Order, now: datetime) -> dict:
    wait = waiting_minutes(o, now)
    due_soon = o.scheduled_for is not None and o.scheduled_for - now <= timedelta(minutes=30)
    urgent = wait >= URGENT_WAIT_MINUTES or due_soon
    return {"waiting_minutes": wait, "urgent": urgent, "level": "urgent" if urgent else "normal",
            "reason": (f"Ready {wait} min ago" if wait >= URGENT_WAIT_MINUTES else "Scheduled delivery is due soon" if due_soon else None)}


def evaluate(rider: DeliveryRider, order: Order, actives: list[Order], now: datetime) -> Plan | None:
    """None = this rider can't take the order (no position, or already at capacity)."""
    if rider.current_lat is None or rider.last_location_at is None or len(actives) >= MAX_ACTIVE:
        return None
    here, (v, c) = (rider.current_lat, rider.current_lng), _pts(order)
    pickup_km, trip_km = haversine_km(*here, *v), haversine_km(*v, *c)
    stale = (now - rider.last_location_at).total_seconds() / 60 > STALE_MINUTES
    reasons, batching = [], None
    if not actives:
        pickup_eta = _mins(pickup_km)
        delivery_eta = pickup_eta + _mins(trip_km)
        reasons += [f"{pickup_km:.1f} km from the vendor (about {max(pickup_eta, 1)} min)", "Online with no other deliveries"]
    else:
        free_at, last_point = 0, here
        for a in actives:
            rem, last_point = _remaining(rider, a)
            free_at = max(free_at, rem)
            av, ac = _pts(a)
            if a.status == S.RIDER_ASSIGNED and similar_direction(av, ac, v, c):
                extra = _mins(haversine_km(*av, *v)) + _mins(haversine_km(*ac, *c)) + HANDOVER_MINUTES
                batching = {"with_order": a.code, "detour_minutes": extra, "vendor_gap_km": round(haversine_km(*av, *v), 2)}
        if batching:
            pickup_eta = _mins(haversine_km(*here, *_pts(actives[0])[0])) + _mins(batching["vendor_gap_km"])
            delivery_eta = free_at + batching["detour_minutes"]
            reasons += [f"Already collecting from a vendor {batching['vendor_gap_km']:.1f} km away, going the same direction (about +{batching['detour_minutes']} min detour)"]
        else:
            pickup_eta = free_at + _mins(haversine_km(*last_point, *v))
            delivery_eta = pickup_eta + _mins(trip_km)
            reasons += [f"Busy with {len(actives)} delivery(ies); free in about {free_at} min"]
    if stale:
        reasons.append(f"Location last updated over {STALE_MINUTES} minutes ago")
    score = 1.0 / (1.0 + (delivery_eta / 45.0) ** 2) + (0.15 if batching else 0.0) - 0.08 * len(actives) - (0.1 if stale else 0.0)
    return Plan(rider=rider, score=round(score, 4), pickup_km=round(pickup_km, 2), pickup_eta=pickup_eta, delivery_eta=delivery_eta,
                trip_km=round(trip_km, 2), active=len(actives), batching=batching, stale=stale, reasons=reasons)


def _active_by_rider(db: Session, rider_ids: list[int]) -> dict[int, list[Order]]:
    out: dict[int, list[Order]] = {r: [] for r in rider_ids}
    if not rider_ids:
        return out
    rows = db.execute(select(Delivery.rider_id, Order).join(Order, Order.id == Delivery.order_id)
                      .where(Delivery.rider_id.in_(rider_ids), Order.status.in_(ACTIVE_STATUSES)).order_by(Delivery.id)).unique().all()
    for rid, o in rows:
        out[rid].append(o)
    return out


def eligible_riders(db: Session) -> list[DeliveryRider]:
    return list(db.scalars(select(DeliveryRider).where(DeliveryRider.is_online.is_(True), DeliveryRider.approval_status == ApprovalStatus.APPROVED)).unique())


def recommend(db: Session, order: Order, limit: int = 3, now: datetime | None = None) -> list[Plan]:
    """The best riders for an order, best first. Pickup orders are never dispatched.
    Riders beyond max pickup time are only considered when nearby riders are scarce.
    Riders with excessive unremitted cash (COD debt) are blocked from new orders."""
    if order.fulfillment_type != FulfillmentType.DELIVERY:
        return []
    now = now or utcnow()
    from app.core.config import get_settings
    s = get_settings()
    max_pickup_mins = s.rider_max_pickup_minutes
    allow_far_pickup = s.rider_allow_far_pickup
    cod_block_on_debt = s.cod_rider_block_on_debt
    max_cash_in_hand = s.cod_rider_max_cash_in_hand
    
    riders = eligible_riders(db)
    actives = _active_by_rider(db, [r.id for r in riders])
    plans = [p for r in riders if (p := evaluate(r, order, actives[r.id], now))]
    
    # Filter riders by pickup time constraint
    nearby_riders = [p for p in plans if p.pickup_eta <= max_pickup_mins]
    
    # Filter riders by COD cash limit if enabled
    if cod_block_on_debt and order.payment_mode == "COD":
        # Check rider cash balance from wallet
        from app.services.wallet_service import balances
        nearby_riders = []
        for p in plans:
            try:
                rider_balance = balances(db, p.rider.id)
                cash_in_hand = -rider_balance.get("cash_in_hand", 0)  # Negative means they owe money
                if cash_in_hand <= max_cash_in_hand:
                    nearby_riders.append(p)
            except:
                # If we can't get balance, allow the rider (fail open)
                nearby_riders.append(p)
    
    # If we have enough nearby riders, use only them
    if len(nearby_riders) >= limit:
        return sorted(nearby_riders, key=lambda p: -p.score)[:limit]
    
    # If nearby riders are scarce, allow farther riders (only if cash balance allows)
    if allow_far_pickup or len(nearby_riders) == 0:
        if cod_block_on_debt and order.payment_mode == "COD":
            # Also check cash limit for farther riders
            allowed_far_riders = []
            for p in plans:
                try:
                    rider_balance = balances(db, p.rider.id)
                    cash_in_hand = -rider_balance.get("cash_in_hand", 0)
                    if cash_in_hand <= max_cash_in_hand:
                        allowed_far_riders.append(p)
                except:
                    allowed_far_riders.append(p)
            return sorted(allowed_far_riders, key=lambda p: -p.score)[:limit]
        else:
            return sorted(plans, key=lambda p: -p.score)[:limit]
    
    # Otherwise, use nearby riders even if fewer than limit
    return sorted(nearby_riders, key=lambda p: -p.score)[:limit]


def recommended(db: Session, order: Order, now: datetime | None = None) -> list[Plan]:
    """The riders worth telling 'this suits you': at most 3, and only those nearly as good as the best (score within 80% of it).
    A rider 10 km away is not 'recommended' just because few riders are online."""
    plans = recommend(db, order, limit=3, now=now)
    return [p for p in plans if p.score >= 0.8 * plans[0].score] if plans else []


def plan_out(p: Plan) -> dict:
    return {"rider_id": p.rider.id, "name": p.rider.user.profile.full_name, "vehicle": p.rider.vehicle_type.value, "phone": p.rider.phone,
            "score": round(p.score * 100), "pickup_km": p.pickup_km, "pickup_eta_minutes": p.pickup_eta, "delivery_eta_minutes": p.delivery_eta,
            "active_deliveries": p.active, "batching": p.batching, "stale_location": p.stale, "reasons": p.reasons}


def waiting_orders(db: Session) -> list[Order]:
    taken = select(Delivery.order_id)
    return list(db.scalars(select(Order).where(Order.status == S.READY_FOR_PICKUP, Order.fulfillment_type == FulfillmentType.DELIVERY,
                                               Order.id.not_in(taken)).order_by(Order.id)).unique())


def assign_by_admin(db: Session, admin_id, order_id: int, rider: DeliveryRider, force: bool, background=None) -> Order:
    """Operations override: the dispatcher picks the rider. Same safety as a rider accepting (row lock, one rider per order)."""
    from app.core.errors import api_error
    from app.services import order_service, rider_service
    if rider.approval_status != ApprovalStatus.APPROVED:
        raise api_error(409, "rider_not_approved", "That rider isn't approved.")
    o = order_service.lock_order(db, order_id)
    if o is None:
        raise api_error(404, "not_found", "Order not found.")
    if o.fulfillment_type != FulfillmentType.DELIVERY:
        raise api_error(409, "pickup_order", "Pickup orders don't use riders.")
    if o.status != S.READY_FOR_PICKUP or db.scalar(select(Delivery.id).where(Delivery.order_id == o.id)):
        raise api_error(409, "already_taken", "This order isn't waiting for a rider.")
    if not force and len(_active_by_rider(db, [rider.id])[rider.id]) >= MAX_ACTIVE:
        raise api_error(409, "rider_busy", f"That rider already has {MAX_ACTIVE} deliveries. Choose another rider or confirm to override.")
    db.add(Delivery(order_id=o.id, rider_id=rider.id, rider_earning=rider_service.earning_for(o)))
    order_service.change_status(db, o, S.RIDER_ASSIGNED, "admin", admin_id, f"Assigned to {rider.user.profile.full_name} by dispatch", background)
    return o
