"""Coupons. Every rule is enforced here on the server: expiry, minimum order, usage limits, first-order-only,
vendor-specific codes, maximum discount. The browser only sends the code text."""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.catalog import Vendor
from app.models.orders import Order, OrderStatus as S
from app.models.promos import Coupon, CouponType, CouponUsage
from app.models.user import User
from app.services import order_events
from app.services.pricing_service import money

ZERO = Decimal("0.00")


def find(db: Session, code: str, lock: bool = False) -> Coupon | None:
    q = select(Coupon).where(Coupon.code == code.strip().upper())
    return db.scalar(q.with_for_update() if lock else q)


def check(db: Session, user: User, c: Coupon | None, vendor_id: int | None, subtotal: Decimal) -> str | None:
    """Returns a customer-friendly reason the coupon can't be used, or None if it's fine."""
    now = utcnow()
    if c is None or not c.is_active:
        return "That promo code isn't valid."
    if c.starts_at and c.starts_at > now:
        return "This promo code isn't active yet."
    if c.expires_at and c.expires_at < now:
        return "This promo code has expired."
    if c.vendor_id is not None and c.vendor_id != vendor_id:
        name = db.get(Vendor, c.vendor_id).name
        return f"This code only works for orders from {name}."
    if subtotal < c.min_order:
        return f"Spend {cedis(c.min_order)} or more to use this code."
    used_total = db.scalar(select(func.count()).select_from(CouponUsage).where(CouponUsage.coupon_id == c.id))
    if c.usage_limit is not None and used_total >= c.usage_limit:
        return "This promo code has reached its usage limit."
    used_by_user = db.scalar(select(func.count()).select_from(CouponUsage).where(CouponUsage.coupon_id == c.id, CouponUsage.user_id == user.id))
    if used_by_user >= c.per_user_limit:
        return "You've already used this promo code."
    if c.first_order_only and db.scalar(select(Order.id).where(Order.customer_id == user.id, Order.paid_at.is_not(None)).limit(1)):
        return "This code is for first orders only."
    return None


def amount(c: Coupon, subtotal: Decimal, delivery_fee: Decimal | None) -> tuple[Decimal, str | None]:
    """The money off. Never more than the order is worth."""
    if c.type == CouponType.PERCENT:
        d = money(subtotal * c.value / 100)
        if c.max_discount is not None:
            d = min(d, money(c.max_discount))
    elif c.type == CouponType.FIXED:
        d = min(money(c.value), subtotal)
    else:                                         # FREE_DELIVERY
        if delivery_fee is not None and delivery_fee == 0:
            return ZERO, "Free delivery only applies to delivery orders."
        if delivery_fee is None:
            return ZERO, "Free delivery will be applied once you choose an address."
        d = delivery_fee
    return min(d, subtotal + (delivery_fee or ZERO)), None


def evaluate(db: Session, user: User, code: str | None, vendor_id: int | None, subtotal: Decimal,
             delivery_fee: Decimal | None) -> tuple[Decimal, str | None, str | None]:
    """-> (discount, info message, error message). Used for the cart view AND the final order price."""
    if not code:
        return ZERO, None, None
    c = find(db, code)
    err = check(db, user, c, vendor_id, subtotal)
    if err:
        return ZERO, None, err
    d, note = amount(c, subtotal, delivery_fee)
    return d, note or (c.description or f"{c.code} applied"), None


def apply_to_cart(db: Session, user: User, cart, code: str) -> Coupon:
    """Validates NOW so the customer gets instant feedback."""
    if not cart.items:
        raise api_error(422, "empty_cart", "Add something to your cart first.")
    c = find(db, code)
    subtotal = money(sum((Decimal(i.item.price) * i.quantity for i in cart.items), Decimal("0")))
    err = check(db, user, c, cart.vendor_id, subtotal)
    if err:
        raise api_error(422, "coupon_invalid", err)
    cart.coupon_code = c.code
    return c


def record_usage(db: Session, user: User, order: Order) -> None:
    """Called inside order creation. Re-checks the limits under a row lock so two simultaneous
    checkouts can't both use the last redemption."""
    if not order.coupon_code or order.discount <= 0:
        return
    c = find(db, order.coupon_code, lock=True)
    if c is None:
        raise api_error(409, "coupon_invalid", "That promo code isn't valid any more.")
    used_total = db.scalar(select(func.count()).select_from(CouponUsage).where(CouponUsage.coupon_id == c.id))
    used_by_user = db.scalar(select(func.count()).select_from(CouponUsage).where(CouponUsage.coupon_id == c.id, CouponUsage.user_id == user.id))
    if (c.usage_limit is not None and used_total >= c.usage_limit) or used_by_user >= c.per_user_limit:
        raise api_error(409, "coupon_exhausted", "Sorry, that promo code was just used up.")
    db.add(CouponUsage(coupon_id=c.id, user_id=user.id, order_id=order.id, discount=order.discount))


@order_events.subscribe
def release_usage_if_cancelled(db, order, old, new, actor_role, background):
    """A cancelled/refunded order gives the redemption back."""
    if new in (S.CANCELLED, S.REFUNDED):
        for u in db.scalars(select(CouponUsage).where(CouponUsage.order_id == order.id)):
            db.delete(u)
