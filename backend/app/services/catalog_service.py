"""Read-side catalog logic: vendors, menu items, search, home page."""
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.geo import haversine_km
from app.core.timeutil import utcnow
from app.models.catalog import ApprovalStatus, BUSINESS_LABELS, BusinessType, Category, MenuItem, Promotion, Vendor
from app.models.customer import Favorite
from app.models.user import User
from app.core import share
from app.services import food_taxonomy as tx, quote_service
from app.services.vendor_hours import is_open_at
from app.schemas.catalog import (
    CategoryOut, HomeOut, MenuItemOut, MenuSection, VendorCard, VendorDetail,
)

Pos = tuple[float, float] | None


# ------------------------------------------------------------------ seams other phases extend
def public_vendors_query() -> Select:
    """Vendors customers may see: approved only."""
    return select(Vendor).where(Vendor.approval_status == ApprovalStatus.APPROVED)


def is_open_now(vendor: Vendor) -> bool:
    return is_open_at(vendor)


def active_promotion_percent(item: MenuItem, now=None) -> int:
    """Best discount among the vendor's promotions that are active right now (item-specific or whole-menu)."""
    now = now or utcnow()
    return max((p.discount_percent for p in item.vendor.promotions
                if p.is_active and p.starts_at <= now <= p.ends_at and p.menu_item_id in (None, item.id)), default=0)


def price_for(item: MenuItem) -> tuple[float, float | None]:
    """(price to charge, original price if discounted)."""
    pct = active_promotion_percent(item)
    if not pct:
        return float(item.price), None
    discounted = (Decimal(item.price) * (100 - pct) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(discounted), float(item.price)


def vendor_radius_km(v: Vendor) -> float:
    return v.delivery_radius_km or get_settings().default_delivery_radius_km


def delivery_fee_for(distance_km: float | None) -> float | None:
    fee = quote_service.delivery_fee(distance_km)
    return None if fee is None else float(fee)


# ------------------------------------------------------------------ builders
def vendor_card(v: Vendor, pos: Pos = None, fav_vendors: set[int] = frozenset()) -> VendorCard:
    dist = round(haversine_km(pos[0], pos[1], v.latitude, v.longitude), 2) if pos else None
    return VendorCard(
        id=v.id, name=v.name, description=v.description, category=v.category.name if v.category else None,
        logo_url=v.logo_url, cover_url=v.cover_url, rating_avg=round(v.rating_avg, 1), rating_count=v.rating_count,
        avg_prep_minutes=v.avg_prep_minutes, is_open=is_open_now(v), business_type=v.business_type.value,
        business_label=BUSINESS_LABELS[v.business_type.value], area=v.area, region=v.region, share_path=share.vendor_path(v.id, v.name), distance_km=dist,
        delivery_fee=delivery_fee_for(dist), delivery_radius_km=vendor_radius_km(v),
        in_zone=(dist <= vendor_radius_km(v)) if dist is not None else None, is_favorite=v.id in fav_vendors,
    )


def item_out(i: MenuItem, pos: Pos = None, fav_items: set[int] = frozenset()) -> MenuItemOut:
    price, original = price_for(i)
    dist = round(haversine_km(pos[0], pos[1], i.vendor.latitude, i.vendor.longitude), 2) if pos else None
    return MenuItemOut(
        id=i.id, vendor_id=i.vendor_id, vendor_name=i.vendor.name, name=i.name, description=i.description,
        price=price, original_price=original, image_url=i.image_url, prep_minutes=i.prep_minutes,
        is_available=i.is_available and is_open_now(i.vendor), is_spicy=i.is_spicy, tags=i.tag_list,
        category=i.category.name if i.category else None, distance_km=dist, is_favorite=i.id in fav_items,
        traits=list(i.traits or []), meal_types=list(i.meal_types or []), dietary=list(i.dietary or []), allergens=list(i.allergens or []),
        dietary_declared=bool(i.dietary_declared), spice_level=i.spice_level or 0, share_path=share.food_path(i.id, i.name),
    )


def favorite_ids(db: Session, user: User | None) -> tuple[set[int], set[int]]:
    if user is None:
        return set(), set()
    rows = db.execute(select(Favorite.vendor_id, Favorite.menu_item_id).where(Favorite.user_id == user.id)).all()
    return {r[0] for r in rows if r[0]}, {r[1] for r in rows if r[1]}


# ------------------------------------------------------------------ queries
def list_categories(db: Session) -> list[CategoryOut]:
    rows = db.scalars(select(Category).where(Category.is_active).order_by(Category.sort_order, Category.name)).all()
    return [CategoryOut.model_validate(c) for c in rows]


def search_vendors(db: Session, user: User | None, pos: Pos = None, q: str | None = None,
                   category_id: int | None = None, max_km: float | None = None, limit: int = 30,
                   business_type: str | None = None) -> list[VendorCard]:
    stmt = public_vendors_query()
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Vendor.name.ilike(like), Vendor.description.ilike(like), Vendor.area.ilike(like), Vendor.address_text.ilike(like),
                              Vendor.id.in_(select(MenuItem.vendor_id).where(MenuItem.name.ilike(like)))))
    if business_type:
        stmt = stmt.where(Vendor.business_type == BusinessType(business_type))
    if category_id:
        stmt = stmt.where(or_(Vendor.category_id == category_id,
                              Vendor.id.in_(select(MenuItem.vendor_id).where(MenuItem.category_id == category_id))))
    favs, _ = favorite_ids(db, user)
    cards = [vendor_card(v, pos, favs) for v in db.scalars(stmt).unique().all()]
    if max_km is not None and pos:
        cards = [c for c in cards if c.distance_km is not None and c.distance_km <= max_km]
    cards.sort(key=lambda c: (c.distance_km if c.distance_km is not None else 9999, -c.rating_avg))
    if not pos:
        cards.sort(key=lambda c: -c.rating_avg)
    return cards[:limit]


def vendor_detail(db: Session, user: User | None, vendor_id: int, pos: Pos = None) -> VendorDetail | None:
    v = db.scalar(public_vendors_query().where(Vendor.id == vendor_id))
    if v is None:
        return None
    favs_v, favs_i = favorite_ids(db, user)
    sections: dict[str, list[MenuItemOut]] = {}
    for i in sorted(v.items, key=lambda i: (i.category.name if i.category else "zzz", i.name)):
        sections.setdefault(i.category.name if i.category else "Other", []).append(item_out(i, pos, favs_i))
    card = vendor_card(v, pos, favs_v)
    return VendorDetail(**card.model_dump(), address_text=v.address_text, phone=v.phone, latitude=v.latitude,
                        longitude=v.longitude, menu=[MenuSection(category=k, items=x) for k, x in sections.items()])


def search_items(db: Session, user: User | None, pos: Pos = None, q: str | None = None, category_id: int | None = None,
                 vendor_id: int | None = None, min_price: float | None = None, max_price: float | None = None,
                 spicy: bool | None = None, available_only: bool = True, min_rating: float | None = None,
                 max_prep: int | None = None, max_km: float | None = None, sort: str = "popular",
                 limit: int = 40, *, traits: list[str] | None = None, meal_types: list[str] | None = None,
                 dietary: list[str] | None = None, avoid_allergens: list[str] | None = None,
                 business_type: str | None = None, open_only: bool = False) -> list[MenuItemOut]:
    visible = public_vendors_query().subquery()
    stmt = select(MenuItem).where(MenuItem.vendor_id.in_(select(visible.c.id)))
    
    # Filter by open vendors if requested
    if open_only:
        # Get currently open vendor IDs
        now = utcnow()
        open_vendor_ids = set()
        for v in db.scalars(select(Vendor).where(Vendor.approval_status == ApprovalStatus.APPROVED)):
            if is_open_at(v, now):
                open_vendor_ids.add(v.id)
        stmt = stmt.where(MenuItem.vendor_id.in_(open_vendor_ids))
    
    if q:
        for word in q.lower().split():
            like = f"%{word}%"
            stmt = stmt.where(or_(MenuItem.name.ilike(like), MenuItem.description.ilike(like),
                                  MenuItem.tags.ilike(like),
                                  MenuItem.vendor_id.in_(select(Vendor.id).where(Vendor.name.ilike(like)))))
    if category_id:
        stmt = stmt.where(MenuItem.category_id == category_id)
    if vendor_id:
        stmt = stmt.where(MenuItem.vendor_id == vendor_id)
    if min_price is not None:
        stmt = stmt.where(MenuItem.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(MenuItem.price <= max_price)
    if spicy is not None:
        stmt = stmt.where(MenuItem.is_spicy == spicy)
    if available_only:
        stmt = stmt.where(MenuItem.is_available)
    if max_prep is not None:
        stmt = stmt.where(MenuItem.prep_minutes <= max_prep)
    if min_rating is not None:
        stmt = stmt.where(MenuItem.vendor_id.in_(select(Vendor.id).where(Vendor.rating_avg >= min_rating)))
    for t in traits or []:
        stmt = stmt.where(MenuItem.traits.contains([t]))
    if meal_types:
        stmt = stmt.where(or_(*[MenuItem.meal_types.contains([m]) for m in meal_types]))
    if business_type:
        stmt = stmt.where(MenuItem.vendor_id.in_(select(Vendor.id).where(Vendor.business_type == BusinessType(business_type))))
    _, favs_i = favorite_ids(db, user)
    rows = db.scalars(stmt.order_by(MenuItem.order_count.desc())).unique().all()
    if dietary or avoid_allergens:     # safety: unknown is never treated as safe (see food_taxonomy.check_diet)
        rows = [r for r in rows if tx.check_diet(list(r.dietary or []), list(r.allergens or []), bool(r.dietary_declared), dietary or [], avoid_allergens or [])[0]]
    items = [item_out(i, pos, favs_i) for i in rows]
    if available_only:
        items = [i for i in items if i.is_available]  # also hides closed vendors
    if max_km is not None and pos:
        items = [i for i in items if i.distance_km is not None and i.distance_km <= max_km]
    if sort == "price_asc":
        items.sort(key=lambda i: i.price)
    elif sort == "price_desc":
        items.sort(key=lambda i: -i.price)
    elif sort == "distance" and pos:
        items.sort(key=lambda i: i.distance_km or 9999)
    return items[:limit]


def greeting_for(user: User | None, now: datetime | None = None) -> str:
    hour = (now or utcnow()).hour  # Ghana is UTC+0 all year
    part = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    return f"{part}, {user.profile.full_name.split()[0]}" if user else f"{part}!"


def home(db: Session, user: User | None, pos: Pos = None) -> HomeOut:
    popular = search_items(db, user, pos, limit=8)
    from app.services.recommend import engine      # imported here: the engine itself uses this module
    headline, recommended = engine.feed_items(db, user, pos, limit=8)
    return HomeOut(
        promotions=active_promotions(db), greeting=greeting_for(user), headline=headline, categories=list_categories(db),
        nearby_vendors=search_vendors(db, user, pos, limit=8), popular_items=popular, recommended_items=recommended,
    )


def recommended_items(db: Session, user: User | None, pos: Pos = None, limit: int = 8) -> list[MenuItemOut]:
    """Personalised when we know the user: categories of their favourites first, then top-rated vendors."""
    favs_v, favs_i = favorite_ids(db, user)
    pool = search_items(db, user, pos, limit=60)
    if favs_i or favs_v:
        liked_cats = {c for i in pool if i.id in favs_i for c in [i.category] if c}
        liked_vendors = favs_v
        pool.sort(key=lambda i: (i.category in liked_cats) + 2 * (i.vendor_id in liked_vendors), reverse=True)
    else:
        ratings = {v.id: v.rating_avg for v in db.scalars(public_vendors_query()).unique().all()}
        pool.sort(key=lambda i: ratings.get(i.vendor_id, 0), reverse=True)
    return [i for i in pool if i.id not in favs_i][:limit] or pool[:limit]


def active_promotions(db: Session, limit: int = 8) -> list[dict]:
    now = utcnow()
    rows = db.scalars(select(Promotion).join(Vendor, Vendor.id == Promotion.vendor_id).where(
        Promotion.is_active, Promotion.starts_at <= now, Promotion.ends_at >= now,
        Vendor.approval_status == ApprovalStatus.APPROVED).order_by(Promotion.discount_percent.desc()).limit(limit)).all()
    vendors = {v.id: v for v in db.scalars(select(Vendor).where(Vendor.id.in_({p.vendor_id for p in rows}))).unique()} if rows else {}
    return [{"id": p.id, "title": p.title, "badge": f"{p.discount_percent}% off", "vendor_id": p.vendor_id,
             "vendor_name": vendors[p.vendor_id].name, "ends_at": p.ends_at.isoformat()} for p in rows]
