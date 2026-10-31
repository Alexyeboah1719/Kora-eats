"""Meal combinations from ONE vendor that fit a total budget and share a single delivery fee."""
import itertools
from decimal import Decimal

from app.core.money import cedis
from app.services.recommend.types import Request, Scored

MAX_POOL = 8


def role(s: Scored) -> str:
    meals, it = set(s.cand.item.meal_types or []), s.cand.item
    if "drink" in meals or (it.category and it.category.name.lower() == "drinks"):
        return "drink"
    if "dessert" in meals:
        return "dessert"
    if meals and meals <= {"snack"}:
        return "side"
    return "main"


def build_combos(pool: list[Scored], req: Request, max_results: int = 3) -> list[dict]:
    """Best 2-3 item sets per vendor (one main, plus a side / drink / dessert) whose total, delivery included, fits the budget."""
    if req.budget is None:
        return []
    by_vendor: dict[int, list[Scored]] = {}
    for s in pool:
        by_vendor.setdefault(s.cand.vendor.id, []).append(s)
    found: list[tuple[float, dict]] = []
    for lst in by_vendor.values():
        lst = sorted(lst, key=lambda s: -s.score)[:MAX_POOL]
        head = lst[0].cand
        modes = []
        if req.fulfillment in ("PICKUP", "BEST") and req.pos is not None:
            modes.append(("PICKUP", Decimal("0")))
        if req.fulfillment in ("DELIVERY", "BEST") and head.in_zone and head.fee is not None:
            modes.append(("DELIVERY", head.fee))
        for mode, fee in modes:
            for r in (2, 3):
                for combo in itertools.combinations(lst, r):
                    roles = [role(s) for s in combo]
                    if len(set(roles)) != len(roles) or "main" not in roles:
                        continue
                    price = sum((s.cand.price for s in combo), Decimal("0"))
                    total = price + fee
                    if total > req.budget:
                        continue
                    value = sum(s.score for s in combo) / len(combo) + 0.08 * len(combo) + (0.05 if "drink" in roles else 0)
                    found.append((value, {
                        "vendor": {"id": head.vendor.id, "name": head.vendor.name, "business_label": None},
                        "items": [{"menu_item_id": s.cand.item.id, "name": s.cand.item.name, "price": float(s.cand.price), "role": role(s)} for s in combo],
                        "fulfillment": mode, "food_total": float(price), "delivery_fee": float(fee), "total": float(total),
                        "left_over": float(req.budget - total),
                        "why": [f"{len(combo)} items for {cedis(total)} {'with delivery' if mode == 'DELIVERY' else 'for pickup'} (within your budget)"]
                               + ([f"One delivery fee covers everything (saves {cedis(fee * (len(combo) - 1))})"] if mode == "DELIVERY" and fee > 0 else []),
                    }))
    found.sort(key=lambda t: -t[0])
    seen, out = set(), []
    for _, combo in found:
        key = (combo["vendor"]["id"], tuple(sorted(i["menu_item_id"] for i in combo["items"])))
        vendor_seen = sum(1 for o in out if o["vendor"]["id"] == combo["vendor"]["id"])
        if key in seen or vendor_seen >= 1:                  # at most one combo per vendor so the list stays varied
            continue
        seen.add(key)
        out.append(combo)
        if len(out) >= max_results:
            break
    return out
