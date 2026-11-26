import hmac

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from app.core.rate_limit import rate_limit
from app.core.timeutil import utcnow
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.routers.vendor import require_vendor
from app.core.errors import api_error
from app.db.session import get_db
from app.models.catalog import Vendor
from app.models.orders import FulfillmentType, Order, OrderStatus as S
from app.models.riders import Delivery
from app.models.user import User
from app.api.deps import get_current_user
from app.schemas.orders import VendorStatusIn
from app.services import order_service as svc
from app.services import return_service

router = APIRouter(prefix="/vendor/orders", tags=["vendor-orders"])
ACTIVE = [S.PAID, S.CONFIRMED, S.PLACED, S.PREPARING, S.READY_FOR_PICKUP, S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY, S.RIDER_ARRIVED, S.RETURNED, S.ACCEPTED_RETURN]
HISTORY = [S.DELIVERED, S.COLLECTED, S.CANCELLED, S.REFUNDED, S.RETURN_DECLINED, S.RETURNED_TO_VENDOR]


def visible_orders_filter(q):
    """Scheduled ('deliver later') orders appear only once the scheduler has released them."""
    return q.where(or_(Order.scheduled_for.is_(None), Order.released_at.is_not(None)))


@router.get("")
def list_orders(view: str = "active", v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    statuses = HISTORY if view == "history" else ACTIVE
    q = select(Order).where(Order.vendor_id == v.id, Order.status.in_(statuses))
    q = visible_orders_filter(q).order_by(Order.id.desc()).limit(100)
    return [svc.order_out(db, o, for_vendor=True) for o in db.scalars(q).unique()]


@router.post("/{order_id}/status")
def set_status(order_id: int, data: VendorStatusIn, background: BackgroundTasks, user: User = Depends(get_current_user),
               v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    o = svc.lock_order(db, order_id)
    if o is None or o.vendor_id != v.id:
        raise api_error(404, "not_found", "Order not found.")
    if o.scheduled_for and not o.released_at:
        raise api_error(409, "not_released", "This is a scheduled order. It will open for you closer to the delivery time.")
    new = S(data.status)
    if new == S.CANCELLED and not data.note:
        raise api_error(422, "reason_required", "Tell the customer why you're rejecting the order.")
    svc.change_status(db, o, new, "vendor", user.id, data.note, background)
    if new == S.READY_FOR_PICKUP and o.fulfillment_type == FulfillmentType.DELIVERY:
        from app.services import dispatch_service
        dispatch_service.auto_assign(db, o, background)      # Bolt-style: hand it to the nearest online rider now
    db.commit()
    return svc.order_out(db, o, for_vendor=True)


def _return_order(db: Session, v: Vendor, order_id: int) -> Order:
    o = svc.lock_order(db, order_id)
    if o is None or o.vendor_id != v.id:
        raise api_error(404, "not_found", "Order not found.")
    if o.scheduled_for and not o.released_at:
        raise api_error(409, "not_released", "This is a scheduled order. It will open for you closer to the delivery time.")
    return o


class ReturnDeclineIn(BaseModel):
    note: str = Field(min_length=3, max_length=300)


@router.post("/{order_id}/return/accept")
def accept_return(order_id: int, background: BackgroundTasks, user: User = Depends(get_current_user),
                  v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    o = _return_order(db, v, order_id)
    svc.change_status(db, o, S.ACCEPTED_RETURN, "vendor", user.id, "Vendor accepted the return", background)
    db.commit()
    return svc.order_out(db, o, for_vendor=True)


@router.post("/{order_id}/return/decline")
def decline_return(order_id: int, data: ReturnDeclineIn, background: BackgroundTasks, user: User = Depends(get_current_user),
                   v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    o = _return_order(db, v, order_id)
    svc.change_status(db, o, S.RETURN_DECLINED, "vendor", user.id, data.note, background)
    return_service.settle(db, o, background)
    db.commit()
    return svc.order_out(db, o, for_vendor=True)


@router.post("/{order_id}/return/confirm")
def confirm_return(order_id: int, background: BackgroundTasks, user: User = Depends(get_current_user),
                   v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """The vendor confirms they physically have the food. The order closes as RETURNED_TO_VENDOR and is not resold."""
    o = _return_order(db, v, order_id)
    svc.change_status(db, o, S.RETURNED_TO_VENDOR, "vendor", user.id, "Vendor confirmed receipt", background)
    d = db.scalar(select(Delivery).where(Delivery.order_id == o.id))
    if d is not None and d.returned_at is None:
        d.returned_at = utcnow()
    return_service.settle(db, o, background)
    db.commit()
    return svc.order_out(db, o, for_vendor=True)


class CollectIn(BaseModel):
    code: str = Field(min_length=4, max_length=8)


@router.post("/{order_id}/collect", dependencies=[Depends(rate_limit("collect", 30, 300))])
def collect(order_id: int, data: CollectIn, background: BackgroundTasks, user: User = Depends(get_current_user),
            v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """Hand a PICKUP order over. The customer shows their pickup code; the vendor types it in here. The vendor
    never sees the code in advance, so entering it proves the right person is collecting the food."""
    o = svc.lock_order(db, order_id)
    if o is None or o.vendor_id != v.id or o.fulfillment_type != FulfillmentType.PICKUP:
        raise api_error(404, "not_found", "Pickup order not found.")
    if o.status != S.READY_FOR_PICKUP:
        raise api_error(409, "not_ready", "This order isn't ready for collection yet.")
    if not hmac.compare_digest((o.pickup_code or "").upper(), data.code.strip().upper()):
        raise api_error(422, "wrong_code", "That pickup code doesn't match. Ask the customer to check their order page.")
    svc.change_status(db, o, S.COLLECTED, "vendor", user.id, "Collected by customer", background)
    db.commit()
    return svc.order_out(db, o, for_vendor=True)
