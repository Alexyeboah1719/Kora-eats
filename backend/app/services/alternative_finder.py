"""AI Alternative Finder - suggests alternatives when meals are unavailable.
Finds: closest match from same restaurant, best price within budget, best alternative restaurant."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.money import cedis
from app.models.catalog import MenuItem, Vendor
from app.services import catalog_service as cat


@dataclass
class Alternative:
    """An alternative meal suggestion."""
    type: str  # "same_restaurant", "best_price", "alternative_restaurant"
    item: dict  # MenuItemOut
    vendor: dict
    reason: str
    price_match: float  # 0-1 score of how close price is to original
    similarity_score: float  # 0-1 score of how similar the meal is


def find_alternatives(
    db: Session,
    original_item_id: int,
    user_budget: Decimal | None = None,
    pos: tuple[float, float] | None = None,
    limit: int = 3
) -> list[Alternative]:
    """Find alternatives for an unavailable meal."""
    original = db.get(MenuItem, original_item_id)
    if not original:
        return []
    
    original_vendor = db.get(Vendor, original.vendor_id)
    if not original_vendor:
        return []
    
    alternatives = []
    
    # 1. Closest match from same restaurant
    same_restaurant = _find_same_restaurant_alternative(db, original, original_vendor, user_budget)
    if same_restaurant:
        alternatives.append(same_restaurant)
    
    # 2. Best price within budget
    if user_budget:
        best_price = _find_best_price_alternative(db, original, original_vendor, user_budget, pos)
        if best_price:
            alternatives.append(best_price)
    
    # 3. Alternative restaurant with same dish
    alt_restaurant = _find_alternative_restaurant(db, original, original_vendor, pos, user_budget)
    if alt_restaurant:
        alternatives.append(alt_restaurant)
    
    # Sort by relevance
    alternatives.sort(key=lambda a: (a.similarity_score * 0.6 + a.price_match * 0.4), reverse=True)
    
    return alternatives[:limit]


def _find_same_restaurant_alternative(
    db: Session,
    original: MenuItem,
    vendor: Vendor,
    budget: Decimal | None
) -> Alternative | None:
    """Find similar meal from the same restaurant."""
    # Get other items from same vendor, same category
    items = db.execute(
        cat.public_items_query()
        .where(MenuItem.vendor_id == vendor.id)
        .where(MenuItem.category_id == original.category_id)
        .where(MenuItem.id != original.id)
        .where(MenuItem.is_available == True)
        .limit(10)
    ).scalars().all()
    
    if not items:
        return None
    
    # Find most similar by price and name similarity
    best_item = None
    best_score = 0
    
    for item in items:
        if budget and item.price > budget:
            continue
        
        # Price similarity (closer is better)
        price_diff = abs(float(item.price - original.price))
        price_score = max(0, 1 - price_diff / max(float(original.price), 1))
        
        # Name similarity (simple overlap)
        original_words = set(original.name.lower().split())
        item_words = set(item.name.lower().split())
        name_overlap = len(original_words & item_words) / max(len(original_words), 1)
        
        score = (price_score * 0.4 + name_overlap * 0.6)
        
        if score > best_score:
            best_score = score
            best_item = item
    
    if not best_item:
        return None
    
    favs = set()
    item_out = cat.item_out(best_item, pos=None, fav_items=favs)
    
    return Alternative(
        type="same_restaurant",
        item=item_out.model_dump(),
        vendor={"id": vendor.id, "name": vendor.name, "area": vendor.area},
        reason=f"Similar meal from {vendor.name}",
        price_match=best_score,
        similarity_score=best_score
    )


def _find_best_price_alternative(
    db: Session,
    original: MenuItem,
    vendor: Vendor,
    budget: Decimal,
    pos: tuple[float, float] | None
) -> Alternative | None:
    """Find comparable meal within budget from any restaurant."""
    # Get items in same category within budget
    items = db.execute(
        cat.public_items_query()
        .where(MenuItem.category_id == original.category_id)
        .where(MenuItem.price <= budget)
        .where(MenuItem.is_available == True)
        .limit(20)
    ).scalars().all()
    
    if not items:
        return None
    
    # Find best value (lowest price that's still good quality)
    best_item = min(items, key=lambda i: i.price)
    
    best_vendor = db.get(Vendor, best_item.vendor_id)
    if not best_vendor:
        return None
    
    favs = set()
    item_out = cat.item_out(best_item, pos, favs)
    
    price_match = 1.0 if best_item.price <= budget else max(0, 1 - float(best_item.price - budget) / float(budget))
    
    return Alternative(
        type="best_price",
        item=item_out.model_dump(),
        vendor={"id": best_vendor.id, "name": best_vendor.name, "area": best_vendor.area},
        reason=f"Best price within your budget from {best_vendor.name}",
        price_match=price_match,
        similarity_score=0.7  # Same category assumption
    )


def _find_alternative_restaurant(
    db: Session,
    original: MenuItem,
    original_vendor: Vendor,
    pos: tuple[float, float] | None,
    budget: Decimal | None
) -> Alternative | None:
    """Find the same dish from a different restaurant."""
    # Get items with similar name from other vendors
    original_words = set(original.name.lower().split())
    
    items = db.execute(
        cat.public_items_query()
        .where(MenuItem.vendor_id != original_vendor.id)
        .where(MenuItem.is_available == True)
        .limit(50)
    ).scalars().all()
    
    best_item = None
    best_score = 0
    best_vendor = None
    
    for item in items:
        if budget and item.price > budget:
            continue
        
        # Name similarity
        item_words = set(item.name.lower().split())
        name_overlap = len(original_words & item_words) / max(len(original_words), 1)
        
        if name_overlap > 0.3:  # At least 30% name similarity
            if name_overlap > best_score:
                best_score = name_overlap
                best_item = item
                best_vendor = db.get(Vendor, item.vendor_id)
    
    if not best_item or not best_vendor:
        return None
    
    favs = set()
    item_out = cat.item_out(best_item, pos, favs)
    
    return Alternative(
        type="alternative_restaurant",
        item=item_out.model_dump(),
        vendor={"id": best_vendor.id, "name": best_vendor.name, "area": best_vendor.area},
        reason=f"Same dish available at {best_vendor.name}",
        price_match=1.0 if best_item.price <= (budget or 9999) else 0.8,
        similarity_score=best_score
    )


def format_alternatives(alternatives: list[Alternative], original_name: str) -> str:
    """Format alternatives into a conversational response."""
    if not alternatives:
        return f"Unfortunately, {original_name} is currently unavailable and I couldn't find suitable alternatives."
    
    response = f"{original_name} is currently unavailable. Here are some alternatives:\n\n"
    
    for i, alt in enumerate(alternatives, 1):
        response += f"{i}. {alt['item']['name']} from {alt['vendor']['name']} - {cedis(alt['item']['price'])}\n"
        response += f"   {alt['reason']}\n"
    
    response += "\nWould you like me to add any of these to your cart instead?"
    
    return response
