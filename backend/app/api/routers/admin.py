"""Admin APIs (/api/admin/*). Every route here requires the ADMIN role."""
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.core.uploads import private_dir
from app.db.session import get_db
from app.models.catalog import ApprovalStatus, Vendor
from app.models.user import User, UserRole
from app.schemas.auth import MessageResponse
from app.schemas.vendor import RejectIn
from app.services import audit_service, email_service, email_templates, notification_service, vendor_policy

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_roles(UserRole.ADMIN))])


def _vendor_row(v: Vendor) -> dict:
    return {"id": v.id, "name": v.name, "owner_name": v.owner_name, "contact_email": v.contact_email, "phone": v.phone,
            "address_text": v.address_text, "business_type": v.business_type.value, "area": v.area, "region": v.region, "missing_documents": vendor_policy.missing_documents(v), "status": v.approval_status.value, "rejection_reason": v.rejection_reason,
            "created_at": v.created_at.isoformat(), "documents": [{"id": d.id, "doc_type": d.doc_type, "original_name": d.original_name} for d in v.documents]}


@router.get("/vendors")
def list_vendors(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Vendor).order_by(Vendor.created_at.desc())
    if status:
        try:
            q = q.where(Vendor.approval_status == ApprovalStatus(status))
        except ValueError:
            raise api_error(422, "bad_status", "Unknown status.")
    return [_vendor_row(v) for v in db.scalars(q).unique()]


def _get(db: Session, vendor_id: int) -> Vendor:
    v = db.get(Vendor, vendor_id)
    if v is None:
        raise api_error(404, "not_found", "Vendor not found.")
    return v


@router.get("/vendors/{vendor_id}/documents/{doc_id}")
def download_document(vendor_id: int, doc_id: int, db: Session = Depends(get_db)):
    v = _get(db, vendor_id)
    d = next((x for x in v.documents if x.id == doc_id), None)
    if d is None or not (private_dir() / d.stored_name).is_file():
        raise api_error(404, "not_found", "Document not found.")
    return FileResponse(private_dir() / d.stored_name, filename=d.original_name)


@router.post("/vendors/{vendor_id}/approve", response_model=MessageResponse)
def approve(vendor_id: int, background: BackgroundTasks, force: bool = False, admin: User = Depends(require_roles(UserRole.ADMIN)),
            db: Session = Depends(get_db)):
    v = _get(db, vendor_id)
    if v.approval_status == ApprovalStatus.APPROVED:
        raise api_error(409, "already_approved", "Vendor is already approved.")
    if not v.documents:
        raise api_error(400, "no_documents", "The vendor hasn't uploaded any business documents.")
    missing = vendor_policy.missing_documents(v)
    if missing and not force:       # requirements depend on the business type (home cooks need less than restaurants)
        raise api_error(400, "missing_documents", "Still needed for a " + v.business_type.value.replace("_", " ").lower() + ": " + "; ".join(missing) + ".")
    if missing:
        audit_service.record(db, "vendor_approved_with_missing_docs", admin.id, detail=f"vendor={v.id}: {'; '.join(missing)}"[:255])
    owner = db.get(User, v.owner_user_id)
    v.approval_status, v.rejection_reason, v.approved_at = ApprovalStatus.APPROVED, None, utcnow()
    owner.role = UserRole.VENDOR
    audit_service.record(db, "vendor_approved", admin.id, detail=f"vendor={v.id}")
    notification_service.notify(db, owner.id, "vendor_approved", "You're approved!", f"{v.name} is now live on KORA EATS.",
                                "/vendor/dashboard.html", background=background)
    db.commit()
    subject, html = email_templates.vendor_approved_email(owner.profile.full_name, v.name)
    background.add_task(email_service.send_email, v.contact_email or owner.email, subject, html)
    return MessageResponse(message=f"{v.name} approved.")


@router.post("/vendors/{vendor_id}/reject", response_model=MessageResponse)
def reject(vendor_id: int, data: RejectIn, background: BackgroundTasks,
           admin: User = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    v = _get(db, vendor_id)
    owner = db.get(User, v.owner_user_id)
    v.approval_status, v.rejection_reason = ApprovalStatus.REJECTED, data.reason
    if owner.role == UserRole.VENDOR:        # revoke access if previously approved
        owner.role = UserRole.CUSTOMER
    audit_service.record(db, "vendor_rejected", admin.id, detail=f"vendor={v.id}")
    notification_service.notify(db, owner.id, "vendor_rejected", "Vendor application update", data.reason[:200],
                                "/vendor-apply.html", background=background)
    db.commit()
    subject, html = email_templates.vendor_rejected_email(owner.profile.full_name, v.name, data.reason)
    background.add_task(email_service.send_email, v.contact_email or owner.email, subject, html)
    return MessageResponse(message=f"{v.name} rejected.")
