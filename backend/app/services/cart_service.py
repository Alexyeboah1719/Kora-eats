from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.models.catalog import MenuItem
from app.models.customer import Address
from app.core.geo import haversine_km
from app.models.orders import Cart, CartItem, FulfillmentType
from app.models.user import User
from app.services import catalog_service, pricing_service, quote_service

MAX_QTY, MAX_LINES = 20, 30


def get_cart(db: Session, user: User) -> Cart:
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    if cart is None:
        try:
            with db.begin_nested():                    # two requests creating the first cart at once: one wins, the other re-reads
                cart = Cart(user_id=user.id)
                db.add(cart)
                db.flush()
        except IntegrityError:
            cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    return cart


def _orderable_item(db: Session, item_id: int) -> MenuItem:
    visible = catalog_service.public_vendors_query().subquery()
    it = db.scalar(select(MenuItem).where(MenuItem.id == item_id, MenuItem.vendor_id.in_(select(visible.c.id))))
    if it is None:
        raise api_error(404, "not_found", "That item doesn't exist.")
    if not it.is_available:
        raise api_error(409, "unavailable", f"{it.name} is currently unavailable.")
    if not catalog_service.is_open_now(it.vendor):
        raise api_error(409, "vendor_closed", f"{it.vendor.name} is closed right now.")
    return it


def add_item(db: Session, user: User, item_id: int, quantity: int, instructions: str | None, replace: bool) -> Cart:
    it = _orderable_item(db, item_id)
    cart = get_cart(db, user)
    db.execute(select(Cart.id).where(Cart.id == cart.id).with_for_update())     # one change at a time per cart (a double-tap must not duplicate or fail)
    db.refresh(cart)                                                              # see what the request that just finished added
    if cart.items and cart.vendor_id != it.vendor_id:
        if not replace:
            raise api_error(409, "different_vendor",
                            f"Your cart has items from another vendor. Start a new cart with {it.vendor.name}?")
        cart.items.clear()
        cart.coupon_code = None
    cart.vendor_id = it.vendor_id
    line = next((c for c in cart.items if c.menu_item_id == item_id), None)
    if line:
        line.quantity = min(MAX_QTY, line.quantity + quantity)
        if instructions is not None:
            line.special_instructions = instructions
    else:
        if len(cart.items) >= MAX_LINES:
            raise api_error(400, "cart_full", "Your cart is full.")
        cart.items.append(CartItem(menu_item_id=item_id, quantity=min(MAX_QTY, quantity), special_instructions=instructions))
    db.commit()
    return cart


def _line(cart: Cart, line_id: int) -> CartItem:
    line = next((c for c in cart.items if c.id == line_id), None)
    if line is None:
        raise api_error(404, "not_found", "Item not in your cart.")
    return line


def update_line(db: Session, user: User, line_id: int, quantity: int | None, instructions: str | None) -> Cart:
    cart = get_cart(db, user)
    line = _line(cart, line_id)
    if quantity is not None:
        if quantity <= 0:
            cart.items.remove(line)
        else:
            line.quantity = min(MAX_QTY, quantity)
    if instructions is not None and quantity != 0:
        line.special_instructions = instructions or None
    if not cart.items:
        cart.vendor_id, cart.coupon_code = None, None
    db.commit()
    return cart


def clear(db: Session, user: User) -> None:
    cart = get_cart(db, user)
    cart.items.clear()
    cart.vendor_id, cart.coupon_code = None, None
    db.commit()


def _options(db: Session, user: User, cart: Cart, address: Address | None) -> dict | None:
    """Delivery and pickup side by side (fee, time, total) plus the 'pickup saves...' message."""
    if not cart.vendor or not cart.items:
        return None
    v = cart.vendor
    dist = round(haversine_km(address.latitude, address.longitude, v.latitude, v.longitude), 2) if address else None
    opts = quote_service.compare(v.avg_prep_minutes, [ci.item.prep_minutes for ci in cart.items], dist,
                                 in_zone=dist is None or dist <= pricing_service.vendor_radius_km(v))
    opts["pickup"]["total"] = float(pricing_service.price_cart(db, user, cart, None, FulfillmentType.PICKUP).total)
    opts["pickup"]["address"] = v.address_text
    if opts["delivery"]:
        opts["delivery"]["total"] = float(pricing_service.price_cart(db, user, cart, address, FulfillmentType.DELIVERY).total)
    return opts


def view(db: Session, user: User, address: Address | None, fulfillment: str = "DELIVERY") -> dict:
    cart = get_cart(db, user)
    ful = FulfillmentType(fulfillment)
    t = pricing_service.price_cart(db, user, cart, address, ful)
    
    # Check if customer is blocked from COD
    from app.models.customer import Customer
    from app.core.timeutil import utcnow
    customer = db.get(Customer, user.id)
    cod_blocked = False
    if customer and customer.cod_blocked_until:
        cod_blocked = customer.cod_blocked_until > utcnow()
    
    return {
        "vendor": {"id": cart.vendor.id, "name": cart.vendor.name, "cod_enabled": cart.vendor.cod_enabled} if cart.vendor else None,
        "items": [{"id": ln.cart_item.id, "menu_item_id": ln.cart_item.menu_item_id, "name": ln.cart_item.item.name,
                   "image_url": ln.cart_item.item.image_url, "unit_price": float(ln.unit_price),
                   "original_price": float(ln.original_price) if ln.original_price else None, "quantity": ln.quantity,
                   "line_total": float(ln.line_total), "special_instructions": ln.cart_item.special_instructions,
                   "available": ln.available} for ln in t.lines],
        "item_count": sum(ln.quantity for ln in t.lines),
        "subtotal": float(t.subtotal), "delivery_fee": float(t.delivery_fee) if t.delivery_fee is not None else None,
        "discount": float(t.discount), "total": float(t.total), "distance_km": t.distance_km,
        "coupon_code": t.coupon_code, "coupon_message": t.coupon_message, "coupon_error": t.coupon_error, "errors": t.errors,
        "fulfillment": ful.value, "options": _options(db, user, cart, address),
        "customer_cod_blocked": cod_blocked,
        "customer_cod_blocked_until": customer.cod_blocked_until.isoformat() if customer and customer.cod_blocked_until else None,
        "customer_cod_penalty_balance": customer.cod_penalty_balance if customer else 0.0,
    }
