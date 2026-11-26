"""The food assistant. Understands a request with nl_query, then answers ONLY from database rows.
It never invents vendors, meals, prices, availability or delivery times: it states prep time (a stored
value) and distance (computed) and nothing it can't back up."""
import re
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import cedis
from app.models.ai import AiConversation, AiMessage, CustomerPreference
from app.models.catalog import Category, MenuItem
from app.models.orders import Order, OrderStatus
from app.models.user import User
from app.services import catalog_service as cat
from app.services import nl_query, order_events, food_taxonomy as tx
from app.services.meal_customization import parse_customization, explain_customization
from app.services.ingredient_qa import answer_ingredient_question
from app.services.alternative_finder import find_alternatives, format_alternatives
from app.services.preference_learning import get_user_profile_summary, suggest_based_on_profile

# Local copy of STOP words for fallback search
STOP = {"and", "the", "with", "for", "want", "need", "get", "give", "show", "something", "some", "any", "food", "meal", "meals",
        "eat", "eating", "have", "can", "you", "please", "near", "nearby", "under", "below", "than", "less", "over", "from",
        "that", "this", "now", "today", "me", "my", "i", "im", "ghs", "cedis", "cedi", "like", "good", "best", "find", "order", "plate",
        "hi", "hello", "hey", "how", "are", "doing", "up", "whats", "what's", "morning", "afternoon", "evening", "greetings",
        "suggest", "suggestion", "time", "budget"}

HELP = ("I can help you find the perfect meal from our vendors! Try:\n"
        "- \"I have GH₵40 and want something filling\"\n"
        "- \"Spicy food near me\"\n"
        "- \"Chicken and rice under GH₵50\"\n"
        "- \"I want waakye with extra spaghetti, less pepper\"\n"
        "- \"Show me vegetarian options\"\n"
        "- \"Quick meals within 15 minutes\"\n\n"
        "I understand budget, food type, spice level, distance, rating, prep time, and customizations. Just tell me what you're craving!")

# Currency conversion rates (GHS as base)
CURRENCY_RATES = {
    "ghs": 1.0,
    "ghc": 1.0,
    "cedi": 1.0,
    "cedis": 1.0,
    "gh₵": 1.0,
    "usd": 15.5,
    "dollar": 15.5,
    "dollars": 15.5,
    "$": 15.5,
    "eur": 16.8,
    "euro": 16.8,
    "euros": 16.8,
    "€": 16.8,
    "gbp": 19.6,
    "pound": 19.6,
    "pounds": 19.6,
    "£": 19.6,
}


def detect_greeting(text: str) -> str | None:
    """Detect common greetings and return a friendly response."""
    text_lower = text.lower().strip()
    greetings = {
        r"^(hi|hello|hey|hi there|hello there|hey there|greetings|good morning|good afternoon|good evening|good day)[.!]*$": None,
        r"^(how are you|how are you doing|how's it going|what's up|whats up|wassup|sup|how do you do)[.!]*$": None,
        r"^(morning|afternoon|evening)[.!]*$": None,
    }
    
    for pattern, _ in greetings.items():
        if re.match(pattern, text_lower):
            hour = datetime.now().hour
            if hour < 12:
                return "Good morning! I'm here to help you find the perfect meal. What are you craving today?"
            elif hour < 17:
                return "Good afternoon! Hungry? Tell me what you're in the mood for, and I'll find you something great."
            else:
                return "Good evening! Looking for dinner? I can help you find the perfect meal. What's on your mind?"
    
    # Check for conversational greetings
    if re.search(r"\b(hi|hello|hey)\b", text_lower) and len(text_lower.split()) <= 5:
        hour = datetime.now().hour
        if hour < 12:
            return "Good morning! I'm here to help you find the perfect meal. What are you craving today?"
        elif hour < 17:
            return "Good afternoon! Hungry? Tell me what you're in the mood for, and I'll find you something great."
        else:
            return "Good evening! Looking for dinner? I can help you find the perfect meal. What's on your mind?"
    
    return None


def convert_to_ghs(text: str) -> tuple[str, float | None]:
    """Extract and convert currency amount to GHS. Returns (text_without_currency, amount_in_ghs)."""
    # Pattern: number followed by currency symbol or name
    patterns = [
        r"(\d+(?:\.\d+)?)\s*([$\€£])",  # $10, €20, £5
        r"([$\€£])\s*(\d+(?:\.\d+)?)",  # $10, €20, £5
        r"(\d+(?:\.\d+)?)\s*(usd|eur|gbp|dollar|dollars|euro|euros|pound|pounds)\b",
        r"(\d+(?:\.\d+)?)\s*(ghs|ghc|cedi|cedis|gh₵)\b",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # Extract number and currency
            if match.groups()[0].isdigit() or (match.groups()[0].replace('.', '', 1).isdigit()):
                num_str = match.groups()[0]
                curr_str = match.groups()[1].lower()
            else:
                num_str = match.groups()[1]
                curr_str = match.groups()[0].lower()
            
            amount = float(num_str)
            rate = CURRENCY_RATES.get(curr_str, 1.0)
            ghs_amount = amount * rate
            
            # Remove the currency pattern from text
            clean_text = re.sub(pattern, f"{ghs_amount:.2f} GHS", text, flags=re.IGNORECASE)
            return clean_text, ghs_amount
    
    return text, None


def evaluate_math(text: str) -> tuple[str, float | None]:
    """Evaluate simple math expressions in text (e.g., "20 + 10", "50 - 5")."""
    # Pattern for simple math: number operator number
    math_pattern = r"(\d+(?:\.\d+)?)\s*([\+\-\*\/])\s*(\d+(?:\.\d+)?)"
    matches = list(re.finditer(math_pattern, text))
    
    if not matches:
        return text, None
    
    result_text = text
    total = None
    
    for match in reversed(matches):  # Process in reverse to maintain positions
        num1 = float(match.group(1))
        op = match.group(2)
        num2 = float(match.group(3))
        
        if op == '+':
            result = num1 + num2
        elif op == '-':
            result = num1 - num2
        elif op == '*':
            result = num1 * num2
        elif op == '/':
            result = num1 / num2 if num2 != 0 else 0
        else:
            continue
        
        total = result
        result_text = result_text[:match.start()] + f"{result:.2f}" + result_text[match.end():]
    
    return result_text, total


def enhance_response(reply: str, items: list, budget: float | None = None) -> str:
    """Make the response more conversational and helpful."""
    if not items:
        return reply
    
    # Add helpful context
    if budget and len(items) > 0:
        cheapest = min(items, key=lambda i: i.price)
        if cheapest.price <= budget:
            reply += f" Great news! There are options within your budget."
    
    # Add variety suggestion
    if len(items) >= 3:
        reply += f" I found {len(items)} different options for you to choose from."
    
    # Add helpful tips based on what was found
    if any("spicy" in (i.tags or "") for i in items):
        reply += " Some of these have a nice kick to them!"
    if any("vegetarian" in (i.tags or "") for i in items):
        reply += " I've included some vegetarian options too."
    
    # Add call to action
    reply += " Would you like to see more details about any of these, or shall I help you with something else?"
    
    return reply


def get_time_context() -> str:
    """Get a simple time-based context message."""
    hour = datetime.now().hour
    if 5 <= hour < 11:
        return "Since it's morning, I've focused on breakfast options"
    elif 11 <= hour < 14:
        return "Since it's lunchtime, I've focused on lunch options"
    elif 14 <= hour < 17:
        return "Since it's afternoon, I've focused on snacks and light meals"
    elif 17 <= hour < 22:
        return "Since it's evening, I've focused on dinner options"
    else:
        return ""  # Late night - no special message


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
        ("price_asc" if p.sort == "price_asc" else "popular"), limit=60)
    if p.filling:
        items = [i for i in items if "filling" in i.tags]
    if p.vegetarian:
        items = [i for i in items if "vegetarian" in i.tags]
    if p.filling:    # most substantial first: pricier dishes in budget usually mean bigger meals
        items.sort(key=lambda i: -i.price)
    return items


def _line(i) -> str:
    bits = [cedis(i.price), f"from {i.vendor_name}"]
    if i.distance_km is not None:
        bits.append(f"{i.distance_km:.1f} km away")
    bits.append(f"{i.prep_minutes} min prep")
    return f"{i.name}: " + ", ".join(bits)


def answer(db: Session, user: User | None, text: str, pos) -> dict:
    # Check for greetings first
    greeting_response = detect_greeting(text)
    if greeting_response:
        # Add personalized greeting if we know the user
        if user:
            profile = get_user_profile_summary(db, user)
            if "preferences" in profile:
                greeting_response += f" {suggest_based_on_profile(db, user, pos)}"
        return {"reply": greeting_response, "items": [], "filters": [], "parsed": None}
    
    # Check for conversational follow-ups
    text_lower = text.lower().strip()
    if text_lower in ("yes", "yeah", "yep", "sure", "ok", "okay", "please", "thanks", "thank you"):
        return {"reply": "Great! What would you like to order? You can tell me about your budget, what you're craving, or any dietary preferences. I can also help with meal customizations!", "items": [], "filters": [], "parsed": None}
    
    if text_lower in ("no", "no thanks", "nope"):
        return {"reply": "No problem! Let me know if you'd like to try a different search or if you have any other questions.", "items": [], "filters": [], "parsed": None}
    
    if re.search(r"\b(show me|more|tell me|what else|anything else)\b", text_lower):
        if user:
            return {"reply": suggest_based_on_profile(db, user, pos), "items": [], "filters": [], "parsed": None}
        return {"reply": "I'd be happy to help! Try telling me what you're in the mood for, your budget, or any specific dietary needs. For example: \"I want chicken under GH₵50\" or \"spicy vegetarian options\". You can also ask me to customize your meal!", "items": [], "filters": [], "parsed": None}
    
    # Check for profile questions
    if re.search(r"\b(what do you know|my preferences|what have you learned|profile|recommend)\b", text_lower):
        if user:
            profile = get_user_profile_summary(db, user)
            return {"reply": profile["message"], "items": [], "filters": [], "parsed": None}
        return {"reply": "I don't know your preferences yet. Sign in and order some food, and I'll start learning what you like!", "items": [], "filters": [], "parsed": None}
    
    # Check for EXPLICIT ingredient questions (with "what is", "describe", "tell me about")
    # Only answer these if they're clearly asking for information, not searching for food
    if re.search(r"\b(what is|describe|tell me about|explain|definition of)\b", text_lower):
        ingredient_answer = answer_ingredient_question(text, db)
        if ingredient_answer:
            return {"reply": ingredient_answer, "items": [], "filters": [], "parsed": None}
    
    # Check for meal customization requests
    if re.search(r"\b(extra|less|no|without|add|remove|spicy|mild|portion|side)\b", text_lower):
        custom = parse_customization(text)
        if any([custom.portion, custom.spice_level, custom.add_ingredients, 
                custom.remove_ingredients, custom.extra_sides, custom.remove_sides]):
            explanation = explain_customization(custom)
            return {"reply": explanation + " I've noted your preferences. Would you like me to find meals that match these, or add this customization to a specific dish?", "items": [], "filters": [], "parsed": None, "customization": custom.to_instruction()}
    
    # PRIMARY: Try to find food matching the request
    # Convert currencies and evaluate math
    processed_text, converted_amount = convert_to_ghs(text)
    processed_text, math_result = evaluate_math(processed_text)
    
    # Parse the processed text
    p = nl_query.parse(processed_text, _vocab(db))
    
    # If currency was converted, mention it
    conversion_note = ""
    if converted_amount and p.max_price is None:
        # If we found a currency but it wasn't parsed as budget, try to extract it
        p.max_price = converted_amount
        conversion_note = f" (I converted that to GHS for you)"
    
    # If the parser didn't understand anything, try original text
    if not p.understood:
        p = nl_query.parse(text, _vocab(db))
    
    # Always try to search even if parsing is weak - extract food terms
    if not p.understood or not p.terms:
        # Extract any food words from the text as search terms
        food_words = [w for w in text_lower.split() if len(w) >= 3 and w not in STOP]
        if food_words:
            p.terms = food_words[:5]  # Use up to 5 words as search terms
    
    notes = []
    if conversion_note:
        notes.append(conversion_note)
    if p.near_me and not pos:
        notes.append("Set your delivery location (top of the home page) so I can find vendors near you.")
    
    items = run_query(db, user, p, pos)
    filters = p.explain()
    
    # Add time context if user didn't specify a particular food
    time_context = ""
    if not p.terms or len(p.terms) == 0:
        time_context = get_time_context()
        if time_context:
            time_context += " that are suitable for this time of day."
    
    if items:
        top = items[:6]
        reply = f"I found {len(items)} option{'s' if len(items) != 1 else ''}" + (f" (showing the best {len(top)})" if len(items) > len(top) else "") + f"{time_context}. "
        reply += "Best match: " + _line(top[0]) + "."
        reply = enhance_response(reply, top, p.max_price)
        reply += " Would you like me to add any of these to your cart, or would you like to customize one of them?"
        return {"reply": " ".join([reply] + notes), "items": top, "filters": filters, "parsed": p}
    
    # If no items found, provide helpful suggestions
    reply = "I couldn't find anything matching " + (", ".join(filters) or "that") + "."
    if p.max_price is not None:
        relaxed = nl_query.Parsed(**{**p.__dict__, "max_price": None})
        pricier = run_query(db, user, relaxed, pos)
        if pricier:
            cheapest = min(pricier, key=lambda i: i.price)
            reply += f" The cheapest match without the budget limit is {_line(cheapest)}."
            reply += f" Would you like to increase your budget to {cedis(cheapest.price)}?"
    elif p.max_km is not None and pos:
        farther = run_query(db, user, nl_query.Parsed(**{**p.__dict__, "max_km": None, "near_me": False}), pos)
        if farther:
            reply += f" The nearest match is {_line(min(farther, key=lambda i: i.distance_km or 9999))}."
            reply += " Would you like to expand your search radius?"
    else:
        # General suggestions when nothing matches
        reply += " Try being more specific, or ask me what's available. For example: \"show me spicy food under GH₵50\" or \"what vegetarian options do you have?\""
    return {"reply": " ".join([reply] + notes), "items": [], "filters": filters, "parsed": p}


# --------------------------------------------------------------------------- conversations + preferences
def chat(db: Session, user: User | None, text: str, pos, conversation_id: int | None) -> dict:
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
