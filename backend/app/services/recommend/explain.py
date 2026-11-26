"""'Why this recommendation?' Every sentence is generated from a number the engine actually used. If we don't know it, we don't say it."""
import math

from app.core.money import cedis, cedis_compact
from app.services import food_taxonomy as tx
from app.services.recommend.memory import Memory
from app.services.recommend.scoring import PERIOD_MEALS
from app.services.recommend.types import CRAVINGS, Request, Scored

PERIOD_PHRASE = {"breakfast": "breakfast", "lunch": "lunch", "snack": "a snack", "dinner": "dinner", "late": "a late meal"}
MEMORY_TEXT = {"reorder": "You've ordered this before", "liked": "You rated this dish highly before", "saved_item": "You saved this dish",
               "saved_vendor": "From a vendor you saved", "vendor_before": "From a vendor you've ordered from", "likes_spicy": "You usually choose spicy food"}


def ceil5(n: int) -> int:
    return int(math.ceil(n / 5.0) * 5)


def reasons_for(s: Scored, req: Request, period: str, mem: Memory | None) -> list[str]:
    c, it, out = s.cand, s.cand.item, []
    where = {"DELIVERY": "with delivery", "PICKUP": "for pickup"}[s.fulfillment]
    if req.budget is not None:
        out.append(f"Within your {cedis_compact(req.budget)} budget ({cedis(s.total)} {where})")
    elif s.fulfillment == "DELIVERY" and not s.fee_unknown and c.fee is not None:
        out.append(f"{cedis(s.total)} with delivery")
    needs = [tx.DIETARY[d].lower() for d in req.diet if d in tx.DIETARY]
    if needs:
        out.append("Meets your dietary needs: " + ", ".join(needs))
    if req.avoid:
        out.append("Vendor confirmed allergen information")
    if s.fulfillment == "PICKUP" and c.fee is not None and c.fee > 0 and c.in_zone:
        out.append(f"Pickup saves {cedis(c.fee)}")
    if s.matched_cravings:
        out.append("Matches your craving: " + ", ".join(CRAVINGS[k].lower() for k in s.matched_cravings))
    if c.distance_km is not None:
        out.append(f"{c.distance_km:.1f} km away")
    if s.fulfillment == "DELIVERY" and c.eta and not s.fee_unknown:
        out.append(f"Delivery under {ceil5(c.eta)} minutes" if c.eta <= 55 else f"Delivery in about {c.eta} minutes")
    elif s.fulfillment == "PICKUP":
        out.append(f"Ready for pickup in about {c.ready} minutes")
    if s.memory_reason in MEMORY_TEXT:
        out.append(MEMORY_TEXT[s.memory_reason])
    elif s.memory_reason == "category" and mem and it.category_id in mem.category_names:
        out.append(f"You often order {mem.category_names[it.category_id]}")
    v = c.vendor
    if v.rating_count >= 5 and v.rating_avg >= 4.3:
        out.append(f"Highly rated vendor ({v.rating_avg:.1f} from {v.rating_count} reviews)")
    elif v.rating_count == 0:
        out.append("New on KORA EATS")
    if (it.order_count or 0) >= 5 and s.parts.get("popularity", 0) >= 0.7:
        out.append("Popular with other customers")
    if not req.meal_type and set(it.meal_types or []) & PERIOD_MEALS[period]:
        out.append(f"Good for {PERIOD_PHRASE[period]}")
    if c.original is not None:
        out.append(f"On offer: {cedis(c.original)} now {cedis(c.price)}")
    return out[:6]
