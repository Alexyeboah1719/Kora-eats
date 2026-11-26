"""ONE place for delivery fee, preparation time, travel time and the pickup-vs-delivery comparison.
Checkout, the cart, search ranking and the AI all call these functions, so the numbers always agree.
Times are ESTIMATES (preparation minutes come from the vendor; travel uses an average speed)."""
from decimal import ROUND_HALF_UP, Decimal

from app.core.config import get_settings
from app.core.money import cedis

CENT = Decimal("0.01")


def delivery_fee(distance_km: float | None) -> Decimal | None:
    """Base fee covers the first few km; each extra km adds a per-km charge. None = distance unknown."""
    if distance_km is None:
        return None
    s = get_settings()
    extra = max(0.0, distance_km - s.delivery_free_km)
    return (Decimal(str(s.delivery_base_fee)) + Decimal(str(extra)) * Decimal(str(s.delivery_per_km))).quantize(CENT, ROUND_HALF_UP)


def prep_minutes(vendor_avg_prep: int, item_preps: list[int]) -> int:
    """Ready time = the slowest dish in the order (kitchens cook in parallel), never less than 5 minutes."""
    return max([5, *item_preps]) if item_preps else max(5, vendor_avg_prep)


def travel_minutes(distance_km: float) -> int:
    return max(1, round(distance_km / get_settings().avg_speed_kmh * 60))


def delivery_eta(prep: int, distance_km: float) -> int:
    """Cooking + waiting for a rider to arrive + riding to the customer."""
    return prep + get_settings().rider_assign_buffer_minutes + travel_minutes(distance_km)


def _mins(n: int) -> str:
    return f"{n} minute{'s' if n != 1 else ''}"


def pickup_message(fee: Decimal | None, ready: int, eta: int | None) -> str | None:
    """'Pickup saves GH₵7.00 and your meal will be ready in 15 minutes.' (or None when pickup isn't better)."""
    min_saving = get_settings().pickup_min_saving_minutes
    cheaper = fee is not None and fee > 0
    faster_by = (eta - ready) if eta is not None else 0
    if cheaper:
        return f"Pickup saves {cedis(fee)} and your meal will be ready in {_mins(ready)}."
    if faster_by >= min_saving:
        return f"Pickup is about {_mins(faster_by)} faster: your meal will be ready in {_mins(ready)}."
    return None


def compare(vendor_avg_prep: int, item_preps: list[int], distance_km: float | None, in_zone: bool = True) -> dict:
    """Both options side by side. 'delivery' is None when we can't price it (no address yet)."""
    ready = prep_minutes(vendor_avg_prep, item_preps)
    out = {"pickup": {"fee": 0.0, "ready_minutes": ready}, "delivery": None, "pickup_saving": None, "pickup_message": None}
    if distance_km is None:
        return out
    fee = delivery_fee(distance_km)
    eta = delivery_eta(ready, distance_km)
    out["delivery"] = {"fee": float(fee), "eta_minutes": eta, "distance_km": round(distance_km, 2), "available": in_zone}
    msg = pickup_message(fee, ready, eta)
    out["pickup_message"] = msg
    if msg:
        out["pickup_saving"] = {"amount": float(fee), "minutes_faster": max(0, eta - ready)}
    return out
