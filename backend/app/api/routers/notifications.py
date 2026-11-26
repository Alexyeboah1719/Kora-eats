from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.errors import api_error
from app.db.session import get_db
from app.models.notifications import Notification, PushSubscription
from app.models.user import User
from app.schemas.auth import MessageResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=5, max_length=100)


class PushIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000, pattern=r"^https://")
    keys: PushKeys


def _out(n: Notification) -> dict:
    return {"id": n.id, "type": n.type, "title": n.title, "body": n.body, "url": n.url, "is_read": n.is_read,
            "created_at": n.created_at.isoformat()}


@router.get("")
def list_notifications(unread_only: bool = False, limit: int = 50, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    return [_out(n) for n in db.scalars(q.order_by(Notification.id.desc()).limit(min(limit, 100)))]


@router.get("/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"count": db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.is_read.is_(False)))}


@router.post("/read-all", response_model=MessageResponse)
def read_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False)).values(is_read=True))
    db.commit()
    return MessageResponse(message="All notifications marked as read.")


@router.post("/{notification_id}/read", response_model=MessageResponse)
def mark_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if n is None:
        raise api_error(404, "not_found", "Notification not found.")
    n.is_read = True
    db.commit()
    return MessageResponse(message="Marked as read.")


@router.get("/push/public-key")
def push_public_key():
    """The VAPID *public* key the browser needs to subscribe. None = push isn't configured on this server."""
    return {"public_key": get_settings().vapid_public_key or None}


@router.post("/push/subscribe", response_model=MessageResponse)
def subscribe(data: PushIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == data.endpoint))
    if sub is None:
        db.add(PushSubscription(user_id=user.id, endpoint=data.endpoint, p256dh=data.keys.p256dh, auth=data.keys.auth))
    else:   # same device, possibly a different account now
        sub.user_id, sub.p256dh, sub.auth = user.id, data.keys.p256dh, data.keys.auth
    db.commit()
    return MessageResponse(message="Push notifications enabled.")


@router.post("/push/unsubscribe", response_model=MessageResponse)
def unsubscribe(endpoint: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.id))
    if sub:
        db.delete(sub)
        db.commit()
    return MessageResponse(message="Push notifications disabled.")
