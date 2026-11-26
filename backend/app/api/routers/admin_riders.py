from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.routers.rider import rider_out
from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.core.uploads import private_dir
from app.db.session import get_db
from app.models.catalog import ApprovalStatus
from app.models.riders import DeliveryRider
from app.models.user import User, UserRole
from app.schemas.auth import MessageResponse
from app.schemas.vendor import RejectIn
from app.services import audit_service, email_service, email_templates, notification_service

router = APIRouter(prefix="/admin/riders", tags=["admin"], dependencies=[Depends(require_roles(UserRole.ADMIN))])


def _get(db: Session, rider_id: int) -> DeliveryRider:
    r = db.get(DeliveryRider, rider_id)
    if r is None:
        raise api_error(404, "not_found", "Rider not found.")
    return r


@router.get("")
def list_riders(status: str | None = None, db: Session = Depends(get_db)):
    q = select(DeliveryRider).order_by(DeliveryRider.created_at.desc())
    if status:
        try:
            q = q.where(DeliveryRider.approval_status == ApprovalStatus(status))
        except ValueError:
            raise api_error(422, "bad_status", "Unknown status.")
    return [rider_out(r) for r in db.scalars(q).unique()]


@router.get("/{rider_id}/document")
def document(rider_id: int, db: Session = Depends(get_db)):
    r = _get(db, rider_id)
    if not r.id_document or not (private_dir() / r.id_document).is_file():
        raise api_error(404, "not_found", "No document uploaded.")
    return FileResponse(private_dir() / r.id_document, filename=r.id_document_name or "id-document")


@router.post("/{rider_id}/approve", response_model=MessageResponse)
def approve(rider_id: int, background: BackgroundTasks, admin: User = Depends(require_roles(UserRole.ADMIN)),
            db: Session = Depends(get_db)):
    r = _get(db, rider_id)
    if r.approval_status == ApprovalStatus.APPROVED:
        raise api_error(409, "already_approved", "Rider is already approved.")
    if not r.id_document:
        raise api_error(400, "no_documents", "The rider hasn't uploaded an ID document.")
    r.approval_status, r.rejection_reason, r.approved_at = ApprovalStatus.APPROVED, None, utcnow()
    r.user.role = UserRole.RIDER
    audit_service.record(db, "rider_approved", admin.id, detail=f"rider={r.id}")
    notification_service.notify(db, r.user_id, "rider_approved", "You're approved!", "You can now go online and take deliveries.",
                                "/rider/dashboard.html", background=background)
    db.commit()
    subject, html = email_templates.rider_approved_email(r.user.profile.full_name)
    background.add_task(email_service.send_email, r.user.email, subject, html)
    return MessageResponse(message="Rider approved.")


@router.post("/{rider_id}/reject", response_model=MessageResponse)
def reject(rider_id: int, data: RejectIn, background: BackgroundTasks, admin: User = Depends(require_roles(UserRole.ADMIN)),
           db: Session = Depends(get_db)):
    r = _get(db, rider_id)
    r.approval_status, r.rejection_reason, r.is_online = ApprovalStatus.REJECTED, data.reason, False
    if r.user.role == UserRole.RIDER:
        r.user.role = UserRole.CUSTOMER
    audit_service.record(db, "rider_rejected", admin.id, detail=f"rider={r.id}")
    notification_service.notify(db, r.user_id, "rider_rejected", "Rider application update", data.reason[:200],
                                "/rider-apply.html", background=background)
    db.commit()
    subject, html = email_templates.rider_rejected_email(r.user.profile.full_name, data.reason)
    background.add_task(email_service.send_email, r.user.email, subject, html)
    return MessageResponse(message="Rider rejected.")
