from datetime import timedelta

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.models.catalog import Vendor
from app.models.orders import Order, OrderStatus as S
from app.models.reviews import Review
from app.models.user import User
from app.services import notification_service, order_service, sentiment
from app.services import email_templates

REVIEW_WINDOW_DAYS = 14


def recompute_vendor_rating(db: Session, vendor_id: int) -> None:
    db.flush()   # make sure pending changes (e.g. a hidden review) are counted
    avg, count = db.execute(select(func.avg(Review.vendor_rating), func.count()).where(
        Review.vendor_id == vendor_id, Review.is_hidden.is_(False))).one()
    v = db.get(Vendor, vendor_id)
    v.rating_avg, v.rating_count = float(avg or 0), int(count or 0)


def create(db: Session, user: User, order_id: int, food: int, vendor: int, comment: str | None) -> Review:
    o = db.scalar(select(Order).where(Order.id == order_id, Order.customer_id == user.id))
    if o is None:
        raise api_error(404, "not_found", "Order not found.")
    if o.status not in order_service.COMPLETED:
        raise api_error(409, "not_delivered", "You can review an order after you've received it.")
    if o.delivered_at and utcnow() - o.delivered_at > timedelta(days=REVIEW_WINDOW_DAYS):
        raise api_error(409, "review_window_closed", f"Reviews close {REVIEW_WINDOW_DAYS} days after delivery.")
    if db.scalar(select(Review.id).where(Review.order_id == o.id)):
        raise api_error(409, "already_reviewed", "You've already reviewed this order.")
    r = Review(order_id=o.id, customer_id=user.id, vendor_id=o.vendor_id, food_rating=food, vendor_rating=vendor,
               comment=(comment or None))
    analyse(r)
    db.add(r)
    db.flush()
    recompute_vendor_rating(db, o.vendor_id)
    db.commit()
    return r


def analyse(review: Review) -> None:
    """Fills sentiment + aspects (food, delivery, packaging, price, service) using the rule-based analyser."""
    res = sentiment.analyse(review.comment, review.food_rating, review.vendor_rating)
    review.sentiment, review.sentiment_score, review.aspects = res["sentiment"], res["score"], res["aspects"]


def review_out(r: Review, with_customer: bool = True) -> dict:
    name = r.customer.profile.full_name.split()
    shown = f"{name[0]} {name[-1][0]}." if len(name) > 1 else name[0]
    return {"id": r.id, "food_rating": r.food_rating, "vendor_rating": r.vendor_rating, "comment": r.comment,
            "image_url": r.image_url, "created_at": r.created_at.isoformat(), "sentiment": r.sentiment,
            "aspects": r.aspects, **({"customer": shown} if with_customer else {})}


def send_due_reminders(db: Session, background: BackgroundTasks | None = None) -> int:
    """Run by the scheduler: nudge customers who haven't reviewed an order delivered a while ago."""
    s = get_settings()
    now = utcnow()
    due = db.scalars(select(Order).where(
        Order.status.in_(order_service.COMPLETED), Order.review_reminded_at.is_(None), Order.delivered_at <= now - timedelta(hours=s.review_reminder_hours),
        Order.delivered_at >= now - timedelta(days=REVIEW_WINDOW_DAYS),
        ~select(Review.id).where(Review.order_id == Order.id).exists()).limit(200)).unique().all()
    for o in due:
        o.review_reminded_at = now
        cust = db.get(User, o.customer_id)
        title = f"How was {o.vendor.name}?"
        notification_service.notify(db, cust.id, "review_reminder", title, "Tell others about your order. It takes 20 seconds.",
                                    f"/order.html?id={o.id}", email=email_templates.notification_email(
                                        title, "Your feedback helps others choose and helps vendors improve.", f"/order.html?id={o.id}", "Leave a review"),
                                    email_to=cust.email, background=background)
    db.commit()
    return len(due)


@order_service.register_serializer
def _review_info(db: Session, o: Order) -> dict:
    r = db.scalar(select(Review).where(Review.order_id == o.id))
    can = (r is None and o.status in order_service.COMPLETED and o.delivered_at is not None
           and utcnow() - o.delivered_at <= timedelta(days=REVIEW_WINDOW_DAYS))
    return {"review": review_out(r, False) if r else None, "can_review": can}
