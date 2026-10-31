"""Group Food AI + Split the Budget.

Given several people (each with wishes and hard limits) and optionally a TOTAL budget, find the vendor and one dish per person
that satisfies the group as well as possible. Hard rules (diet, allergens, no spice, personal price cap) are never broken.
With a budget this is a multiple-choice knapsack, solved exactly by dynamic programming (every person picks exactly one dish).
Delivery is one shared fee for the whole group."""
import math
import statistics
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.money import cedis, cedis_compact
from app.services import food_taxonomy as tx, quote_service
from app.services.recommend.scoring import craving_score, rating_score
from app.services.recommend.types import CRAVINGS, Candidate

MAX_PEOPLE = 20
OPTIONS_PER_PERSON = 12
DP_MAX_UNITS = 1000


@dataclass
class Person:
    label: str
    user_id: str | None = None
    cravings: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    max_price: Decimal | None = None
    diet: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    no_spice: bool = False

    @property
    def has_wishes(self) -> bool:
        return bool(self.cravings or self.terms)


@dataclass
class Option:
    cand: Candidate
    match: float          # 0..1 how well it fits what this person asked for (1.0 when they asked for nothing)
    sat: float            # match blended with vendor quality, used for the optimisation
    why: list[str]


def allowed(p: Person, c: Candidate) -> bool:
    """Hard rules. Unknown allergen/diet information is never treated as safe."""
    it = c.item
    if p.max_price is not None and c.price > p.max_price:
        return False
    if p.no_spice and (it.spice_level or 0) > 0:
        return False
    return tx.check_diet(list(it.dietary or []), list(it.allergens or []), bool(it.dietary_declared), p.diet, p.avoid)[0]


def _text(c: Candidate) -> str:
    it = c.item
    return f"{it.name} {it.tags} {it.description} {' '.join(it.traits or [])}".lower().replace("_", " ")


def option_for(p: Person, c: Candidate, median_price: float, max_pop: int) -> Option | None:
    if not allowed(p, c):
        return None
    parts, why = [], []
    if p.terms:
        hit = [t for t in p.terms if t.lower() in _text(c)]
        parts.append(len(hit) / len(p.terms))
        if hit:
            why.append("Matches: " + ", ".join(hit))
    if p.cravings:
        pop = math.log1p(c.item.order_count or 0) / math.log1p(max_pop) if max_pop > 0 else 0.0
        cs, matched, _ = craving_score(c, p.cravings, median_price, pop)
        parts.append(cs or 0.0)
        if matched:
            why.append("Craving: " + ", ".join(CRAVINGS[k].lower() for k in matched))
    match = sum(parts) / len(parts) if parts else 1.0
    if p.no_spice:
        why.append("Not spicy")
    if p.diet:
        why.append("Meets: " + ", ".join(tx.DIETARY[d].lower() for d in p.diet))
    if p.avoid:
        why.append("Vendor confirmed allergen information")
    if p.max_price is not None:
        why.append(f"Within {cedis(p.max_price)}")
    pop = math.log1p(c.item.order_count or 0) / math.log1p(max_pop) if max_pop > 0 else 0.0
    sat = 0.6 * match + 0.3 * rating_score(c.vendor.rating_avg, c.vendor.rating_count) + 0.1 * pop
    return Option(c, match, sat, why)


def _knapsack(options: list[list[Option]], cap_money: Decimal) -> list[Option] | None:
    """Pick exactly one option per person, total price <= cap_money, maximising total satisfaction (ties: cheaper)."""
    n = len(options)
    unit = max(Decimal("1"), (cap_money / DP_MAX_UNITS).to_integral_value(rounding="ROUND_CEILING"))
    cap = int(cap_money // unit)
    cost = lambda o: int(math.ceil(float(o.cand.price / unit)))              # rounded UP, so the real total can never exceed the budget
    NEG = float("-inf")
    best = [[NEG] * (cap + 1) for _ in range(n + 1)]
    pick: list[list[int]] = [[-1] * (cap + 1) for _ in range(n)]
    best[0][0] = 0.0
    for i in range(n):
        for w in range(cap + 1):
            if best[i][w] == NEG:
                continue
            for k, o in enumerate(options[i]):
                nw = w + cost(o)
                if nw <= cap:
                    v = best[i][w] + o.sat - 1e-6 * float(o.cand.price)         # tiny price term breaks ties towards cheaper plans
                    if v > best[i + 1][nw]:
                        best[i + 1][nw], pick[i][nw] = v, k * (cap + 1) + w
    end = max(range(cap + 1), key=lambda w: best[n][w])
    if best[n][end] == NEG:
        return None
    chosen, w = [], end
    for i in range(n - 1, -1, -1):
        code = pick[i][w]
        k, prev = divmod(code, cap + 1)
        chosen.append(options[i][k])
        w = prev
    return list(reversed(chosen))


def plan_for_vendor(persons: list[Person], per_person: list[list[Option]], fee: Decimal, budget: Decimal | None) -> list[Option] | None:
    options = [sorted(o, key=lambda x: (-x.sat, x.cand.price))[:OPTIONS_PER_PERSON] for o in per_person]
    if any(not o for o in options):
        return None
    if budget is None:
        return [o[0] for o in options]
    left = budget - fee
    if left <= 0:
        return None
    return _knapsack(options, left)


def build_plans(persons: list[Person], cands: list[Candidate], budget: Decimal | None, fulfillment: str, pos, top: int = 3) -> dict:
    """-> {"plans": [...best vendors...], "hint": str|None}. Plans are complete (every person gets a dish)."""
    if not persons or not cands:
        return {"plans": [], "partial": [], "hint": "No dishes are available right now."}
    prices = [float(c.price) for c in cands]
    median_price, max_pop = statistics.median(prices), max((c.item.order_count or 0) for c in cands)
    by_vendor: dict[int, list[Candidate]] = {}
    for c in cands:
        by_vendor.setdefault(c.vendor.id, []).append(c)
    full, partial = [], []
    cheapest_need: Decimal | None = None
    for vid, vc in by_vendor.items():
        per = [[o for c in vc if (o := option_for(p, c, median_price, max_pop))] for p in persons]
        unmet = [persons[i].label for i, o in enumerate(per) if not o]
        head = vc[0]
        modes = []
        if fulfillment in ("PICKUP", "BEST"):
            modes.append(("PICKUP", Decimal("0"), False))
        if fulfillment in ("DELIVERY", "BEST"):
            if pos is None:
                modes.append(("DELIVERY", Decimal("0"), True))               # fee depends on a location we don't have
            elif head.in_zone and head.fee is not None:
                modes.append(("DELIVERY", head.fee, False))
        if unmet:
            partial.append({"vendor": head.vendor, "unmet": unmet})
            continue
        candidates_here = []
        for mode, fee, unknown in modes:
            chosen = plan_for_vendor(persons, per, fee, budget)
            if chosen is None:
                floor = sum((min(o.cand.price for o in opts) for opts in per), Decimal("0")) + fee   # cheapest possible for this group here
                cheapest_need = floor if cheapest_need is None else min(cheapest_need, floor)
                continue
            food = sum((o.cand.price for o in chosen), Decimal("0"))
            candidates_here.append((food + fee, mode, fee, unknown, chosen, food))
        if not candidates_here:
            continue
        total, mode, fee, unknown, chosen, food = min(candidates_here, key=lambda t: (t[0], 0 if t[1] == "PICKUP" else 1))
        compat = sum(o.match for o in chosen) / len(chosen)
        full.append({"vendor": head.vendor, "fulfillment": mode, "fee": fee, "fee_unknown": unknown, "total": total, "food": food,
                     "chosen": chosen, "compat": compat, "head": head})
    full.sort(key=lambda p: (-round(p["compat"], 3), p["total"]))
    plans = [_plan_out(p, persons, budget) for p in full[:top]]
    hint = None
    if not plans:
        if cheapest_need is not None and budget is not None:
            hint = f"Not enough for this group: the cheapest meal for everyone is about {cedis(cheapest_need)} in total."
        elif partial:
            hint = "No single vendor can serve everyone's dietary needs. Try the vendors listed below, or relax a requirement."
    return {"plans": plans, "partial": [{"vendor": x["vendor"].name, "no_dish_for": x["unmet"]} for x in partial[:3]], "hint": hint}


def _plan_out(p: dict, persons: list[Person], budget: Decimal | None) -> dict:
    n = len(persons)
    head = p["head"]
    fee = p["fee"]
    per_head_fee = (fee / n).quantize(Decimal("0.01"))
    people = [{"label": persons[i].label, "user_id": persons[i].user_id, "menu_item_id": o.cand.item.id, "dish": o.cand.item.name,
               "price": float(o.cand.price), "share_of_delivery": float(per_head_fee), "pays": float(o.cand.price + per_head_fee),
               "match": round(o.match * 100), "why": o.why} for i, o in enumerate(p["chosen"])]
    out = {"vendor": {"id": head.vendor.id, "name": head.vendor.name, "area": head.vendor.area, "rating_avg": round(head.vendor.rating_avg, 1),
                      "rating_count": head.vendor.rating_count},
           "fulfillment": p["fulfillment"], "fee_unknown": p["fee_unknown"], "people": people, "food_total": float(p["food"]),
           "delivery_fee": float(fee), "total": float(p["total"]), "cost_per_person": float((p["total"] / n).quantize(Decimal("0.01"))),
           "compatibility": round(p["compat"] * 100),
           "left_over": float(budget - p["total"]) if budget is not None else None,
           "why": [f"{n} people, one vendor, {'one delivery fee' if p['fulfillment'] == 'DELIVERY' and fee > 0 else 'no delivery fee'}"]}
    if budget is not None:
        out["why"].append(f"{cedis(p['total'])} in total, within your {cedis_compact(budget)} budget")
    if p["fulfillment"] == "DELIVERY" and head.fee is not None:
        out["pickup_message"] = quote_service.pickup_message(head.fee, head.ready, head.eta)
    return out
