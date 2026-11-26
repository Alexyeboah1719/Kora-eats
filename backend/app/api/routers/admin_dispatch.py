from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.db.session import get_db
from app.models.riders import DeliveryRider
from app.models.user import User, UserRole
from app.services import audit_service, dispatch_service as dsp, notification_service, rider_service

router = APIRouter(prefix="/admin/dispatch", tags=["admin"], dependencies=[Depends(require_roles(UserRole.ADMIN))])


@router.get("")
def board(db: Session = Depends(get_db)):
    """Orders waiting for a rider, with the AI's recommended riders for each and how long each has been waiting."""
    now = utcnow()
    online = dsp.eligible_riders(db)
    out = []
    for o in dsp.waiting_orders(db):
        out.append({"order": {"id": o.id, "code": o.code, "vendor": o.vendor.name, "vendor_address": o.vendor.address_text, "address": o.delivery_address,
                              "total": float(o.total), "earning": float(rider_service.earning_for(o)), "trip_km": round(dsp.haversine_km(o.vendor.latitude, o.vendor.longitude, o.delivery_lat, o.delivery_lng), 2)},
                    "priority": dsp.priority(o, now), "recommended": [dsp.plan_out(p) for p in dsp.recommend(db, o, limit=3, now=now)]})
    out.sort(key=lambda x: (not x["priority"]["urgent"], -x["priority"]["waiting_minutes"]))
    return {"orders": out, "online_riders": [{"id": r.id, "name": r.user.profile.full_name, "vehicle": r.vehicle_type.value, "has_location": r.current_lat is not None} for r in online]}


class AssignIn(BaseModel):
    rider_id: int
    force: bool = False


@router.post("/{order_id}/assign")
def assign(order_id: int, data: AssignIn, background: BackgroundTasks, admin: User = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    rider = db.get(DeliveryRider, data.rider_id)
    if rider is None:
        raise api_error(404, "not_found", "Rider not found.")
    o = dsp.assign_by_admin(db, admin.id, order_id, rider, data.force, background)
    audit_service.record(db, "dispatch_assigned", admin.id, detail=f"order={o.code} rider={rider.id}{' (forced)' if data.force else ''}")
    notification_service.notify(db, rider.user_id, "delivery_assigned", "Delivery assigned to you", f"Pickup at {o.vendor.name}. Open your dashboard for details.",
                                "/rider/dashboard.html", background=background)
    db.commit()
    return {"order_id": o.id, "code": o.code, "status": o.status.value, "rider": rider.user.profile.full_name}
