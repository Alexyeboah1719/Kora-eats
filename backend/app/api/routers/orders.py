from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import api_error
from app.db.session import get_db
from app.models.orders import Order, OrderStatus
from app.models.user import User
from app.schemas.orders import CancelIn, CheckoutIn
from app.services import order_service as svc, tracking_service

router = APIRouter(prefix="/orders", tags=["orders"])


def _mine(db: Session, user: User, order_id: int) -> Order:
    o = db.scalar(select(Order).where(Order.id == order_id, Order.customer_id == user.id))
    if o is None:
        raise api_error(404, "not_found", "Order not found.")
    return o


@router.post("", status_code=201)
def checkout(data: CheckoutIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Turns the cart into an order awaiting payment. Prices are recomputed here on the server."""
    return svc.order_out(db, svc.create_order(db, user, data))


@router.get("")
def list_orders(user: User = Depends(get_current_user), db: Session = Depends(get_db), limit: int = 50):
    rows = db.scalars(select(Order).where(Order.customer_id == user.id).order_by(Order.id.desc()).limit(min(limit, 100))).unique()
    return [svc.order_out(db, o) for o in rows]


@router.get("/{order_id}")
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.order_out(db, _mine(db, user, order_id))


@router.get("/{order_id}/tracking")
def tracking(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Live position of the rider for the customer's own order. ETA is an estimate."""
    return tracking_service.tracking_view(db, _mine(db, user, order_id))


@router.post("/{order_id}/cancel")
def cancel(order_id: int, data: CancelIn, background: BackgroundTasks, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Customers can cancel only before paying. After payment, the vendor or support handles it."""
    _mine(db, user, order_id)
    o = svc.lock_order(db, order_id)
    if o.status not in (OrderStatus.PENDING_PAYMENT, OrderStatus.PAYMENT_FAILED):
        raise api_error(409, "cannot_cancel", "This order has been paid. Contact support to cancel it.")
    svc.change_status(db, o, OrderStatus.CANCELLED, "customer", user.id, data.reason or "Cancelled by customer", background)
    db.commit()
    return svc.order_out(db, o)
