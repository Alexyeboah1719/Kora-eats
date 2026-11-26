"""Orchestrates retrieve -> score -> explain, and builds the customer-facing answers (feed, afford, craving)."""
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.money import cedis, cedis_compact
from app.core.timeutil import utcnow
from app.models.user import User
from app.services import catalog_service as cat, quote_service
from app.services.recommend import combos, events, memory as mem_mod
from app.services.recommend.explain import ceil5, reasons_for
from app.services.recommend.retrieve import retrieve
from app.services.recommend.scoring import PERIOD_LABEL, diversify, meal_period, score_all
from app.services.recommend.types import Request, Scored

POOL = 60          # how many scored dishes we keep for combos / relaxation hints


@dataclass
class Result:
    items: list[Scored]
    pool: list[Scored]
    period: str
    used_memory: bool
    considered: int
    memory: mem_mod.Memory | None
    request: Request


def _merge(a: list[str], b: list[str] | None) -> list[str]:
    return list(dict.fromkeys([*a, *(b or [])]))


def rank(db: Session, user: User | None, req: Request) -> Result:
    now = req.now or utcnow()
    prefs = mem_mod.get_prefs(db, user)
    req = replace(req, diet=list(req.diet), avoid=list(req.avoid))
    if prefs is not None:                                      # what the customer told us explicitly is always respected (it's safety)
        req.diet, req.avoid = _merge(req.diet, prefs.dietary), _merge(req.avoid, prefs.avoid_allergens)
    mem = mem_mod.load(db, user, prefs) if req.use_memory else None
    spice = (prefs.spice_preference if prefs and prefs.spice_preference else None) or mem_mod.effective_spice(mem)
    cands = retrieve(db, req, spice if spice in ("none", "mild", "hot") else None)
    scored = score_all(cands, req, mem, spice, now)
    pool = sorted(scored, key=lambda s: -s.score)[:POOL]
    top = diversify(pool, req.limit)
    period = meal_period(now.hour)
    for s in pool:
        s.reasons = reasons_for(s, req, period, mem)
    return Result(items=top, pool=pool, period=period, used_memory=any(s.memory_reason for s in top), considered=len(cands), memory=mem, request=req)


# ------------------------------------------------------------------ presentation
def greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def headline(user: User | None, res: Result) -> str:
    req, shown = res.request, res.items
    now = req.now or utcnow()
    name = user.profile.full_name.split()[0] if user else None
    label = req.meal_type or PERIOD_LABEL[res.period]
    who = f"{greeting(now.hour)}{', ' + name if name else ''}"
    if not shown:
        return f"{who}, I couldn't find {label} options that match right now."
    n = len(shown)
    cap = ""
    if req.budget is not None:
        cap = f" under {cedis_compact(req.budget)}"
    elif res.used_memory and res.memory and res.memory.typical_budget:
        top_total = max(s.total for s in shown)
        cap = f" under {cedis_compact(ceil5(int(top_total) + (0 if top_total == int(top_total) else 1)))}"   # e.g. GH₵33.50 -> under GH₵35
    return f"{who}, here {'is' if n == 1 else 'are'} {n} {label} option{'s' if n != 1 else ''}{cap}{' based on your usual choices' if res.used_memory else ''}."


def scored_out(s: Scored, pos, fav_items: set[int]) -> dict:
    c = s.cand
    d = cat.item_out(c.item, pos, fav_items).model_dump()
    options = {"pickup": {"total": float(c.price), "ready_minutes": c.ready}, "delivery": None}
    if c.fee is not None and c.in_zone:
        options["delivery"] = {"fee": float(c.fee), "eta_minutes": c.eta, "total": float(c.price + c.fee)}
    return {"item": d, "vendor": {"id": c.vendor.id, "name": c.vendor.name, "business_label": cat.BUSINESS_LABELS[c.vendor.business_type.value],
                                   "area": c.vendor.area, "rating_avg": round(c.vendor.rating_avg, 1), "rating_count": c.vendor.rating_count},
            "match_score": round(s.score * 100), "why": s.reasons, "warnings": s.warnings, "fulfillment": s.fulfillment,
            "total_cost": float(s.total), "fee_unknown": s.fee_unknown, "options": options,
            "pickup_message": quote_service.pickup_message(c.fee, c.ready, c.eta) if (c.fee is not None and c.in_zone) else None}


def present(db: Session, user: User | None, res: Result, surface: str) -> dict:
    favs = cat.favorite_ids(db, user)[1]
    pos = res.request.pos
    events.log_impressions(db, user, surface, res.items)
    return {"headline": headline(user, res), "results": [scored_out(s, pos, favs) for s in res.items],
            "meta": {"period": res.period, "personalised": res.used_memory, "considered": res.considered,
                     "location_known": pos is not None}}


# ------------------------------------------------------------------ the customer-facing features
def feed(db: Session, user: User | None, pos, limit: int = 8, meal_type: str | None = None, fulfillment: str = "BEST") -> dict:
    return present(db, user, rank(db, user, Request(pos=pos, limit=limit, meal_type=meal_type, fulfillment=fulfillment, surface="feed", max_km=None)), "feed")


def afford(db: Session, user: User | None, pos, budget: Decimal, fulfillment: str = "BEST", cravings: list[str] | None = None,
           diet: list[str] | None = None, avoid: list[str] | None = None, meal_type: str | None = None, limit: int = 8) -> dict:
    """'What can I afford?': the budget is the TOTAL, so food + delivery (or pickup) must fit inside it."""
    req = Request(pos=pos, budget=budget, fulfillment=fulfillment, cravings=cravings or [], diet=diet or [], avoid=avoid or [],
                  meal_type=meal_type, limit=limit, surface="afford")
    res = rank(db, user, req)
    out = present(db, user, res, "afford")
    out["combos"] = combos.build_combos(res.pool, res.request)
    if pos is None and fulfillment != "PICKUP":
        out["note"] = "Set your delivery location so I can include the delivery fee in your budget."
    if not res.items:                                            # say exactly what it would take
        relaxed = rank(db, user, replace(req, budget=None, limit=POOL))
        cheapest = min(relaxed.pool, key=lambda s: s.total, default=None)
        if cheapest is not None:
            c = cheapest.cand
            out["hint"] = (f"The cheapest available option right now is {c.item.name} from {c.vendor.name} at "
                           f"{cedis(cheapest.total)} {'with delivery' if cheapest.fulfillment == 'DELIVERY' else 'for pickup'}.")
    return out


def craving(db: Session, user: User | None, pos, cravings: list[str], budget: Decimal | None = None, fulfillment: str = "BEST",
            diet: list[str] | None = None, avoid: list[str] | None = None, limit: int = 8) -> dict:
    req = Request(pos=pos, budget=budget, fulfillment=fulfillment, cravings=cravings, diet=diet or [], avoid=avoid or [], limit=limit, surface="craving")
    return present(db, user, rank(db, user, req), "craving")


def feed_items(db: Session, user: User | None, pos, limit: int = 8) -> tuple[str, list]:
    """For the home page: ranked MenuItemOut objects carrying their 'why' and match score."""
    res = rank(db, user, Request(pos=pos, limit=limit, surface="feed"))
    favs = cat.favorite_ids(db, user)[1]
    events.log_impressions(db, user, "feed", res.items)
    out = []
    for s in res.items:
        o = cat.item_out(s.cand.item, pos, favs)
        o.why, o.match_score = s.reasons, round(s.score * 100)
        out.append(o)
    return headline(user, res), out
