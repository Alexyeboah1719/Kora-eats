"""Vendor onboarding + dashboard APIs (/api/vendor/*)."""
from datetime import time

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import api_error
from app.core.uploads import save_private_document, save_public_image
from app.db.session import get_db
from app.models.catalog import ApprovalStatus, Category, MenuItem, Vendor, VendorDocument, VendorHour
from app.models.user import User, UserRole
from app.schemas.auth import MessageResponse
from app.schemas.vendor import (
    DocumentOut, HourIn, MenuItemIn, MenuItemVendorOut, VendorApplyIn, VendorProfileIn, VendorProfileOut,
)
from app.services import audit_service

router = APIRouter(prefix="/vendor", tags=["vendor"])
DOC_KINDS = {"BUSINESS_REGISTRATION", "FOOD_HANDLER_CERT", "ID_CARD", "OTHER"}
MAX_DOCS = 6


# ----------------------------------------------------------------- dependencies
def owned_vendor(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Vendor:
    """The caller's vendor record in ANY approval state (used during onboarding)."""
    v = db.scalar(select(Vendor).where(Vendor.owner_user_id == user.id))
    if v is None:
        raise api_error(404, "no_application", "You haven't applied to be a vendor yet.")
    return v


def require_vendor(user: User = Depends(get_current_user), v: Vendor = Depends(owned_vendor)) -> Vendor:
    """Approved vendors only: role VENDOR *and* approval APPROVED."""
    if user.role != UserRole.VENDOR or v.approval_status != ApprovalStatus.APPROVED:
        raise api_error(403, "vendor_not_approved", "Your vendor account isn't approved yet.")
    return v


def _out(v: Vendor) -> VendorProfileOut:
    return VendorProfileOut(
        id=v.id, name=v.name, owner_name=v.owner_name, contact_email=v.contact_email, phone=v.phone,
        address_text=v.address_text, latitude=v.latitude, longitude=v.longitude, description=v.description,
        category_id=v.category_id, avg_prep_minutes=v.avg_prep_minutes, logo_url=v.logo_url, cover_url=v.cover_url,
        is_open=v.is_open, cod_enabled=v.cod_enabled, approval_status=v.approval_status.value, rejection_reason=v.rejection_reason,
        delivery_radius_km=v.delivery_radius_km, business_type=v.business_type.value, region=v.region, area=v.area, digital_address=v.digital_address,
        documents=[DocumentOut.model_validate(d) for d in v.documents],
    )


def _check_category(db: Session, category_id: int | None) -> None:
    if category_id is not None and db.get(Category, category_id) is None:
        raise api_error(422, "bad_category", "Unknown category.")


# ----------------------------------------------------------------- onboarding
@router.post("/apply", response_model=VendorProfileOut, status_code=201)
def apply(data: VendorApplyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != UserRole.CUSTOMER:
        raise api_error(403, "wrong_role", "Only customer accounts can apply to become a vendor.")
    _check_category(db, data.category_id)
    v = db.scalar(select(Vendor).where(Vendor.owner_user_id == user.id))
    fields = data.model_dump(exclude={"opens_at", "closes_at"})
    if v is None:
        v = Vendor(owner_user_id=user.id, approval_status=ApprovalStatus.PENDING, **fields)
        db.add(v)
        db.flush()
    elif v.approval_status == ApprovalStatus.REJECTED:       # re-apply after fixing issues
        for k, val in fields.items():
            setattr(v, k, val)
        v.approval_status, v.rejection_reason = ApprovalStatus.PENDING, None
    else:
        raise api_error(409, "already_applied", "You already have a vendor application.")
    v.hours.clear()
    db.flush()
    v.hours.extend(VendorHour(weekday=d, opens_at=data.opens_at, closes_at=data.closes_at) for d in range(7))
    audit_service.record(db, "vendor_applied", user.id, detail=f"vendor={v.id}")
    db.commit()
    return _out(v)


@router.get("/application", response_model=VendorProfileOut)
def my_application(v: Vendor = Depends(owned_vendor)):
    return _out(v)


@router.post("/documents", response_model=DocumentOut, status_code=201)
def upload_document(doc_type: str = Form(...), file: UploadFile = File(...), v: Vendor = Depends(owned_vendor),
                    db: Session = Depends(get_db)):
    if doc_type not in DOC_KINDS:
        raise api_error(422, "bad_doc_type", "Unknown document type.")
    if len(v.documents) >= MAX_DOCS:
        raise api_error(400, "too_many", f"You can upload up to {MAX_DOCS} documents.")
    stored, original = save_private_document(file)
    d = VendorDocument(vendor_id=v.id, doc_type=doc_type, original_name=original, stored_name=stored)
    db.add(d)
    db.commit()
    return d


@router.delete("/documents/{doc_id}", response_model=MessageResponse)
def delete_document(doc_id: int, v: Vendor = Depends(owned_vendor), db: Session = Depends(get_db)):
    d = next((x for x in v.documents if x.id == doc_id), None)
    if d is None:
        raise api_error(404, "not_found", "Document not found.")
    db.delete(d)
    db.commit()
    return MessageResponse(message="Document removed.")


@router.post("/me/logo", response_model=VendorProfileOut)
def upload_logo(file: UploadFile = File(...), v: Vendor = Depends(owned_vendor), db: Session = Depends(get_db)):
    v.logo_url = save_public_image(file)
    db.commit()
    return _out(v)


@router.post("/me/cover", response_model=VendorProfileOut)
def upload_cover(file: UploadFile = File(...), v: Vendor = Depends(owned_vendor), db: Session = Depends(get_db)):
    v.cover_url = save_public_image(file)
    db.commit()
    return _out(v)


# ----------------------------------------------------------------- profile + hours (approved vendors)
@router.get("/me", response_model=VendorProfileOut)
def get_me(v: Vendor = Depends(require_vendor)):
    return _out(v)


@router.put("/me", response_model=VendorProfileOut)
def update_me(data: VendorProfileIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    _check_category(db, data.category_id)
    for k, val in data.model_dump(exclude={"business_type"}).items():     # the seller type is fixed once approved (ask support to change it)
        setattr(v, k, val)
    db.commit()
    return _out(v)


@router.post("/open", response_model=VendorProfileOut)
def set_open(is_open: bool, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    v.is_open = is_open
    db.commit()
    return _out(v)


@router.post("/cod", response_model=VendorProfileOut)
def toggle_cod(cod_enabled: bool, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """Toggle Cash on Delivery for this vendor."""
    v.cod_enabled = cod_enabled
    db.commit()
    return _out(v)


@router.get("/hours", response_model=list[HourIn])
def get_hours(v: Vendor = Depends(require_vendor)):
    return sorted(v.hours, key=lambda h: h.weekday)


@router.put("/hours", response_model=list[HourIn])
def set_hours(hours: list[HourIn], v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    if len({h.weekday for h in hours}) != len(hours):
        raise api_error(422, "duplicate_day", "Each weekday can appear once.")
    v.hours.clear()
    db.flush()
    v.hours.extend(VendorHour(**h.model_dump()) for h in hours)
    db.commit()
    return sorted(v.hours, key=lambda h: h.weekday)


# ----------------------------------------------------------------- menu management
def _own_item(db: Session, v: Vendor, item_id: int) -> MenuItem:
    it = db.scalar(select(MenuItem).where(MenuItem.id == item_id, MenuItem.vendor_id == v.id))
    if it is None:
        raise api_error(404, "not_found", "Menu item not found.")
    return it


@router.get("/menu", response_model=list[MenuItemVendorOut])
def list_menu(v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    return db.scalars(select(MenuItem).where(MenuItem.vendor_id == v.id).order_by(MenuItem.name)).unique().all()


@router.post("/menu", response_model=MenuItemVendorOut, status_code=201)
def add_item(data: MenuItemIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    _check_category(db, data.category_id)
    it = MenuItem(vendor_id=v.id, **data.model_dump())
    db.add(it)
    db.commit()
    return it


@router.put("/menu/{item_id}", response_model=MenuItemVendorOut)
def edit_item(item_id: int, data: MenuItemIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    _check_category(db, data.category_id)
    it = _own_item(db, v, item_id)
    for k, val in data.model_dump().items():
        setattr(it, k, val)
    db.commit()
    return it


@router.patch("/menu/{item_id}/availability", response_model=MenuItemVendorOut)
def set_availability(item_id: int, is_available: bool, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    it = _own_item(db, v, item_id)
    it.is_available = is_available
    db.commit()
    return it


@router.post("/menu/{item_id}/image", response_model=MenuItemVendorOut)
def item_image(item_id: int, file: UploadFile = File(...), v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    it = _own_item(db, v, item_id)
    it.image_url = save_public_image(file)
    db.commit()
    return it


@router.delete("/menu/{item_id}", response_model=MessageResponse)
def delete_item(item_id: int, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    it = _own_item(db, v, item_id)
    db.delete(it)
    db.commit()
    return MessageResponse(message="Menu item deleted.")


@router.get("/analytics")
def analytics(v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """Food sales, orders, popular dishes, busiest hours and repeat customers for the vendor's own restaurant."""
    from app.services import analytics_service
    return analytics_service.vendor_overview(db, v)
