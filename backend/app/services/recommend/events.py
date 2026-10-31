"""The learning loop: record what we recommended and what customers did with it, so the engine can be measured and improved.
Only for signed-in customers with personalisation on. Deleted when they choose 'forget my history'."""
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.ai import CustomerPreference, RecommendationEvent
from app.models.orders import OrderStatus as S
from app.models.user import User
from app.services import order_events
from app.services.recommend.types import Scored

SURFACES = {"feed", "afford", "craving", "assistant", "group"}
KINDS = {"impression", "click", "add_to_cart", "order"}


def tracking_allowed(db: Session, user: User | None) -> bool:
    if user is None:
        return False
    prefs = db.get(CustomerPreference, user.id)
    return prefs is None or prefs.personalization_enabled


def log_impressions(db: Session, user: User | None, surface: str, shown: list[Scored]) -> None:
    if not shown or not tracking_allowed(db, user):
        return
    for rank, s in enumerate(shown, start=1):
        db.add(RecommendationEvent(user_id=user.id, surface=surface, kind="impression", menu_item_id=s.cand.item.id,
                                   vendor_id=s.cand.vendor.id, rank=rank, score=round(s.score, 4)))
    db.commit()


def log_event(db: Session, user: User | None, surface: str, kind: str, menu_item_id: int, vendor_id: int | None, rank: int | None = None) -> bool:
    if surface not in SURFACES or kind not in KINDS or not tracking_allowed(db, user):
        return False
    db.add(RecommendationEvent(user_id=user.id, surface=surface, kind=kind, menu_item_id=menu_item_id, vendor_id=vendor_id, rank=rank))
    db.commit()
    return True


@order_events.subscribe
def attribute_orders(db, order, old, new, actor_role, background):
    """When an order is placed, credit it to a recommendation the customer saw (or clicked) for that dish in the last day."""
    if old is not None or new != S.PENDING_PAYMENT:
        return
    user = db.get(User, order.customer_id)
    if not tracking_allowed(db, user):
        return
    since = utcnow() - timedelta(days=1)
    for oi in order.items:
        if not oi.menu_item_id:
            continue
        last = db.scalar(select(RecommendationEvent).where(RecommendationEvent.user_id == user.id, RecommendationEvent.menu_item_id == oi.menu_item_id,
                                                           RecommendationEvent.kind != "order", RecommendationEvent.created_at >= since)
                         .order_by(RecommendationEvent.id.desc()).limit(1))
        if last is not None:
            db.add(RecommendationEvent(user_id=user.id, surface=last.surface, kind="order", menu_item_id=oi.menu_item_id,
                                       vendor_id=order.vendor_id, rank=last.rank, score=last.score))


def prune(db: Session) -> int:
    res = db.execute(delete(RecommendationEvent).where(RecommendationEvent.created_at < utcnow() - timedelta(days=get_settings().rec_event_retention_days)))
    db.commit()
    return res.rowcount or 0
