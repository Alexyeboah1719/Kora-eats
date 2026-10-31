"""KORA EATS decision-engine endpoints. Guests can use them (no memory, no tracking); signed-in customers get personalisation."""
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.core.errors import api_error
from app.core.rate_limit import rate_limit
from app.core.timeutil import utcnow
from app.db.session import get_db
from app.models.ai import AiConversation, CustomerPreference, RecommendationEvent
from app.models.catalog import MenuItem, Vendor
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.services import food_taxonomy as tx
from app.services.recommend import engine, events, memory as mem_mod
from app.services.recommend.scoring import meal_period
from app.services.recommend.types import CRAVINGS

router = APIRouter(tags=["recommend"])
limit_ai = [Depends(rate_limit("rec", 60, 60))]


def _pos(lat, lng):
    return (lat, lng) if lat is not None and lng is not None else None


def _clean(values, allowed, what):
    try:
        return tx.clean(values, allowed)
    except ValueError as e:
        raise api_error(422, "bad_option", str(e))


def _cravings(values: list[str]) -> list[str]:
    out = []
    for v in values:
        k = v.strip().lower().replace(" ", "_").replace("-", "_")
        if k not in CRAVINGS:
            raise api_error(422, "bad_craving", f"'{v}' isn't a craving I know. Choose from: {', '.join(CRAVINGS)}")
        if k not in out:
            out.append(k)
    return out


class Where(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    fulfillment: Literal["DELIVERY", "PICKUP", "BEST"] = "BEST"
    dietary: list[str] = []
    avoid_allergens: list[str] = []
    limit: int = Field(8, ge=1, le=20)


class AffordIn(Where):
    budget: Decimal = Field(gt=0, le=5000, decimal_places=2)       # the TOTAL, delivery fee included
    cravings: list[str] = []
    meal_type: str | None = None


class CravingIn(Where):
    cravings: list[str] = Field(min_length=1, max_length=9)
    budget: Decimal | None = Field(None, gt=0, le=5000, decimal_places=2)


@router.get("/ai/feed", dependencies=limit_ai)
def feed(lat: float | None = Query(None, ge=-90, le=90), lng: float | None = Query(None, ge=-180, le=180), limit: int = Query(8, ge=1, le=20),
         meal_type: str | None = None, fulfillment: Literal["DELIVERY", "PICKUP", "BEST"] = "BEST", db: Session = Depends(get_db),
         user: User | None = Depends(get_optional_user)):
    """Dishes ranked for this customer right now, each with the reasons it was chosen."""
    if meal_type and meal_type not in tx.MEAL_TYPES:
        raise api_error(422, "bad_option", "Unknown meal time.")
    return engine.feed(db, user, _pos(lat, lng), limit, meal_type, fulfillment)


@router.post("/ai/afford", dependencies=limit_ai)
def afford(data: AffordIn, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    """What can I afford? Finds dishes (and combos) where food + delivery or pickup fits inside the budget."""
    if data.meal_type and data.meal_type not in tx.MEAL_TYPES:
        raise api_error(422, "bad_option", "Unknown meal time.")
    return engine.afford(db, user, _pos(data.lat, data.lng), data.budget, data.fulfillment, _cravings(data.cravings),
                         _clean(data.dietary, tx.DIETARY, "dietary"), _clean(data.avoid_allergens, tx.ALLERGENS, "allergens"), data.meal_type, data.limit)


@router.post("/ai/craving", dependencies=limit_ai)
def craving(data: CravingIn, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    """Craving Diagnosis: very hungry, spicy, filling, light, sweet, cheap, popular, comfort food, high protein."""
    return engine.craving(db, user, _pos(data.lat, data.lng), _cravings(data.cravings), data.budget, data.fulfillment,
                          _clean(data.dietary, tx.DIETARY, "dietary"), _clean(data.avoid_allergens, tx.ALLERGENS, "allergens"), data.limit)


@router.get("/ai/options")
def options():
    return {"cravings": [{"id": k, "label": v} for k, v in CRAVINGS.items()], "meal_types": [{"id": k, "label": v} for k, v in tx.MEAL_TYPES.items()]}


class EventIn(BaseModel):
    surface: Literal["feed", "afford", "craving", "assistant", "group"]
    kind: Literal["click", "add_to_cart"]
    menu_item_id: int
    rank: int | None = Field(None, ge=1, le=100)


@router.post("/ai/events", response_model=MessageResponse, dependencies=[Depends(rate_limit("rec_events", 120, 60))])
def record_event(data: EventIn, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    """The app tells us when a recommendation was clicked or added to the cart (only stored if personalisation is on)."""
    item = db.get(MenuItem, data.menu_item_id)
    if item is not None:
        events.log_event(db, user, data.surface, data.kind, item.id, item.vendor_id, data.rank)
    return MessageResponse(message="ok")


# ---------------------------------------------------------------- food profile (what the customer tells us)
class FoodProfileIn(BaseModel):
    dietary: list[str] = []
    avoid_allergens: list[str] = []
    spice_preference: Literal["none", "mild", "medium", "hot"] | None = None
    personalization_enabled: bool = True


def _profile_out(p: CustomerPreference | None) -> dict:
    return {"dietary": list(p.dietary or []) if p else [], "avoid_allergens": list(p.avoid_allergens or []) if p else [],
            "spice_preference": p.spice_preference if p else None, "personalization_enabled": p.personalization_enabled if p else True}


@router.get("/me/food-profile")
def get_food_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _profile_out(db.get(CustomerPreference, user.id))


@router.put("/me/food-profile")
def put_food_profile(data: FoodProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dietary needs and allergens are always applied to what we recommend. Unknown allergen info is never treated as safe."""
    p = db.get(CustomerPreference, user.id) or CustomerPreference(user_id=user.id, budget_samples=0)
    p.dietary = _clean(data.dietary, tx.DIETARY, "dietary")
    p.avoid_allergens = _clean(data.avoid_allergens, tx.ALLERGENS, "allergens")
    p.spice_preference = data.spice_preference
    p.personalization_enabled = data.personalization_enabled
    db.add(p)
    db.commit()
    return _profile_out(p)


# ---------------------------------------------------------------- food memory (what we learned) - transparent and erasable
@router.get("/me/food-memory")
def food_memory(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Exactly what KORA EATS has learned from your own orders and saved items. Nothing hidden."""
    prefs = db.get(CustomerPreference, user.id)
    m = mem_mod.load(db, user, prefs)
    base = {"personalization_enabled": prefs.personalization_enabled if prefs else True,
            "forgotten_before": prefs.memory_reset_at.isoformat() if prefs and prefs.memory_reset_at else None}
    if m is None:
        return {**base, "orders_considered": 0, "message": "Personalisation is turned off, so nothing is being learned from your orders."}
    vendors = dict(db.execute(select(Vendor.id, Vendor.name).where(Vendor.id.in_(list(m.vendor_counts) or [0]))).all())
    times = Counter_periods(m)
    return {**base, "orders_considered": m.n_orders, "favorite_categories": m.top_categories(),
            "favorite_vendors": [vendors[v] for v, _ in m.vendor_counts.most_common(3) if v in vendors],
            "typical_dish_price": float(m.typical_price) if m.typical_price else None,
            "typical_spend_per_order": float(m.typical_budget) if m.typical_budget else None,
            "spicy_share_percent": round(m.spicy_share * 100) if m.spicy_share is not None else None,
            "usual_order_times": times, "saved_dishes": len(m.fav_items), "saved_vendors": len(m.fav_vendors),
            "message": "This is used only to rank food for you. You can switch it off or forget it at any time."}


def Counter_periods(m) -> list[str]:
    from collections import Counter
    c = Counter()
    for hour, n in m.usual_hours.items():
        c[meal_period(hour)] += n
    return [p for p, _ in c.most_common(2)]


@router.delete("/me/food-memory", response_model=MessageResponse)
def forget(include_chats: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Forget my history: orders before now stop influencing recommendations, and the tracking data is deleted."""
    p = db.get(CustomerPreference, user.id) or CustomerPreference(user_id=user.id, budget_samples=0)
    p.memory_reset_at = utcnow()
    p.top_categories, p.avg_budget, p.budget_samples, p.spicy_preference = None, None, 0, None   # the learned lean (not your stated one)
    db.add(p)
    db.execute(delete(RecommendationEvent).where(RecommendationEvent.user_id == user.id))
    if include_chats:
        db.execute(delete(AiConversation).where(AiConversation.user_id == user.id))
    db.commit()
    return MessageResponse(message="Done. KORA EATS has forgotten your order history for recommendations.")


# ====================================================================== Split the Budget (no group needed)
class PersonIn(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] = ""
    cravings: list[str] = []
    terms: list[str] = Field([], max_length=8)
    max_price: Decimal | None = Field(None, gt=0, le=5000, decimal_places=2)
    dietary: list[str] = []
    avoid_allergens: list[str] = []
    no_spice: bool = False


class SplitIn(BaseModel):
    people: int = Field(ge=2, le=20)
    budget: Decimal | None = Field(None, gt=0, le=5000, decimal_places=2)       # the whole group's total, delivery included
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    fulfillment: Literal["DELIVERY", "PICKUP", "BEST"] = "BEST"
    preferences: list[PersonIn] = Field([], max_length=20)
    vendor_id: int | None = None


@router.post("/ai/group/split", dependencies=limit_ai)
def split_budget(data: SplitIn, db: Session = Depends(get_db)):
    """Split the Budget: N people share one budget. Finds the vendor and one dish each that fits and suits everyone best."""
    import re
    from app.core.money import cedis_compact
    from app.services.recommend import group as solver
    from app.services.recommend.retrieve import retrieve
    from app.services.recommend.types import Request
    if len(data.preferences) > data.people:
        raise api_error(422, "too_many_preferences", "More preference lists than people.")
    persons = []
    for i in range(data.people):
        p = data.preferences[i] if i < len(data.preferences) else PersonIn()
        terms = list(dict.fromkeys(w for w in (re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()[:30] for t in p.terms) if w))
        persons.append(solver.Person(label=p.label or f"Person {i + 1}", cravings=_cravings(p.cravings), terms=terms, max_price=p.max_price,
                                     diet=_clean(p.dietary, tx.DIETARY, "dietary"), avoid=_clean(p.avoid_allergens, tx.ALLERGENS, "allergens"), no_spice=p.no_spice))
    pos = _pos(data.lat, data.lng)
    cands = retrieve(db, Request(pos=pos, budget=data.budget, fulfillment=data.fulfillment, vendor_id=data.vendor_id, surface="group"), None)
    out = solver.build_plans(persons, cands, data.budget, data.fulfillment, pos)
    n = len(out["plans"])
    out["headline"] = (f"{data.people} people" + (f", {cedis_compact(data.budget)} total" if data.budget else "") +
                       (f": {n} plan{'s' if n != 1 else ''} found." if n else ": no plan fits yet."))
    if pos is None and data.fulfillment != "PICKUP":
        out["note"] = "Set your delivery location so I can include the delivery fee in the budget."
    return out
