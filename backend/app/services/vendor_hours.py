from datetime import datetime

from app.core.timeutil import utcnow


def is_open_at(vendor, now: datetime | None = None) -> bool:
    """Manual switch AND today's opening hours (Ghana = UTC+0). No hours set = always open."""
    if not vendor.is_open:
        return False
    hours = {h.weekday: h for h in vendor.hours}
    if not hours:
        return True
    now = now or utcnow()
    t, wd = now.time(), now.weekday()
    today = hours.get(wd)
    if today and not today.is_closed:
        if today.opens_at <= today.closes_at:
            if today.opens_at <= t < today.closes_at:
                return True
        elif t >= today.opens_at:       # overnight shift started today
            return True
    yday = hours.get((wd - 1) % 7)       # overnight shift that began yesterday
    return bool(yday and not yday.is_closed and yday.opens_at > yday.closes_at and t < yday.closes_at)
