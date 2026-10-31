"""Orders: creation from the cart, the status state machine, and serialisation."""
import secrets
from collections.abc import Callable
from datetime import timedelta, timezone
from decimal import Decimal

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.models.customer import Address
from app.models.orders import FulfillmentType, Order, OrderItem, OrderStatus as S, OrderStatusHistory
from app.models.user import User
from app.schemas.orders import CheckoutIn
from app.core.config import get_settings
from app.services import cart_service, coupon_service, order_events, pricing_service, vendor_hours

# --------------------------------------------------------------------------- the state machine
TRANSITIONS: dict[S, set[S]] = {
    S.PENDING_PAYMENT: {S.PAID, S.PAYMENT_FAILED, S.CANCELLED},
    S.PAYMENT_FAILED: {S.PENDING_PAYMENT, S.CANCELLED},           # retry payment, or give up
    S.PAID: {S.CONFIRMED, S.CANCELLED, S.REFUNDED},
    S.CONFIRMED: {S.PREPARING, S.CANCELLED},
    S.PREPARING: {S.READY_FOR_PICKUP, S.CANCELLED},
    S.READY_FOR_PICKUP: {S.RIDER_ASSIGNED, S.COLLECTED, S.CANCELLED},   # RIDER_ASSIGNED for delivery, COLLECTED for pickup
    S.RIDER_ASSIGNED: {S.PICKED_UP, S.READY_FOR_PICKUP, S.CANCELLED},
    S.PICKED_UP: {S.OUT_FOR_DELIVERY},
    S.OUT_FOR_DELIVERY: {S.DELIVERED},
    S.DELIVERED: {S.REFUNDED},
    S.COLLECTED: {S.REFUNDED},
    S.CANCELLED: {S.REFUNDED},
    S.REFUNDED: set(),
}
# Who may perform which transition. Admin may perform any transition that exists in TRANSITIONS.
ROLE_RULES: dict[str, set[tuple[S, S]]] = {
    "customer": {(S.PENDING_PAYMENT, S.CANCELLED), (S.PAYMENT_FAILED, S.CANCELLED), (S.PAYMENT_FAILED, S.PENDING_PAYMENT)},
    "vendor": {(S.PAID, S.CONFIRMED), (S.PAID, S.CANCELLED), (S.CONFIRMED, S.PREPARING), (S.CONFIRMED, S.CANCELLED),
               (S.PREPARING, S.READY_FOR_PICKUP), (S.READY_FOR_PICKUP, S.COLLECTED)},
    "rider": {(S.READY_FOR_PICKUP, S.RIDER_ASSIGNED), (S.RIDER_ASSIGNED, S.PICKED_UP), (S.RIDER_ASSIGNED, S.READY_FOR_PICKUP),
              (S.PICKED_UP, S.OUT_FOR_DELIVERY), (S.OUT_FOR_DELIVERY, S.DELIVERED)},
    "system": {(S.PENDING_PAYMENT, S.PAID), (S.PENDING_PAYMENT, S.PAYMENT_FAILED), (S.PENDING_PAYMENT, S.CANCELLED),
               (S.PAYMENT_FAILED, S.PENDING_PAYMENT), (S.PAYMENT_FAILED, S.CANCELLED)},
}


COMPLETED = (S.DELIVERED, S.COLLECTED)     # an order that reached the customer, either way


def change_status(db: Session, order: Order, new: S, actor_role: str, actor_id=None, note: str | None = None,
                  background: BackgroundTasks | None = None) -> Order:
    """The ONLY place an order's status changes. Validates, records history, emits an event.
    The caller commits."""
    old = order.status
    if new not in TRANSITIONS[old]:
        raise api_error(409, "invalid_transition", f"An order that is {old.value} can't become {new.value}.")
    if new == S.RIDER_ASSIGNED and order.fulfillment_type == FulfillmentType.PICKUP:
        raise api_error(409, "invalid_transition", "Pickup orders don't use a rider.")
    if new == S.COLLECTED and order.fulfillment_type != FulfillmentType.PICKUP:
        raise api_error(409, "invalid_transition", "Only pickup orders can be collected.")
    if actor_role != "admin" and (old, new) not in ROLE_RULES.get(actor_role, set()):
        raise api_error(403, "forbidden_transition", "You aren't allowed to make that change.")
    order.status = new
    if new == S.PAID:
        order.paid_at = utcnow()
    elif new in COMPLETED:
        order.delivered_at = utcnow()          # 'completed at' for both delivery and pickup
    elif new == S.CANCELLED:
        order.cancelled_reason = note
    order.history.append(OrderStatusHistory(from_status=old, to_status=new, actor_user_id=actor_id,
                                            actor_role=actor_role, note=note))   # append keeps order.history current
    db.flush()
    order_events.emit(db, order, old, new, actor_role, background)
    return order


def lock_order(db: Session, order_id: int) -> Order | None:
    """Row-lock the order so two requests can't change it at the same time (e.g. two riders accepting)."""
    return db.scalar(select(Order).where(Order.id == order_id).with_for_update(of=Order))


# --------------------------------------------------------------------------- creation
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"   # no 0/O/1/I/L: easy to read out over the phone


def _new_pickup_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(6))


def _new_code(db: Session) -> str:
    while True:
        code = "KE-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))
        if db.scalar(select(Order.id).where(Order.code == code)) is None:
            return code


def create_order(db: Session, user: User, data: CheckoutIn) -> Order:
    pickup = data.fulfillment_type == FulfillmentType.PICKUP.value
    addr = None
    if not pickup:
        addr = db.scalar(select(Address).where(Address.id == data.address_id, Address.user_id == user.id)) if data.address_id else None
        if addr is None:
            raise api_error(404, "address_not_found", "Choose a delivery address.")
    phone = data.phone or user.profile.phone
    if not phone:
        raise api_error(422, "phone_required", "Add a phone number so the rider and payment provider can reach you.")
    cart = cart_service.get_cart(db, user)
    if not cart.items:
        raise api_error(400, "empty_cart", "Your cart is empty.")
    t = pricing_service.price_cart(db, user, cart, addr, FulfillmentType(data.fulfillment_type))
    if t.errors:
        raise api_error(409, "cart_invalid", t.errors[0])
    if cart.coupon_code and t.coupon_error:
        raise api_error(409, "coupon_invalid", t.coupon_error)
    scheduled = _validate_schedule(cart.vendor, data.scheduled_for)
    funded_by, delivery_part = None, Decimal("0.00")
    if t.discount > 0 and t.coupon_code:                 # remember WHO pays for the discount, so vendors and the platform are charged fairly later
        from app.models.promos import CouponType
        from app.services import coupon_service
        c = coupon_service.find(db, t.coupon_code)
        if c is not None:
            funded_by = "VENDOR" if c.vendor_id else "PLATFORM"      # a vendor's own coupon costs the vendor; an admin (platform-wide) coupon costs KORA EATS
            delivery_part = t.discount if c.type == CouponType.FREE_DELIVERY else Decimal("0.00")
    order = Order(scheduled_for=scheduled, coupon_funded_by=funded_by, discount_delivery_part=delivery_part, 
        code=_new_code(db), customer_id=user.id, vendor_id=cart.vendor_id, status=S.PENDING_PAYMENT,
        subtotal=t.subtotal, delivery_fee=t.delivery_fee or Decimal("0.00"), discount=t.discount, total=t.total, coupon_code=t.coupon_code,
        fulfillment_type=FulfillmentType(data.fulfillment_type), pickup_code=_new_pickup_code() if pickup else None,
        payment_mode=data.payment_mode or "ONLINE",
        # pickup: the "delivery" point is simply the vendor's own location
        delivery_address=(cart.vendor.address_text if pickup else f"{addr.address_line}, {addr.city}")[:300],
        delivery_lat=cart.vendor.latitude if pickup else addr.latitude, delivery_lng=cart.vendor.longitude if pickup else addr.longitude,
        delivery_notes=None if pickup else addr.delivery_notes, customer_phone=phone, notes=data.notes,
    )
    order.items = [OrderItem(menu_item_id=ln.cart_item.menu_item_id, name=ln.cart_item.item.name, unit_price=ln.unit_price,
                             quantity=ln.quantity, line_total=ln.line_total,
                             special_instructions=ln.cart_item.special_instructions) for ln in t.lines]
    db.add(order)
    db.flush()
    
    # All orders (including COD) start at PENDING_PAYMENT and go through normal vendor acceptance flow
    # COD orders will skip the actual payment verification but still need vendor acceptance
    order.history.append(OrderStatusHistory(from_status=None, to_status=S.PENDING_PAYMENT, actor_user_id=user.id,
                                            actor_role="customer", note=f"Order placed - {data.payment_mode or 'Online payment'}"))
    
    if not user.profile.phone:
        user.profile.phone = phone
    on_order_created(db, user, cart, order)
    order_events.emit(db, order, None, order.status, "customer", None)
    cart.items.clear()
    cart.vendor_id, cart.coupon_code = None, None
    db.commit()
    return order


def _validate_schedule(vendor, when):
    """'Deliver later': between the minimum lead time and a week ahead, while the vendor is open."""
    if when is None:
        return None
    s = get_settings()
    when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)   # Ghana = UTC+0
    now = utcnow()
    if when < now + timedelta(minutes=s.scheduled_min_lead_minutes):
        raise api_error(422, "schedule_too_soon", f"Choose a time at least {s.scheduled_min_lead_minutes} minutes from now, or deliver now.")
    if when > now + timedelta(days=s.scheduled_max_days):
        raise api_error(422, "schedule_too_far", f"You can schedule up to {s.scheduled_max_days} days ahead.")
    if not vendor_hours.is_open_at(vendor, when):
        raise api_error(409, "vendor_closed_then", f"{vendor.name} is closed at that time. Pick a time within their opening hours.")
    return when


def on_order_created(db: Session, user: User, cart, order: Order) -> None:
    coupon_service.record_usage(db, user, order)


# --------------------------------------------------------------------------- serialisation
_serializers: list[Callable[[Session, Order], dict]] = []


def register_serializer(fn: Callable[[Session, Order], dict]) -> Callable:
    """Later phases add fields (payment, rider, review) to the order JSON without editing this file."""
    _serializers.append(fn)
    return fn


def order_out(db: Session, o: Order, for_vendor: bool = False) -> dict:
    out = {
        "id": o.id, "code": o.code, "status": o.status.value, "payment_mode": o.payment_mode or "ONLINE",
        "vendor": {"id": o.vendor.id, "name": o.vendor.name, "phone": o.vendor.phone, "address": o.vendor.address_text,
                   "lat": o.vendor.latitude, "lng": o.vendor.longitude, "cod_enabled": o.vendor.cod_enabled},
        "items": [{"name": i.name, "quantity": i.quantity, "unit_price": float(i.unit_price),
                   "line_total": float(i.line_total), "special_instructions": i.special_instructions} for i in o.items],
        "subtotal": float(o.subtotal), "delivery_fee": float(o.delivery_fee), "discount": float(o.discount),
        "total": float(o.total), "coupon_code": o.coupon_code, "delivery_address": o.delivery_address,
        "delivery_lat": o.delivery_lat, "delivery_lng": o.delivery_lng, "delivery_notes": o.delivery_notes,
        "customer_phone": o.customer_phone, "notes": o.notes, "cancelled_reason": o.cancelled_reason,
        "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
        "released_at": o.released_at.isoformat() if o.released_at else None,
        "created_at": o.created_at.isoformat(), "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
        "history": [{"status": h.to_status.value, "at": h.created_at.isoformat(), "note": h.note} for h in o.history],
        "can_cancel": o.status in (S.PENDING_PAYMENT, S.PAYMENT_FAILED),
        "can_pay": o.status in (S.PENDING_PAYMENT, S.PAYMENT_FAILED),
        "refund_required": o.status == S.CANCELLED and o.paid_at is not None,
        "fulfillment_type": o.fulfillment_type.value,
        "pickup_code": o.pickup_code if (o.fulfillment_type == FulfillmentType.PICKUP and not for_vendor) else None,
    }
    for fn in _serializers:
        out.update(fn(db, o))
    return out
