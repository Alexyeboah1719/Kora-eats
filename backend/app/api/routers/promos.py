"""Coupons + promotions: vendor-managed (/vendor/*) and admin-managed (/admin/*)."""
from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.routers.vendor import require_vendor
from app.core.errors import api_error
from app.db.session import get_db
from app.models.catalog import MenuItem, Promotion, Vendor
from app.models.promos import Coupon, CouponUsage
from app.models.user import UserRole
from app.schemas.auth import MessageResponse
from app.schemas.promos import CouponIn, PromotionIn

vendor_router = APIRouter(prefix="/vendor", tags=["vendor-promos"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_roles(UserRole.ADMIN))])


def _aware(dt):
    return None if dt is None else (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt)


def coupon_out(db: Session, c: Coupon) -> dict:
    used = db.scalar(select(func.count()).select_from(CouponUsage).where(CouponUsage.coupon_id == c.id))
    return {"id": c.id, "code": c.code, "description": c.description, "type": c.type.value, "value": float(c.value),
            "min_order": float(c.min_order), "max_discount": float(c.max_discount) if c.max_discount is not None else None,
            "starts_at": c.starts_at.isoformat() if c.starts_at else None, "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            "usage_limit": c.usage_limit, "per_user_limit": c.per_user_limit, "first_order_only": c.first_order_only,
            "vendor_id": c.vendor_id, "is_active": c.is_active, "times_used": used}


def promo_out(p: Promotion) -> dict:
    return {"id": p.id, "vendor_id": p.vendor_id, "menu_item_id": p.menu_item_id, "title": p.title, "description": p.description,
            "discount_percent": p.discount_percent, "starts_at": p.starts_at.isoformat(), "ends_at": p.ends_at.isoformat(), "is_active": p.is_active}


def _save_coupon(db: Session, data: CouponIn, vendor_id: int | None, existing: Coupon | None = None) -> Coupon:
    clash = db.scalar(select(Coupon).where(Coupon.code == data.code))
    if clash is not None and (existing is None or clash.id != existing.id):
        raise api_error(409, "code_taken", "That code already exists. Pick another.")
    c = existing or Coupon(vendor_id=vendor_id)
    for k, v in data.model_dump().items():
        setattr(c, k, _aware(v) if k in ("starts_at", "expires_at") else v)
    db.add(c)
    db.commit()
    return c


# ------------------------------------------------------------------ vendor: coupons
def _own_coupon(db, v, cid):
    c = db.scalar(select(Coupon).where(Coupon.id == cid, Coupon.vendor_id == v.id))
    if c is None:
        raise api_error(404, "not_found", "Coupon not found.")
    return c


@vendor_router.get("/coupons")
def my_coupons(v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    return [coupon_out(db, c) for c in db.scalars(select(Coupon).where(Coupon.vendor_id == v.id).order_by(Coupon.id.desc()))]


@vendor_router.post("/coupons", status_code=201)
def create_my_coupon(data: CouponIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    return coupon_out(db, _save_coupon(db, data, v.id))


@vendor_router.put("/coupons/{coupon_id}")
def update_my_coupon(coupon_id: int, data: CouponIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    return coupon_out(db, _save_coupon(db, data, v.id, _own_coupon(db, v, coupon_id)))


@vendor_router.delete("/coupons/{coupon_id}", response_model=MessageResponse)
def deactivate_my_coupon(coupon_id: int, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    _own_coupon(db, v, coupon_id).is_active = False        # keep the row: past orders reference its usage
    db.commit()
    return MessageResponse(message="Coupon deactivated.")


# ------------------------------------------------------------------ vendor: promotions
def _own_promo(db, v, pid):
    p = db.scalar(select(Promotion).where(Promotion.id == pid, Promotion.vendor_id == v.id))
    if p is None:
        raise api_error(404, "not_found", "Promotion not found.")
    return p


def _save_promo(db: Session, v: Vendor, data: PromotionIn, p: Promotion | None = None) -> Promotion:
    if data.menu_item_id is not None and db.scalar(select(MenuItem.id).where(MenuItem.id == data.menu_item_id, MenuItem.vendor_id == v.id)) is None:
        raise api_error(422, "bad_item", "That food item isn't on your menu.")
    p = p or Promotion(vendor_id=v.id)
    for k, val in data.model_dump().items():
        setattr(p, k, _aware(val) if k in ("starts_at", "ends_at") else val)
    db.add(p)
    db.commit()
    return p


@vendor_router.get("/promotions")
def my_promotions(v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    return [promo_out(p) for p in db.scalars(select(Promotion).where(Promotion.vendor_id == v.id).order_by(Promotion.id.desc()))]


@vendor_router.post("/promotions", status_code=201)
def create_promotion(data: PromotionIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    return promo_out(_save_promo(db, v, data))


@vendor_router.put("/promotions/{promo_id}")
def update_promotion(promo_id: int, data: PromotionIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    return promo_out(_save_promo(db, v, data, _own_promo(db, v, promo_id)))


@vendor_router.delete("/promotions/{promo_id}", response_model=MessageResponse)
def delete_promotion(promo_id: int, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    db.delete(_own_promo(db, v, promo_id))
    db.commit()
    return MessageResponse(message="Promotion deleted.")


# ------------------------------------------------------------------ admin
@admin_router.get("/coupons")
def all_coupons(db: Session = Depends(get_db)):
    return [coupon_out(db, c) for c in db.scalars(select(Coupon).order_by(Coupon.id.desc()))]


@admin_router.post("/coupons", status_code=201)
def admin_create_coupon(data: CouponIn, vendor_id: int | None = None, db: Session = Depends(get_db)):
    if vendor_id is not None and db.get(Vendor, vendor_id) is None:
        raise api_error(404, "not_found", "Vendor not found.")
    return coupon_out(db, _save_coupon(db, data, vendor_id))


@admin_router.put("/coupons/{coupon_id}")
def admin_update_coupon(coupon_id: int, data: CouponIn, db: Session = Depends(get_db)):
    c = db.get(Coupon, coupon_id)
    if c is None:
        raise api_error(404, "not_found", "Coupon not found.")
    return coupon_out(db, _save_coupon(db, data, c.vendor_id, c))


@admin_router.delete("/coupons/{coupon_id}", response_model=MessageResponse)
def admin_deactivate_coupon(coupon_id: int, db: Session = Depends(get_db)):
    c = db.get(Coupon, coupon_id)
    if c is None:
        raise api_error(404, "not_found", "Coupon not found.")
    c.is_active = False
    db.commit()
    return MessageResponse(message="Coupon deactivated.")


@admin_router.get("/promotions")
def all_promotions(db: Session = Depends(get_db)):
    return [{**promo_out(p), "vendor_name": db.get(Vendor, p.vendor_id).name} for p in db.scalars(select(Promotion).order_by(Promotion.id.desc()).limit(200))]


@admin_router.patch("/promotions/{promo_id}/active", response_model=MessageResponse)
def admin_toggle_promotion(promo_id: int, is_active: bool, db: Session = Depends(get_db)):
    p = db.get(Promotion, promo_id)
    if p is None:
        raise api_error(404, "not_found", "Promotion not found.")
    p.is_active = is_active
    db.commit()
    return MessageResponse(message="Promotion updated.")
