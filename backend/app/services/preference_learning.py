"""Enhanced preference learning - tracks detailed user behavior for better personalization."""
from collections import Counter
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models.ai import CustomerPreference
from app.models.catalog import MenuItem
from app.models.orders import Order, OrderStatus
from app.models.user import User


def learn_from_order(db: Session, order: Order) -> None:
    """Learn detailed preferences from a completed order."""
    if order.status not in (OrderStatus.DELIVERED, OrderStatus.COLLECTED):
        return
    
    pref = db.get(CustomerPreference, order.customer_id)
    if not pref:
        pref = CustomerPreference(user_id=order.customer_id)
        db.add(pref)
    
    # Update last order time
    pref.last_order_at = utcnow()
    
    # Track order hour
    order_hour = order.created_at.hour
    if pref.typical_order_hour is None:
        pref.typical_order_hour = order_hour
    else:
        # Simple moving average for hour
        pref.typical_order_hour = int((pref.typical_order_hour + order_hour) / 2)
    
    # Learn from order items
    order_items = db.execute(
        select(MenuItem)
        .join(Order, Order.id == order.id)
        .where(MenuItem.id == order.menu_item_id)  # This is simplified - actual schema may differ
    ).scalars().all()
    
    # Track favorite dishes
    if not pref.favorite_dishes:
        pref.favorite_dishes = []
    
    for item in order_items:
        # Add to favorites if not already there
        if item.id not in pref.favorite_dishes:
            pref.favorite_dishes.append(item.id)
            # Keep only top 20 favorites
            if len(pref.favorite_dishes) > 20:
                pref.favorite_dishes = pref.favorite_dishes[-20:]
    
    # Learn protein preference
    protein_keywords = {
        "chicken": "chicken",
        "fish": "fish", "tilapia": "fish", "red fish": "fish",
        "beef": "beef", "meat": "beef",
        "goat": "goat",
        "pork": "pork",
        "vegetarian": "vegetarian", "vegan": "vegetarian"
    }
    
    for item in order_items:
        item_name_lower = item.name.lower()
        for keyword, protein in protein_keywords.items():
            if keyword in item_name_lower:
                if pref.protein_preference != protein:
                    pref.protein_preference = protein
                break
    
    # Learn delivery speed preference
    if order.fulfillment == "DELIVERY":
        delivered_time = order.delivered_at or order.updated_at
        if delivered_time and order.created_at:
            delivery_minutes = (delivered_time - order.created_at).total_seconds() / 60
            # If consistently fast delivery, set preference
            if delivery_minutes < 30:
                pref.prefers_fast_delivery = True
    
    db.commit()


def get_user_profile_summary(db: Session, user: User) -> dict:
    """Get a summary of what we've learned about the user."""
    pref = db.get(CustomerPreference, user.id)
    if not pref:
        return {"message": "I haven't learned your preferences yet. Order some food and I'll start learning!"}
    
    summary = []
    
    if pref.favorite_dishes:
        favorite_count = len(pref.favorite_dishes)
        summary.append(f"You've ordered {favorite_count} different dish{'es' if favorite_count != 1 else ''}.")
    
    if pref.protein_preference:
        summary.append(f"You seem to prefer {pref.protein_preference}.")
    
    if pref.typical_order_hour:
        hour = pref.typical_order_hour
        if 6 <= hour < 12:
            time_desc = "morning"
        elif 12 <= hour < 17:
            time_desc = "afternoon"
        elif 17 <= hour < 21:
            time_desc = "evening"
        else:
            time_desc = "night"
        summary.append(f"You usually order around {time_desc}.")
    
    if pref.prefers_fast_delivery:
        summary.append("You prefer restaurants that deliver quickly.")
    
    if pref.avg_budget:
        summary.append(f"Your typical budget is around GH₵{pref.avg_budget:.0f}.")
    
    if pref.spice_preference:
        summary.append(f"You prefer {pref.spice_preference} spice level.")
    
    if pref.top_categories:
        summary.append(f"Your favorite categories: {', '.join(pref.top_categories[:3])}.")
    
    if not summary:
        return {"message": "I haven't learned your preferences yet. Order some food and I'll start learning!"}
    
    return {"message": " ".join(summary), "preferences": {
        "protein_preference": pref.protein_preference,
        "typical_order_hour": pref.typical_order_hour,
        "prefers_fast_delivery": pref.prefers_fast_delivery,
        "avg_budget": pref.avg_budget,
        "spice_preference": pref.spice_preference,
        "top_categories": pref.top_categories,
        "favorite_dishes_count": len(pref.favorite_dishes) if pref.favorite_dishes else 0
    }}


def suggest_based_on_profile(db: Session, user: User, pos) -> str:
    """Suggest meals based on learned profile."""
    pref = db.get(CustomerPreference, user.id)
    if not pref:
        return "I don't know your preferences yet. Try ordering a few meals and I'll learn what you like!"
    
    suggestions = []
    
    if pref.protein_preference:
        suggestions.append(f"I noticed you like {pref.protein_preference}. Here are some {pref.protein_preference} options:")
    
    if pref.avg_budget:
        suggestions.append(f"Based on your typical budget of GH₵{pref.avg_budget:.0f}, here are some options:")
    
    if pref.spice_preference:
        suggestions.append(f"Since you prefer {pref.spice_preference} spice, here are some options:")
    
    if pref.top_categories:
        suggestions.append(f"You've ordered from {pref.top_categories[0]} a lot. Here are more options:")
    
    if not suggestions:
        return "I'd be happy to suggest something! Tell me what you're in the mood for, your budget, or any dietary preferences."
    
    return " ".join(suggestions)
