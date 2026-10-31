"""One function - notify() - for every kind of notification: in-app row, optional email, optional web push."""
import json
import logging

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.notifications import Notification, PushSubscription
from app.services import email_service

logger = logging.getLogger(__name__)


def _run(background: BackgroundTasks | None, fn, *args) -> None:
    """Background task when we have a request; otherwise run inline (used by the scheduler)."""
    if background is not None:
        background.add_task(fn, *args)
    else:
        fn(*args)


def _send_push(subs: list[dict], payload: dict) -> None:
    """Sends a Web Push message to each subscription. DEVELOPMENT PLACEHOLDER when VAPID keys are
    missing: the message is logged instead. Production needs VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY
    (generate with: python -m scripts.generate_vapid)."""
    s = get_settings()
    if not s.vapid_private_key:
        logger.warning("[DEV PUSH - not sent] %s -> %d device(s)", payload.get("title"), len(subs))
        return
    from pywebpush import WebPushException, webpush
    dead: list[str] = []
    for sub in subs:
        try:
            webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                    data=json.dumps(payload), vapid_private_key=s.vapid_private_key, vapid_claims={"sub": s.vapid_subject}, ttl=3600)
        except WebPushException as e:
            if getattr(e.response, "status_code", None) in (404, 410):   # browser unsubscribed: forget it
                dead.append(sub["endpoint"])
            else:
                logger.warning("Web push failed: %s", e)
        except Exception:
            logger.exception("Web push error")
    if dead:
        with SessionLocal() as db:
            for ep in dead:
                row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == ep))
                if row:
                    db.delete(row)
            db.commit()


def notify(db: Session, user_id, type_: str, title: str, body: str, url: str | None = None,
           email: tuple[str, str] | None = None, email_to: str | None = None, push: bool = True,
           background: BackgroundTasks | None = None) -> Notification:
    """Creates the in-app notification now (inside the caller's transaction) and queues email + push."""
    n = Notification(user_id=user_id, type=type_, title=title[:120], body=body[:300], url=url)
    db.add(n)
    if email and email_to:
        _run(background, email_service.send_email, email_to, email[0], email[1])
    if push:
        subs = [{"endpoint": x.endpoint, "p256dh": x.p256dh, "auth": x.auth}
                for x in db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id))]
        if subs:
            _run(background, _send_push, subs, {"title": title, "body": body, "url": url or "/", "type": type_})
    return n
