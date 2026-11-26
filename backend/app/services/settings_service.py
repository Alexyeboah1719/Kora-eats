"""Operational settings an admin can change without redeploying. Values are validated against fixed
ranges, saved in the database, and applied on top of the environment settings. Every web worker reloads
them every minute, so a change reaches all of them."""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import api_error
from app.db.session import SessionLocal
from app.models.admin import SystemSetting

logger = logging.getLogger(__name__)
# key -> (label, type, min, max)
EDITABLE = {
    "delivery_base_fee": ("Base delivery fee (GH₵)", float, 0, 100),
    "delivery_per_km": ("Extra fee per km beyond the free distance (GH₵)", float, 0, 50),
    "delivery_free_km": ("Distance covered by the base fee (km)", float, 0, 20),
    "default_delivery_radius_km": ("Default delivery radius (km)", float, 1, 100),
    "rider_share_percent": ("Rider keeps this % of the delivery fee (platform deducts the rest)", int, 0, 100),
    "vendor_commission_percent": ("Platform commission on vendor food sales (%)", float, 0, 50),
    "payout_hold_hours": ("Hold earnings before they can be withdrawn (hours)", int, 0, 336),
    "min_withdrawal": ("Minimum withdrawal (GH₵)", float, 1, 1000),
    "max_withdrawal_per_day": ("Maximum withdrawn per person per day (GH₵)", float, 10, 100000),
    "withdrawal_approval_threshold": ("Withdrawals above this need admin approval (GH₵, 0 = never)", float, 0, 100000),
    "withdrawal_fee": ("Fee charged per withdrawal (GH₵)", float, 0, 50),
    "payout_account_cooldown_hours": ("Wait after changing a payout account before withdrawing (hours)", int, 0, 168),
    "cod_enabled": ("Pay on delivery enabled (1 = yes, 0 = no)", int, 0, 1),
    "cod_max_order_total": ("Largest order allowed as pay-on-delivery (GH₵)", float, 10, 5000),
    "cod_rider_max_cash_in_hand": ("Most unremitted cash a rider may hold before no more cash orders (GH₵)", float, 0, 10000),
    "cod_max_failures": ("Refused cash payments before pay-on-delivery is blocked for a customer", int, 1, 10),
    "cod_block_days": ("Days a customer is blocked from pay-on-delivery", int, 1, 365),
    "rec_w_value": ("AI weight: price fit / value", float, 0, 5), "rec_w_distance": ("AI weight: distance", float, 0, 5),
    "rec_w_time": ("AI weight: time until ready/arrival", float, 0, 5), "rec_w_fee": ("AI weight: delivery fee share", float, 0, 5),
    "rec_w_rating": ("AI weight: vendor rating", float, 0, 5), "rec_w_popularity": ("AI weight: popularity", float, 0, 5),
    "rec_w_memory": ("AI weight: personal food memory", float, 0, 5), "rec_w_time_of_day": ("AI weight: right meal for the time of day", float, 0, 5),
    "rec_w_craving": ("AI weight: stated cravings", float, 0, 5), "rec_retrieval_limit": ("AI: max dishes scored per request", int, 50, 2000),
    "rec_event_retention_days": ("AI: keep recommendation tracking for (days)", int, 7, 365),
    "avg_speed_kmh": ("Average rider speed used for ETAs (km/h)", float, 5, 60),
    "rider_assign_buffer_minutes": ("Typical wait for a rider to reach the vendor (minutes)", int, 0, 60),
    "order_payment_timeout_minutes": ("Cancel unpaid orders after (minutes)", int, 5, 240),
    "scheduled_min_lead_minutes": ("Minimum lead time for scheduled orders (minutes)", int, 15, 720),
    "scheduled_max_days": ("How far ahead orders can be scheduled (days)", int, 1, 30),
    "review_reminder_hours": ("Send review reminders after (hours)", int, 1, 72),
}


def current(db: Session) -> list[dict]:
    s = get_settings()
    return [{"key": k, "label": label, "value": getattr(s, k), "min": lo, "max": hi, "type": "int" if t is int else "number"}
            for k, (label, t, lo, hi) in EDITABLE.items()]


def validate(key: str, raw) -> float | int:
    if key not in EDITABLE:
        raise api_error(422, "unknown_setting", "That setting can't be changed here.")
    _, typ, lo, hi = EDITABLE[key]
    try:
        v = typ(raw)
    except (TypeError, ValueError):
        raise api_error(422, "bad_value", "Enter a valid number.")
    if not (lo <= v <= hi):
        raise api_error(422, "out_of_range", f"Value must be between {lo} and {hi}.")
    return v


def update(db: Session, key: str, raw, admin_id) -> None:
    v = validate(key, raw)
    row = db.get(SystemSetting, key) or SystemSetting(key=key, value=str(v))
    row.value, row.updated_by = str(v), admin_id
    db.add(row)
    db.commit()
    setattr(get_settings(), key, v)


def load_overrides(db: Session) -> int:
    n = 0
    for row in db.scalars(select(SystemSetting)):
        if row.key in EDITABLE:
            try:
                setattr(get_settings(), row.key, validate(row.key, row.value))
                n += 1
            except Exception:
                logger.warning("Ignoring invalid stored setting %s=%r", row.key, row.value)
    return n


async def refresh_loop(interval: int = 60) -> None:
    while True:
        try:
            def once():
                with SessionLocal() as db:
                    load_overrides(db)
            await asyncio.to_thread(once)
        except Exception:
            logger.exception("Settings refresh failed")
        await asyncio.sleep(interval)
