"""Candidate retrieval. SQL narrows the marketplace down (availability, budget, meal time, distance box, spice preference)
BEFORE Python scores anything, so the engine stays fast as the number of dishes grows."""
import math
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.geo import haversine_km
from app.models.catalog import MenuItem, Vendor
from app.services import catalog_service as cat, food_taxonomy as tx, quote_service
from app.services.pricing_service import money, vendor_radius_km
from app.services.recommend.types import Candidate, Request


def retrieve(db: Session, req: Request, spice_pref: str | None) -> list[Candidate]:
    visible = cat.public_vendors_query()
    if req.pos and req.max_km:                                       # bounding box first (cheap), exact distance later
        dlat = req.max_km / 111.0
        dlng = req.max_km / (111.0 * max(0.2, math.cos(math.radians(req.pos[0]))))
        visible = visible.where(Vendor.latitude.between(req.pos[0] - dlat, req.pos[0] + dlat),
                                Vendor.longitude.between(req.pos[1] - dlng, req.pos[1] + dlng))
    ids = visible.with_only_columns(Vendor.id)
    stmt = select(MenuItem).where(MenuItem.vendor_id.in_(ids), MenuItem.is_available)
    if req.vendor_id is not None:
        stmt = stmt.where(MenuItem.vendor_id == req.vendor_id)
    if req.budget is not None:
        stmt = stmt.where(MenuItem.price <= req.budget)               # base price: promotions only lower it, so this is a safe superset
    if req.meal_type:
        stmt = stmt.where(MenuItem.meal_types.contains([req.meal_type]))
    if spice_pref == "none" and "spicy" not in req.cravings:
        stmt = stmt.where(MenuItem.spice_level == 0)
    for term in req.terms:
        like = f"%{term}%"
        stmt = stmt.where(MenuItem.name.ilike(like) | MenuItem.tags.ilike(like) | MenuItem.description.ilike(like))
    rows = db.scalars(stmt.order_by(MenuItem.order_count.desc()).limit(get_settings().rec_retrieval_limit)).unique().all()

    out: list[Candidate] = []
    for it in rows:
        v = it.vendor
        if not cat.is_open_now(v):
            continue
        if (req.diet or req.avoid) and not tx.check_diet(list(it.dietary or []), list(it.allergens or []), bool(it.dietary_declared), req.diet, req.avoid)[0]:
            continue                                                  # safety: unknown is never treated as safe
        dist = round(haversine_km(req.pos[0], req.pos[1], v.latitude, v.longitude), 2) if req.pos else None
        if req.max_km is not None and dist is not None and dist > req.max_km:
            continue
        price, original = cat.price_for(it)
        fee = quote_service.delivery_fee(dist)
        ready = quote_service.prep_minutes(v.avg_prep_minutes, [it.prep_minutes])
        out.append(Candidate(item=it, vendor=v, price=money(price), original=money(original) if original else None, distance_km=dist,
                             in_zone=dist is None or dist <= vendor_radius_km(v), fee=fee, ready=ready,
                             eta=quote_service.delivery_eta(ready, dist) if dist is not None else None))
    return out
