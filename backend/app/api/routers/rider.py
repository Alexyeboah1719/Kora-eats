"""Rider onboarding + delivery APIs (/api/rider/*)."""
from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import api_error
from app.core.uploads import save_private_document
from app.db.session import get_db
from app.models.catalog import ApprovalStatus
from app.models.riders import DeliveryRider, VehicleType
from app.models.user import User, UserRole
from app.schemas.auth import MessageResponse
from app.schemas.rider import RiderApplyIn, RiderUpdateIn
from pydantic import BaseModel, Field

from app.services import audit_service, rider_service as svc, tracking_service

router = APIRouter(prefix="/rider", tags=["rider"])


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


def rider_out(r: DeliveryRider) -> dict:
    return {"id": r.id, "name": r.user.profile.full_name, "email": r.user.email, "phone": r.phone,
            "vehicle_type": r.vehicle_type.value, "plate_number": r.plate_number,
            "approval_status": r.approval_status.value, "rejection_reason": r.rejection_reason,
            "is_online": r.is_online, "has_document": bool(r.id_document), "document_name": r.id_document_name,
            "last_location_at": r.last_location_at.isoformat() if r.last_location_at else None,
            "created_at": r.created_at.isoformat()}


def owned_rider(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DeliveryRider:
    r = db.scalar(select(DeliveryRider).where(DeliveryRider.user_id == user.id))
    if r is None:
        raise api_error(404, "no_application", "You haven't applied to be a rider yet.")
    return r


def require_rider(user: User = Depends(get_current_user), r: DeliveryRider = Depends(owned_rider)) -> DeliveryRider:
    if user.role != UserRole.RIDER or r.approval_status != ApprovalStatus.APPROVED:
        raise api_error(403, "rider_not_approved", "Your rider account isn't approved yet.")
    return r


def _vehicle(v: str) -> VehicleType:
    try:
        return VehicleType(v)
    except ValueError:
        raise api_error(422, "bad_vehicle", "Choose MOTORBIKE, BICYCLE, CAR or ON_FOOT.")


# ------------------------------------------------------------------ onboarding
@router.post("/apply", status_code=201)
def apply(data: RiderApplyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != UserRole.CUSTOMER:
        raise api_error(403, "wrong_role", "Only customer accounts can apply to become a rider.")
    r = db.scalar(select(DeliveryRider).where(DeliveryRider.user_id == user.id))
    if r is None:
        r = DeliveryRider(user_id=user.id, phone=data.phone, vehicle_type=_vehicle(data.vehicle_type), plate_number=data.plate_number)
        db.add(r)
    elif r.approval_status == ApprovalStatus.REJECTED:
        r.phone, r.vehicle_type, r.plate_number = data.phone, _vehicle(data.vehicle_type), data.plate_number
        r.approval_status, r.rejection_reason = ApprovalStatus.PENDING, None
    else:
        raise api_error(409, "already_applied", "You already have a rider application.")
    audit_service.record(db, "rider_applied", user.id)
    db.commit()
    return rider_out(r)


@router.get("/application")
def application(r: DeliveryRider = Depends(owned_rider)):
    return rider_out(r)


@router.post("/id-document")
def upload_id(file: UploadFile = File(...), r: DeliveryRider = Depends(owned_rider), db: Session = Depends(get_db)):
    r.id_document, r.id_document_name = save_private_document(file)
    db.commit()
    return rider_out(r)


# ------------------------------------------------------------------ profile / status
@router.get("/me")
def me(r: DeliveryRider = Depends(require_rider)):
    return rider_out(r)


@router.put("/me")
def update_me(data: RiderUpdateIn, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    r.phone, r.vehicle_type, r.plate_number = data.phone, _vehicle(data.vehicle_type), data.plate_number
    db.commit()
    return rider_out(r)


@router.post("/online")
def set_online(is_online: bool, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    if not is_online and svc._active_count(db, r):
        raise api_error(409, "active_deliveries", "Finish your active deliveries before going offline.")
    r.is_online = is_online
    db.commit()
    return rider_out(r)


# ------------------------------------------------------------------ deliveries
@router.get("/available")
def available(r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.available(db, r) if r.is_online else []


@router.get("/deliveries")
def deliveries(view: str = "active", r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.my_deliveries(db, r, view)


@router.post("/deliveries/{order_id}/accept")
def accept(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.accept(db, r, order_id, background)


@router.post("/deliveries/{order_id}/pickup")
def pickup(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.pickup(db, r, order_id, background)


@router.post("/deliveries/{order_id}/out-for-delivery")
def out_for_delivery(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.out_for_delivery(db, r, order_id, background)


class WaitIn(BaseModel):
    wait_minutes: int | None = Field(default=None, ge=0, le=5)


@router.post("/deliveries/{order_id}/arrived")
def arrived(order_id: int, background: BackgroundTasks, data: WaitIn | None = None,
            r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.arrived(db, r, order_id, data.wait_minutes if data else None, background)


@router.post("/deliveries/{order_id}/delivered")
def delivered(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.deliver(db, r, order_id, background)


class ReportIn(BaseModel):
    outcome: str
    message: str = Field(min_length=5, max_length=1000)


class ReturnIn(BaseModel):
    reason: str | None = None
    note: str | None = Field(default=None, max_length=300)


@router.post("/deliveries/{order_id}/return")
def return_order(order_id: int, background: BackgroundTasks, data: ReturnIn | None = None,
                 r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Rider brings the food back to the vendor (customer refused COD, never showed up...)."""
    return svc.return_to_vendor(db, r, order_id, data.reason if data else None, data.note if data else None, background)


@router.post("/deliveries/{order_id}/returned")
def returned(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Rider says the food is physically back with the vendor; the vendor confirms receipt separately."""
    return svc.handed_back(db, r, order_id, background)


@router.post("/deliveries/{order_id}/report")
def report(order_id: int, data: ReportIn, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Rider reports a problem at the door (not paid, customer absent, wrong address...). Goes to admin support."""
    return svc.report_issue(db, r, order_id, data.outcome, data.message.strip())


@router.post("/deliveries/{order_id}/release", response_model=MessageResponse)
def release(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    svc.release(db, r, order_id, background)
    return MessageResponse(message="Delivery released.")


@router.get("/earnings")
def earnings(r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.earnings(db, r)


@router.post("/location")
def update_location(data: LocationIn, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider),
                    db: Session = Depends(get_db)):
    """Called every few seconds by the rider's phone while online."""
    if not r.is_online:
        raise api_error(409, "offline", "Go online before sharing your location.")
    return {"stored": tracking_service.record_location(db, r, data.lat, data.lng, background)}
