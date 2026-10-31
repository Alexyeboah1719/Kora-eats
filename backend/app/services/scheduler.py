"""Background processing: releases scheduled orders to vendors, cancels abandoned unpaid orders,
sends review reminders and prunes old GPS points. Runs every minute inside the web process.

With several server processes only ONE runs each cycle (PostgreSQL advisory lock), so nothing is done twice.
For heavy workloads move these functions to a dedicated worker (Celery/RQ/APScheduler) unchanged."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.db.session import SessionLocal, engine
from app.models.orders import Order, OrderStatus as S
from app.services import notification_service, order_service, payment_service, review_service, tracking_service
from app.services import payout_service
from app.services.recommend import events as rec_events

logger = logging.getLogger(__name__)
LOCK_ID = 727001
RELEASE_LEAD_MINUTES = 30      # vendors see a scheduled order this long before they must start cooking


def release_scheduled_orders(db: Session) -> int:
    """Paid scheduled orders become visible to the vendor shortly before they need to start preparing."""
    now = utcnow()
    rows = db.scalars(select(Order).where(Order.scheduled_for.is_not(None), Order.released_at.is_(None),
                                          Order.status.in_([S.PAID, S.CONFIRMED]))).unique().all()
    n = 0
    for o in rows:
        if now >= o.scheduled_for - timedelta(minutes=o.vendor.avg_prep_minutes + RELEASE_LEAD_MINUTES):
            o.released_at = now
            if o.vendor.owner_user_id:
                notification_service.notify(db, o.vendor.owner_user_id, "scheduled_order_due", "Scheduled order due soon",
                                            f"Order {o.code} is for {o.scheduled_for:%d %b %H:%M}. Start preparing soon.",
                                            "/vendor/dashboard.html#orders")
            n += 1
    db.commit()
    return n


def expire_unpaid_orders(db: Session) -> int:
    """An order nobody paid for must not sit forever. Re-check with the provider first (the customer may
    have paid and closed the page), then cancel what is still unpaid."""
    cutoff = utcnow() - timedelta(minutes=get_settings().order_payment_timeout_minutes)
    ids = db.scalars(select(Order.id).where(Order.status.in_([S.PENDING_PAYMENT, S.PAYMENT_FAILED]), Order.created_at < cutoff).limit(100)).all()
    cancelled = 0
    for oid in ids:
        try:
            payment_service.verify_order(db, oid)
            o = order_service.lock_order(db, oid)
            if o.status in (S.PENDING_PAYMENT, S.PAYMENT_FAILED):
                order_service.change_status(db, o, S.CANCELLED, "system", None, "Payment timed out")
                cancelled += 1
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not expire order %s", oid)
    return cancelled


def run_cycle(db: Session) -> dict:
    out = {}
    for name, fn in (("released", release_scheduled_orders), ("expired", expire_unpaid_orders),
                     ("reminders", review_service.send_due_reminders), ("pruned", tracking_service.prune_old_tracking),
                     ("rec_events_pruned", rec_events.prune), ("withdrawals_reconciled", payout_service.reconcile_processing)):
        try:
            out[name] = fn(db)
        except Exception:
            db.rollback()
            logger.exception("Background job '%s' failed", name)
    return out


def _run_once() -> None:
    with engine.connect() as lock_conn:                       # a dedicated connection holds the advisory lock
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:i)"), {"i": LOCK_ID}).scalar():
            return                                            # another worker is already running this cycle
        try:
            with SessionLocal() as db:
                result = run_cycle(db)
            if any(result.values()):
                logger.info("Background cycle: %s", result)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:i)"), {"i": LOCK_ID})
            lock_conn.commit()


async def loop(interval_seconds: int = 60) -> None:
    while True:
        try:
            await asyncio.to_thread(_run_once)
        except Exception:
            logger.exception("Background loop error")
        await asyncio.sleep(interval_seconds)
