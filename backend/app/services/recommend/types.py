from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.models.catalog import MenuItem, Vendor

CRAVINGS = {"very_hungry": "Very hungry", "spicy": "Spicy", "filling": "Filling", "light": "Light", "sweet": "Sweet",
            "cheap": "Cheap", "popular": "Popular", "comfort": "Comfort food", "high_protein": "High protein"}
# Cravings that describe the FOOD (a dish must match at least one of them) vs ones that only change how we rank.
FOOD_CRAVINGS = {"very_hungry", "spicy", "filling", "light", "sweet", "comfort", "high_protein"}


@dataclass
class Request:
    pos: tuple[float, float] | None = None
    budget: Decimal | None = None             # TOTAL the customer can spend, delivery fee included
    fulfillment: str = "BEST"                 # DELIVERY | PICKUP | BEST (whichever is cheaper)
    cravings: list[str] = field(default_factory=list)
    diet: list[str] = field(default_factory=list)       # dietary needs (explicit, from the request or the food profile)
    avoid: list[str] = field(default_factory=list)      # allergens to avoid
    meal_type: str | None = None
    max_km: float | None = None
    terms: list[str] = field(default_factory=list)      # food words, e.g. ["chicken", "rice"]
    limit: int = 8
    surface: str = "feed"
    now: datetime | None = None
    use_memory: bool = True
    vendor_id: int | None = None              # restrict to one vendor (group orders are placed with a single vendor)


@dataclass
class Candidate:
    item: MenuItem
    vendor: Vendor
    price: Decimal                            # what the customer pays for the dish (after any promotion)
    original: Decimal | None
    distance_km: float | None
    in_zone: bool                             # can the vendor deliver here?
    fee: Decimal | None                       # delivery fee, None when we don't know where the customer is
    ready: int                                # minutes until the food is ready (pickup)
    eta: int | None                           # minutes until it arrives (delivery)


@dataclass
class Scored:
    cand: Candidate
    score: float                              # 0..1
    parts: dict[str, float]
    reasons: list[str]
    warnings: list[str]
    fulfillment: str                          # the option we suggest: DELIVERY | PICKUP
    total: Decimal                            # what that option costs in total
    fee_unknown: bool = False
    memory_reason: str | None = None
    matched_cravings: list[str] = field(default_factory=list)
