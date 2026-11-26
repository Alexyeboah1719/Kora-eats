"""Personal Food Memory: what this customer's own orders, favourites and reviews say about their taste and spending.
Built on the fly from real data (no hidden profile). Respects 'personalisation off' and 'forget my history'."""
import statistics
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import CustomerPreference
from app.models.catalog import Category, MenuItem, Vendor
from app.models.customer import Favorite
from app.models.orders import Order, OrderItem
from app.models.reviews import Review
from app.models.user import User
from app.services.order_service import COMPLETED

MAX_ORDERS = 200


@dataclass
class Memory:
    n_orders: int = 0
    item_counts: Counter = field(default_factory=Counter)
    vendor_counts: Counter = field(default_factory=Counter)
    category_counts: Counter = field(default_factory=Counter)
    category_names: dict[int, str] = field(default_factory=dict)
    liked_items: set[int] = field(default_factory=set)
    fav_items: set[int] = field(default_factory=set)
    fav_vendors: set[int] = field(default_factory=set)
    disliked_vendors: dict[int, int] = field(default_factory=dict)      # vendor id -> the rating they gave
    typical_price: Decimal | None = None                                  # median price of dishes they order
    typical_budget: Decimal | None = None                                 # 75th percentile of their food spend per order
    spicy_share: float | None = None
    usual_hours: Counter = field(default_factory=Counter)
    spice_preference: str | None = None
    has_signal: bool = False                                              # any history/favourites at all?

    def top_categories(self, n=3) -> list[str]:
        return [self.category_names[c] for c, _ in self.category_counts.most_common(n) if c in self.category_names]


def get_prefs(db: Session, user: User | None) -> CustomerPreference | None:
    return db.get(CustomerPreference, user.id) if user is not None else None


def load(db: Session, user: User | None, prefs: CustomerPreference | None = None) -> Memory | None:
    """None when there is no signed-in user or they switched personalisation off."""
    if user is None:
        return None
    prefs = prefs if prefs is not None else get_prefs(db, user)
    if prefs is not None and not prefs.personalization_enabled:
        return None
    m = Memory(spice_preference=prefs.spice_preference if prefs else None)
    since = prefs.memory_reset_at if prefs else None

    q = select(Order).where(Order.customer_id == user.id, Order.status.in_(COMPLETED))
    if since is not None:
        q = q.where(Order.created_at > since)
    orders = db.scalars(q.order_by(Order.id.desc()).limit(MAX_ORDERS)).unique().all()
    m.n_orders = len(orders)
    if orders:
        ids = [o.id for o in orders]
        rows = db.execute(select(OrderItem.order_id, OrderItem.menu_item_id, OrderItem.unit_price, OrderItem.quantity, MenuItem.category_id, MenuItem.spice_level)
                          .join(MenuItem, MenuItem.id == OrderItem.menu_item_id, isouter=True).where(OrderItem.order_id.in_(ids))).all()
        prices, spicy_qty, total_qty = [], 0, 0
        for _, item_id, price, qty, cat_id, spice in rows:
            if item_id:
                m.item_counts[item_id] += qty
            if cat_id:
                m.category_counts[cat_id] += qty
            prices += [float(price)] * qty
            total_qty += qty
            spicy_qty += qty if (spice or 0) > 0 else 0
        for o in orders:
            m.vendor_counts[o.vendor_id] += 1
            m.usual_hours[o.created_at.hour] += 1
        m.typical_price = Decimal(str(round(statistics.median(prices), 2))) if prices else None
        spends = sorted(float(o.subtotal) for o in orders)
        m.typical_budget = Decimal(str(round(spends[min(len(spends) - 1, int(0.75 * len(spends)))], 2)))
        m.spicy_share = spicy_qty / total_qty if total_qty else None
        for oid, rating in db.execute(select(Review.order_id, Review.food_rating).where(Review.customer_id == user.id, Review.order_id.in_(ids))):
            if rating >= 4:
                m.liked_items |= {i for o_id, i, *_ in rows if o_id == oid and i}
        for vid, rating in db.execute(select(Review.vendor_id, Review.vendor_rating).where(Review.customer_id == user.id, Review.vendor_rating <= 2)):
            m.disliked_vendors[vid] = rating
    favs = db.execute(select(Favorite.vendor_id, Favorite.menu_item_id).where(Favorite.user_id == user.id)).all()
    m.fav_vendors = {v for v, _ in favs if v}
    m.fav_items = {i for _, i in favs if i}
    if m.category_counts:
        m.category_names = dict(db.execute(select(Category.id, Category.name).where(Category.id.in_(list(m.category_counts)))).all())
    m.has_signal = bool(m.n_orders or m.fav_items or m.fav_vendors or m.spice_preference)
    return m


def effective_spice(m: Memory | None) -> str | None:
    """The customer's stated preference wins; otherwise infer a lean from their order history (only with enough orders)."""
    if m is None:
        return None
    if m.spice_preference:
        return m.spice_preference
    if m.n_orders >= 3 and m.spicy_share is not None:
        return "hot" if m.spicy_share >= 0.7 else "none_lean" if m.spicy_share <= 0.1 else None
    return None
