"""Pure scoring functions (no database access). Each component returns 0..1; the final score is a weighted mean of
the components that apply. Weights come from settings so admins can tune them without a deploy."""
import math
import statistics
from datetime import datetime
from decimal import Decimal

from app.core.config import get_settings
from app.services.recommend.memory import Memory
from app.services.recommend.types import FOOD_CRAVINGS, Candidate, Request, Scored

PERIOD_MEALS = {"breakfast": {"breakfast"}, "lunch": {"lunch"}, "snack": {"snack", "dessert"}, "dinner": {"dinner"}, "late": {"snack", "dinner"}}
PERIOD_LABEL = {"breakfast": "breakfast", "lunch": "lunch", "snack": "snack", "dinner": "dinner", "late": "late-night"}


def meal_period(hour: int) -> str:
    """Ghana is UTC+0 all year, so the server clock is local time."""
    return "breakfast" if 5 <= hour < 11 else "lunch" if 11 <= hour < 16 else "snack" if 16 <= hour < 18 else "dinner" if 18 <= hour < 23 else "late"


def weights() -> dict[str, float]:
    s = get_settings()
    return {"value": s.rec_w_value, "distance": s.rec_w_distance, "time": s.rec_w_time, "fee": s.rec_w_fee, "rating": s.rec_w_rating,
            "popularity": s.rec_w_popularity, "memory": s.rec_w_memory, "time_of_day": s.rec_w_time_of_day, "craving": s.rec_w_craving}


def choose_option(c: Candidate, req: Request) -> tuple[str, Decimal, bool] | None:
    """(fulfillment, total cost, fee_unknown) - the cheapest option that fits the budget, or None if nothing fits."""
    opts: dict[str, tuple[Decimal, bool]] = {}
    if req.fulfillment in ("PICKUP", "BEST") and req.pos is not None:
        opts["PICKUP"] = (c.price, False)
    if req.fulfillment in ("PICKUP",) and req.pos is None:
        opts["PICKUP"] = (c.price, False)
    if req.fulfillment in ("DELIVERY", "BEST"):
        if req.pos is None:
            opts["DELIVERY"] = (c.price, True)                        # can't price delivery without a location: say so, don't guess
        elif c.in_zone and c.fee is not None:
            opts["DELIVERY"] = (c.price + c.fee, False)
    if req.budget is not None:
        opts = {k: v for k, v in opts.items() if v[0] <= req.budget}
    if not opts:
        return None
    best = min(opts, key=lambda k: (opts[k][0], 0 if k == "PICKUP" else 1))
    return best, opts[best][0], opts[best][1]


def value_score(price: Decimal, total: Decimal, budget: Decimal | None, target: float) -> float:
    if budget:
        r = float(total / budget)
        return 1.0 if r <= 0.5 else max(0.35, 1 - (r - 0.5) * 1.3)      # comfortably inside the budget is best
    return math.exp(-abs(float(price) - target) / max(target, 1.0))     # close to what they usually spend (or the market median)


def rating_score(avg: float, count: int) -> float:
    """Bayesian average: a vendor with 2 five-star reviews doesn't outrank one with 200 four-and-a-half-star reviews."""
    bayes = (avg * count + 4.0 * 5) / (count + 5)
    return min(1.0, max(0.0, (bayes - 3.0) / 2.0))


def time_of_day_score(c: Candidate, period: str, meal_forced: bool) -> float:
    meals = set(c.item.meal_types or [])
    if meal_forced:
        return 1.0
    if not meals:
        return 0.5
    if meals & PERIOD_MEALS[period]:
        return 1.0
    return 0.6 if meals <= {"drink", "dessert", "snack"} else 0.1       # side items and drinks suit most times; a breakfast dish at dinner doesn't


def memory_score(c: Candidate, m: Memory, spice: str | None) -> tuple[float, str | None]:
    it, vid = c.item, c.vendor.id
    if it.id in m.item_counts:
        return 1.0, "reorder"
    if it.id in m.liked_items:
        return 0.95, "liked"
    if it.id in m.fav_items:
        return 0.9, "saved_item"
    if vid in m.fav_vendors:
        return 0.75, "saved_vendor"
    if vid in m.vendor_counts:
        return 0.7, "vendor_before"
    if it.category_id in m.category_counts:
        share = m.category_counts[it.category_id] / max(1, sum(m.category_counts.values()))
        return 0.5 + 0.4 * share, "category"
    if spice == "hot" and (it.spice_level or 0) >= 2:
        return 0.6, "likes_spicy"
    return 0.25, None                                                     # small baseline keeps room for discovery


def craving_score(c: Candidate, cravings: list[str], median_price: float, pop: float) -> tuple[float | None, list[str], bool]:
    """-> (score, matched food cravings, eligible). A dish must match at least one FOOD craving to be eligible."""
    it, traits, meals = c.item, set(c.item.traits or []), set(c.item.meal_types or [])
    parts, food_parts, matched = [], [], []
    for k in cravings:
        if k == "spicy":
            s = 1.0 if (it.spice_level or 0) >= 2 else 0.5 if (it.spice_level or 0) == 1 else 0.0
        elif k == "filling":
            s = 1.0 if "filling" in traits else 0.0
        elif k == "light":
            s = 1.0 if ("light" in traits and "filling" not in traits) else 0.6 if "light" in traits else 0.0
        elif k == "sweet":
            s = 1.0 if ("sweet" in traits or "dessert" in meals) else 0.0
        elif k == "comfort":
            s = 1.0 if "comfort" in traits else 0.0
        elif k == "high_protein":
            s = 1.0 if "high_protein" in traits else 0.0
        elif k == "very_hungry":
            s = (0.6 if "filling" in traits else 0.0) + (0.4 if float(c.price) >= median_price else 0.0)
        elif k == "cheap":
            s = 1.0 if float(c.price) <= 0.75 * median_price else max(0.0, 1.0 - (float(c.price) / median_price - 0.75))
        elif k == "popular":
            s = pop
        else:
            continue
        parts.append(s)
        if k in FOOD_CRAVINGS:
            food_parts.append(s)
            if s > 0:
                matched.append(k)
    eligible = not food_parts or any(p > 0 for p in food_parts)
    return (sum(parts) / len(parts) if parts else None), matched, eligible


def score_all(cands: list[Candidate], req: Request, mem: Memory | None, spice_pref: str | None, now: datetime) -> list[Scored]:
    if not cands:
        return []
    W = weights()
    prices = [float(c.price) for c in cands]
    median_price = statistics.median(prices)
    target = float(mem.typical_price) if (mem and mem.typical_price) else median_price
    max_pop = max((c.item.order_count or 0) for c in cands)
    period = meal_period(now.hour)
    w = dict(W)
    if "cheap" in req.cravings:
        w["value"] *= 1.8
    if "popular" in req.cravings:
        w["popularity"] *= 2.5

    out: list[Scored] = []
    for c in cands:
        opt = choose_option(c, req)
        if opt is None:
            continue
        ful, total, fee_unknown = opt
        pop = math.log1p(c.item.order_count or 0) / math.log1p(max_pop) if max_pop > 0 else 0.0
        cs, matched, eligible = craving_score(c, req.cravings, median_price, pop)
        if not eligible:
            continue
        parts: dict[str, float] = {"value": value_score(c.price, total, req.budget, target),
                                   "rating": rating_score(c.vendor.rating_avg, c.vendor.rating_count),
                                   "time_of_day": time_of_day_score(c, period, bool(req.meal_type))}
        if c.distance_km is not None:
            parts["distance"] = 1.0 / (1.0 + c.distance_km / 3.0)
        minutes = c.eta if (ful == "DELIVERY" and c.eta) else c.ready
        parts["time"] = 1.0 / (1.0 + (minutes / 45.0) ** 2)
        if ful == "PICKUP":
            parts["fee"] = 1.0
        elif not fee_unknown and c.fee is not None and total > 0:
            parts["fee"] = max(0.0, 1.0 - float(c.fee / total))
        if max_pop > 0:
            parts["popularity"] = pop
        mem_reason, warnings = None, []
        if mem is not None and mem.has_signal:
            parts["memory"], mem_reason = memory_score(c, mem, spice_pref)
        if cs is not None:
            parts["craving"] = cs
        den = sum(w[k] for k in parts)
        score = sum(w[k] * v for k, v in parts.items()) / den if den else 0.0
        if c.original:
            score = min(1.0, score + 0.03)
        if mem is not None and c.vendor.id in mem.disliked_vendors:
            score *= 0.75
            warnings.append(f"You rated {c.vendor.name} {mem.disliked_vendors[c.vendor.id]} stars before.")
        if spice_pref == "mild" and (c.item.spice_level or 0) >= 3:
            score *= 0.7
        if spice_pref == "none_lean" and (c.item.spice_level or 0) >= 2 and "spicy" not in req.cravings:
            score *= 0.85
        out.append(Scored(cand=c, score=score, parts=parts, reasons=[], warnings=warnings, fulfillment=ful, total=total,
                          fee_unknown=fee_unknown, memory_reason=mem_reason, matched_cravings=matched))
    return out


def diversify(scored: list[Scored], limit: int, per_vendor: int = 2) -> list[Scored]:
    """Highest scores first, but no more than `per_vendor` dishes from one vendor while others are available."""
    ranked = sorted(scored, key=lambda s: -s.score)
    picked, deferred, per = [], [], {}
    for s in ranked:
        vid = s.cand.vendor.id
        if per.get(vid, 0) < per_vendor:
            picked.append(s)
            per[vid] = per.get(vid, 0) + 1
        else:
            deferred.append(s)
        if len(picked) >= limit:
            return picked
    return (picked + deferred)[:limit]
