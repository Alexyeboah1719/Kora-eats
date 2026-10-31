"""The food assistant. Understands a request with nl_query, then answers ONLY from database rows.
It never invents vendors, meals, prices, availability or delivery times: it states prep time (a stored
value) and distance (computed) and nothing it can't back up."""
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import cedis, cedis_compact
from app.models.ai import AiConversation, AiMessage, CustomerPreference
from app.models.catalog import Category, MenuItem
from app.models.orders import Order, OrderStatus
from app.models.user import User
from app.services import catalog_service as cat
from app.services import nl_query, order_events
from app.services import ai_memory

HELP = ("I can find food from vendors on KORA EATS. Try: \"I have GH₵40 and want something filling\", \"spicy food near me\" "
        "or \"chicken and rice under GH₵50\". I understand budget, food type, spice level, distance, rating and prep time.")

# Role-specific question patterns
VENDOR_KEYWORDS = ["vendor orders", "my restaurant", "my menu", "restaurant orders", "my sales", "my earnings", "my revenue", "vendor dashboard", "manage orders", "my business"]
ADMIN_KEYWORDS = ["admin dashboard", "platform revenue", "total orders", "platform analytics", "all vendors", "all riders", "system stats", "platform stats"]
RIDER_KEYWORDS = ["my deliveries", "rider orders", "my delivery orders", "rider earnings", "my riding stats", "delivery history"]
CUSTOMER_KEYWORDS = ["my orders", "my history", "my past orders", "order history", "my account"]

# Conversational responses
GREETINGS = {
    "hi": "Hello! I'm your food assistant. I can help you find the perfect meal. What are you craving today?",
    "hello": "Hey there! Ready to find something delicious? Tell me what you're in the mood for!",
    "hey": "Hi! I'm here to help you discover great food. What can I find for you?",
    "good morning": "Good morning! Start your day right. What would you like to eat?",
    "good afternoon": "Good afternoon! Time for a tasty meal. What are you thinking?",
    "good evening": "Good evening! Hungry? Let me find you something great.",
    "how are you": "I'm doing great, thanks for asking! I'm ready to help you find delicious food. What can I do for you?",
    "thanks": "You're welcome! Let me know if you need anything else.",
    "thank you": "Happy to help! Feel free to ask if you want more recommendations.",
    "bye": "Goodbye! Enjoy your meal and come back anytime!",
    "goodbye": "Take care! Hope you find something delicious. See you soon!",
}

SMALL_TALK = {
    "what can you do": "I can help you find food based on your budget, cravings, location, and preferences. Just tell me what you're in the mood for, how much you want to spend, or any specific requirements like spicy, vegetarian, or quick delivery!",
    "who are you": "I'm the KORA EATS food assistant! I help you discover the best meals from vendors near you. I use real menus, prices, and ratings to give you honest recommendations.",
    "help": "I'm here to help you find food! Try asking me things like:\n• \"I have GH₵30 and want something filling\"\n• \"Spicy chicken near me\"\n• \"Best rated jollof under GH₵50\"\n• \"Quick vegetarian options\"\n• \"What can I afford for GH₵40?\"",
}


def _handle_conversation(text: str) -> tuple[str | None, bool]:
    """Handle greetings and small talk. Returns (response, should_skip_search)."""
    t = text.lower().strip()

    # Check for greetings
    for greeting, response in GREETINGS.items():
        if greeting in t:
            return response, True

    # Check for small talk
    for question, response in SMALL_TALK.items():
        if question in t:
            return response, True

    return None, False


def _vocab(db: Session) -> set[str]:
    visible = cat.public_vendors_query().subquery()
    rows = db.execute(select(MenuItem.name, MenuItem.tags).where(MenuItem.vendor_id.in_(select(visible.c.id)))).all()
    cats = db.execute(select(Category.name, Category.slug).where(Category.is_active)).all()
    return nl_query.build_vocab([(a, b) for a, b in rows] + [(a, "") for a, _ in cats])


def run_query(db: Session, user: User | None, p: nl_query.Parsed, pos) -> list:
    """Applies a parsed request with the normal catalog search (so availability/open hours are respected)."""
    max_km = p.max_km if pos else None                    # distance can only be applied if we know where they are
    items = cat.search_items(
        db, user, pos, q=" ".join(p.terms) or None, max_price=p.max_price, spicy=p.spicy, min_rating=p.min_rating,
        max_prep=p.max_prep, max_km=max_km, sort="distance" if (p.sort == "distance" and pos) else
        ("price_asc" if p.sort == "price_asc" else "popular"), limit=60, open_only=p.open_only)
    if p.filling:
        items = [i for i in items if "filling" in i.tags]
    if p.vegetarian:
        items = [i for i in items if "vegetarian" in i.tags]
    if p.filling:    # most substantial first: pricier dishes in budget usually mean bigger meals
        items.sort(key=lambda i: -i.price)

    # Enhanced: Handle combo requests
    if p.want_combo and p.max_price:
        items = _suggest_combos(items, p.max_price)

    # Enhanced: Handle prediction requests
    if p.want_prediction and user:
        items = _personalize_suggestions(db, user, items)

    return items


def _line(i) -> str:
    bits = [cedis(i.price), f"from {i.vendor_name}"]
    if i.distance_km is not None:
        bits.append(f"{i.distance_km:.1f} km away")
    bits.append(f"{i.prep_minutes} min prep")
    return f"{i.name}: " + ", ".join(bits)


def _budget_breakdown(items: list, budget: float, pos) -> str | None:
    """Calculate what users can afford including delivery vs pickup."""
    if not items:
        return None
    # Simple delivery fee estimation (typically GH₵5-15)
    est_delivery = 10.0
    pickup_total = sum(i.price for i in items[:3])  # Top 3 items for pickup
    delivery_total = pickup_total + est_delivery

    if pickup_total <= budget:
        return f"With pickup, you could get up to 3 items (~{cedis_compact(pickup_total)}). With delivery, budget would be ~{cedis_compact(delivery_total)}."
    elif delivery_total <= budget:
        return f"You can afford delivery for the top match. Total with delivery: ~{cedis_compact(delivery_total)}."
    else:
        cheapest = min(items, key=lambda i: i.price)
        return f"For {cedis_compact(budget)}, I recommend {cheapest.name} ({cedis_compact(cheapest.price)}). Consider pickup to save on delivery."


def _rating_insight(items: list) -> str | None:
    """Provide insights based on vendor ratings."""
    if not items:
        return None
    high_rated = [i for i in items if i.rating_avg and i.rating_avg >= 4.5]
    if high_rated:
        best = max(high_rated, key=lambda i: i.rating_avg or 0)
        return f"Top pick by rating: {best.name} from {best.vendor_name} ({best.rating_avg:.1f}⭐)."
    return None


def _suggest_combos(items: list, budget: float) -> list:
    """Suggest combinations of items within budget."""
    if not items or len(items) < 2:
        return items

    # Group by vendor to create realistic combos
    vendor_items = {}
    for item in items:
        if item.vendor_id not in vendor_items:
            vendor_items[item.vendor_id] = []
        vendor_items[item.vendor_id].append(item)

    combos = []
    for vendor_id, v_items in vendor_items.items():
        if len(v_items) >= 2:
            # Try to create 2-item combos within budget
            for i in range(len(v_items)):
                for j in range(i + 1, min(i + 4, len(v_items))):
                    combo_price = v_items[i].price + v_items[j].price
                    if combo_price <= budget:
                        # Mark as combo suggestion
                        v_items[i].combo_suggestion = True
                        v_items[j].combo_suggestion = True
                        combos.extend([v_items[i], v_items[j]])
                        break
                if len(combos) >= 6:
                    break
        if len(combos) >= 6:
            break

    return combos if combos else items[:6]


def _personalize_suggestions(db: Session, user: User, items: list) -> list:
    """Personalize suggestions based on user's order history and preferences."""
    if not items:
        return items

    # Get user's preferences
    pref = db.get(CustomerPreference, user.id)
    if not pref:
        return items

    # Boost items matching user's preferred categories
    if pref.top_categories:
        category_boost = {}
        for cat_name in pref.top_categories:
            category_boost[cat_name.lower()] = 2.0

        for item in items:
            if item.category_name and item.category_name.lower() in category_boost:
                item.personal_score = (item.rating_avg or 3.5) * category_boost[item.category_name.lower()]
            else:
                item.personal_score = item.rating_avg or 3.5

        items.sort(key=lambda i: -(i.personal_score or 0))

    return items[:10]


def answer(db: Session, user: User | None, text: str, pos) -> dict:
    # Handle conversational responses first
    conv_response, skip_search = _handle_conversation(text)
    if conv_response:
        return {"reply": conv_response, "items": [], "filters": [], "parsed": None}

    # Check for role-specific questions that require authentication
    t_lower = text.lower()
    
    # Vendor-specific questions
    if any(kw in t_lower for kw in VENDOR_KEYWORDS):
        if not user:
            return {"reply": "I can help you check your restaurant's orders, sales, and earnings. Please log in to your vendor account so I can securely retrieve your business data.", "items": [], "filters": [], "parsed": None, "auth_required": True, "required_role": "VENDOR"}
        elif user.role.value != "VENDOR":
            return {"reply": "To check restaurant orders and sales, you need to log in with a vendor account. Please log out and log in with your vendor credentials.", "items": [], "filters": [], "parsed": None, "auth_required": True, "required_role": "VENDOR"}
    
    # Admin-specific questions
    if any(kw in t_lower for kw in ADMIN_KEYWORDS):
        if not user:
            return {"reply": "I can help you check platform-wide analytics, total orders, and system statistics. Please log in to your admin account so I can securely retrieve platform data.", "items": [], "filters": [], "parsed": None, "auth_required": True, "required_role": "ADMIN"}
        elif user.role.value != "ADMIN":
            return {"reply": "To check platform analytics and system statistics, you need to log in with an admin account. Please log out and log in with your admin credentials.", "items": [], "filters": [], "parsed": None, "auth_required": True, "required_role": "ADMIN"}
    
    # Rider-specific questions
    if any(kw in t_lower for kw in RIDER_KEYWORDS):
        if not user:
            return {"reply": "I can help you check your delivery history, earnings, and riding statistics. Please log in to your rider account so I can securely retrieve your delivery data.", "items": [], "filters": [], "parsed": None, "auth_required": True, "required_role": "RIDER"}
        elif user.role.value != "RIDER":
            return {"reply": "To check delivery orders and earnings, you need to log in with a rider account. Please log out and log in with your rider credentials.", "items": [], "filters": [], "parsed": None, "auth_required": True, "required_role": "RIDER"}
    
    # Customer order history
    if any(kw in t_lower for kw in CUSTOMER_KEYWORDS):
        if not user:
            return {"reply": "I can help you check your order history and account details. Please log in to your customer account so I can securely retrieve your order data.", "items": [], "filters": [], "parsed": None, "auth_required": True, "required_role": "CUSTOMER"}
    
    # General earnings/wallet questions
    if any(kw in t_lower for kw in ["earn", "earning", "revenue", "sales", "wallet", "payout"]):
        if not user:
            return {"reply": "I can help you check your account information including earnings and wallet balance. Please log in to your account so I can securely retrieve your details.", "items": [], "filters": [], "parsed": None, "auth_required": True}

    p = nl_query.parse(text, _vocab(db))
    if not p.understood:
        return {"reply": HELP, "items": [], "filters": [], "parsed": None}
    notes = []
    if p.near_me and not pos:
        notes.append("Set your delivery location (top of the home page) so I can find vendors near you.")
    items = run_query(db, user, p, pos)
    filters = p.explain()

    # Enhanced: Add budget breakdown if budget specified
    if p.max_price is not None and items:
        budget_breakdown = _budget_breakdown(items[:10], p.max_price, pos)
        if budget_breakdown:
            notes.append(budget_breakdown)

    # Enhanced: Add rating-based insights
    if p.min_rating is not None and items:
        rating_insight = _rating_insight(items[:10])
        if rating_insight:
            notes.append(rating_insight)

    if items:
        top = items[:6]
        reply = f"I found {len(items)} option{'s' if len(items) != 1 else ''}" + (f" (showing the best {len(top)})" if len(items) > len(top) else "") + ". "
        reply += "Best match: " + _line(top[0]) + "."
        # Enhanced: Add follow-up suggestions
        if p.max_price:
            reply += f" Want to see what else you can get for {cedis_compact(p.max_price)}? Try 'show me combos' or 'what about pickup?'."
        return {"reply": " ".join([reply] + notes), "items": top, "filters": filters, "parsed": p}
    # nothing matched: say exactly why, using real numbers from the database
    reply = "I couldn't find anything matching " + (", ".join(filters) or "that") + "."
    if p.max_price is not None:
        relaxed = nl_query.Parsed(**{**p.__dict__, "max_price": None})
        pricier = run_query(db, user, relaxed, pos)
        if pricier:
            cheapest = min(pricier, key=lambda i: i.price)
            reply += f" The cheapest match without the budget limit is {_line(cheapest)}."
    elif p.max_km is not None and pos:
        farther = run_query(db, user, nl_query.Parsed(**{**p.__dict__, "max_km": None, "near_me": False}), pos)
        if farther:
            reply += f" The nearest match is {_line(min(farther, key=lambda i: i.distance_km or 9999))}."
    return {"reply": " ".join([reply] + notes), "items": [], "filters": filters, "parsed": p}


# --------------------------------------------------------------------------- conversations + preferences
def chat(db: Session, user: User | None, text: str, pos, conversation_id: int | None) -> dict:
    # Resolve follow-up questions using conversation memory
    context = ai_memory.conversation_memory.get_context(conversation_id, db) if conversation_id else {}
    follow_up = ai_memory.conversation_memory.resolve_follow_up(text, context)
    
    # If follow-up resolved, adjust the query
    if follow_up["resolved"]:
        # Adjust period-based queries
        if follow_up["period_changed"] and context.get("last_topic"):
            text = f"{context['last_topic']} for previous period"
        elif follow_up["reference"] == "previous filters" and context["recent_queries"]:
            last_filters = context["recent_queries"][-1].get("filters", [])
            if last_filters:
                text = f"{text} (like before: {', '.join(last_filters)})"
    
    result = answer(db, user, text, pos)
    conv = None
    if user is not None:
        if conversation_id:
            conv = db.scalar(select(AiConversation).where(AiConversation.id == conversation_id, AiConversation.user_id == user.id))
            if conv is None:
                raise HTTPException(404, {"code": "not_found", "message": "Conversation not found."})
        else:
            conv = AiConversation(user_id=user.id, title=text[:80])
            db.add(conv)
            db.flush()
        db.add(AiMessage(conversation_id=conv.id, role="user", content=text))
        db.add(AiMessage(conversation_id=conv.id, role="assistant", content=result["reply"],
                         item_ids=[i.id for i in result["items"]], filters=result["filters"]))
        learn(db, user, result["parsed"])
        
        # Update conversation memory
        ai_memory.conversation_memory.update_context(
            conv.id, text, result["reply"], result["filters"], [i.id for i in result["items"]], db
        )
        
        db.commit()
    return {"conversation_id": conv.id if conv else None, "reply": result["reply"],
            "items": [i.model_dump() for i in result["items"]], "filters": result["filters"]}


def learn(db: Session, user: User, p: nl_query.Parsed | None) -> None:
    if p is None:
        return
    pref = db.get(CustomerPreference, user.id) or CustomerPreference(user_id=user.id, budget_samples=0)
    db.add(pref)
    if p.spicy is not None:
        pref.spicy_preference = p.spicy
    if p.max_price is not None:
        n = pref.budget_samples or 0
        pref.avg_budget = ((pref.avg_budget or 0) * n + p.max_price) / (n + 1)
        pref.budget_samples = n + 1


def history(db: Session, user: User, conversation_id: int) -> list[dict]:
    conv = db.scalar(select(AiConversation).where(AiConversation.id == conversation_id, AiConversation.user_id == user.id))
    if conv is None:
        raise HTTPException(404, {"code": "not_found", "message": "Conversation not found."})
    rows = db.scalars(select(AiMessage).where(AiMessage.conversation_id == conv.id).order_by(AiMessage.id)).all()
    return [{"role": m.role, "content": m.content, "item_ids": m.item_ids or [], "filters": m.filters or []} for m in rows]


@order_events.subscribe
def learn_from_delivery(db, order, old, new, actor_role, background):
    """When an order is delivered, remember which categories this customer actually orders."""
    done = (OrderStatus.DELIVERED, OrderStatus.COLLECTED)
    if new not in done or old in done:
        return
    rows = db.execute(select(Category.name, func.count()).join(MenuItem, MenuItem.category_id == Category.id)
                      .join(Order, Order.customer_id == order.customer_id)
                      .where(Order.status.in_(done), MenuItem.vendor_id == Order.vendor_id)
                      .group_by(Category.name).order_by(func.count().desc()).limit(3)).all()
    pref = db.get(CustomerPreference, order.customer_id) or CustomerPreference(user_id=order.customer_id, budget_samples=0)
    db.add(pref)
    pref.top_categories = [r[0] for r in rows]
