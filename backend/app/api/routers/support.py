from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import api_error
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.admin import SupportTicket
from app.models.orders import Order
from app.models.user import User

router = APIRouter(prefix="/support", tags=["support"])


class TicketIn(BaseModel):
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=3000)]
    order_id: int | None = None


def _out(t: SupportTicket) -> dict:
    return {"id": t.id, "subject": t.subject, "message": t.message, "status": t.status.value, "admin_reply": t.admin_reply,
            "created_at": t.created_at.isoformat(), "replied_at": t.replied_at.isoformat() if t.replied_at else None, "order_id": t.order_id}


@router.post("/tickets", status_code=201, dependencies=[Depends(rate_limit("support", 10, 3600))])
def create(data: TicketIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.order_id and db.scalar(select(Order.id).where(Order.id == data.order_id, Order.customer_id == user.id)) is None:
        raise api_error(404, "not_found", "Order not found.")      # you can only raise tickets about your own orders
    t = SupportTicket(user_id=user.id, **data.model_dump())
    db.add(t); db.commit()
    return _out(t)


@router.get("/tickets")
def mine(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_out(t) for t in db.scalars(select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(SupportTicket.id.desc()))]
