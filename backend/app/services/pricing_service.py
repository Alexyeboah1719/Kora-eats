"""ALL money maths lives here and runs on the server only. The browser never sends prices."""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.core.config import get_settings
from app.core.geo import haversine_km
from app.models.catalog import Vendor
from app.models.customer import Address
from app.models.orders import Cart, FulfillmentType
from app.services import catalog_service

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    cart_item: object
    unit_price: Decimal
    original_price: Decimal | None
    quantity: int
    line_total: Decimal
    available: bool


@dataclass
class Totals:
    lines: list[Line] = field(default_factory=list)
    subtotal: Decimal = Decimal("0.00")
    delivery_fee: Decimal | None = None
    discount: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    distance_km: float | None = None
    coupon_code: str | None = None
    coupon_message: str | None = None
    coupon_error: str | None = None
    errors: list[str] = field(default_factory=list)


vendor_radius_km = catalog_service.vendor_radius_km   # one definition of "delivery zone" for the whole app


def compute_discount(db, user, cart: Cart, subtotal: Decimal, delivery_fee: Decimal | None):
    from app.services import coupon_service      # imported here: coupon_service imports this module's money()
    return coupon_service.evaluate(db, user, cart.coupon_code, cart.vendor_id, subtotal, delivery_fee)


def price_cart(db, user, cart: Cart, address: Address | None, fulfillment: FulfillmentType = FulfillmentType.DELIVERY) -> Totals:
    t = Totals()
    vendor = cart.vendor
    for ci in cart.items:
        price, original = catalog_service.price_for(ci.item)
        unit = money(price)
        ok = ci.item.is_available and catalog_service.is_open_now(ci.item.vendor)
        t.lines.append(Line(ci, unit, money(original) if original else None, ci.quantity, money(unit * ci.quantity), ok))
        if not ci.item.is_available:
            t.errors.append(f"{ci.item.name} is no longer available.")
    t.subtotal = money(sum((ln.line_total for ln in t.lines), Decimal("0")))
    if vendor and not catalog_service.is_open_now(vendor):
        t.errors.append(f"{vendor.name} is closed right now.")
    if fulfillment == FulfillmentType.PICKUP:
        t.delivery_fee = Decimal("0.00")            # nothing to deliver, no fee, no radius limit
    elif vendor and address:
        t.distance_km = round(haversine_km(address.latitude, address.longitude, vendor.latitude, vendor.longitude), 2)
        if t.distance_km > vendor_radius_km(vendor):
            t.errors.append(f"{vendor.name} doesn't deliver that far ({t.distance_km:.1f} km; limit {vendor_radius_km(vendor):.0f} km).")
        t.delivery_fee = money(catalog_service.delivery_fee_for(t.distance_km))
    discount, message, error = compute_discount(db, user, cart, t.subtotal, t.delivery_fee)
    t.discount, t.coupon_message, t.coupon_error, t.coupon_code = discount, message, error, cart.coupon_code
    t.total = max(Decimal("0.00"), money(t.subtotal + (t.delivery_fee or 0) - t.discount))
    return t
