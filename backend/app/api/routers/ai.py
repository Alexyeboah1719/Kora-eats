from typing import Annotated
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.ai import CustomerPreference
from app.models.catalog import MenuItem
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.services import ai_service, cart_service as svc
from app.services.alternative_finder import find_alternatives, format_alternatives
from app.services.preference_learning import get_user_profile_summary

router = APIRouter(tags=["ai"])


class ChatIn(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    conversation_id: int | None = None


class AlternativeIn(BaseModel):
    menu_item_id: int = Field(..., gt=0)
    budget: Decimal | None = Field(None, gt=0, le=5000, decimal_places=2)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)


class AddToCartIn(BaseModel):
    menu_item_id: int = Field(..., gt=0)
    quantity: int = Field(1, ge=1, le=10)
    customization: str | None = Field(None, max_length=500)


def _pos(lat, lng):
    return (lat, lng) if lat is not None and lng is not None else None


@router.post("/ai/chat", dependencies=[Depends(rate_limit("ai_chat", 40, 60))])
def chat(data: ChatIn, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    """The food assistant. Works for guests too (no history is stored for them)."""
    return ai_service.chat(db, user, data.message, _pos(data.lat, data.lng), data.conversation_id)


@router.get("/ai/conversations/{conversation_id}")
def conversation(conversation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ai_service.history(db, user, conversation_id)


@router.get("/ai/preferences")
def preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(CustomerPreference, user.id)
    return {"spicy_preference": p.spicy_preference if p else None, "avg_budget": round(p.avg_budget, 2) if p and p.avg_budget else None,
            "top_categories": (p.top_categories if p else None) or []}


@router.get("/search/natural", dependencies=[Depends(rate_limit("nl_search", 60, 60))])
def natural_search(q: str = Query(..., min_length=1, max_length=300), lat: float | None = Query(None, ge=-90, le=90),
                   lng: float | None = Query(None, ge=-180, le=180), db: Session = Depends(get_db),
                   user: User | None = Depends(get_optional_user)):
    """Plain-English search: 'spicy chicken and rice under GH₵50 within 3 km' -> filters + real results."""
    r = ai_service.answer(db, user, q, _pos(lat, lng))
    return {"understood": r["parsed"] is not None, "filters": r["filters"], "items": [i.model_dump() for i in r["items"]],
            "message": r["reply"]}


@router.post("/ai/alternatives", dependencies=[Depends(rate_limit("ai_alt", 30, 60))])
def get_alternatives(data: AlternativeIn, db: Session = Depends(get_db)):
    """Find alternatives when a meal is unavailable. Returns similar meals, best price options, and alternative restaurants."""
    alts = find_alternatives(db, data.menu_item_id, data.budget, _pos(data.lat, data.lng))
    original = db.scalar(select(MenuItem).where(MenuItem.id == data.menu_item_id))
    original_name = original.name if original else "this meal"
    
    return {
        "alternatives": [
            {
                "type": alt.type,
                "item": alt.item,
                "vendor": alt.vendor,
                "reason": alt.reason,
                "price_match": alt.price_match,
                "similarity_score": alt.similarity_score
            }
            for alt in alts
        ],
        "message": format_alternatives(alts, original_name)
    }


@router.post("/ai/add-to-cart", dependencies=[Depends(rate_limit("ai_cart", 20, 60))])
def ai_add_to_cart(data: AddToCartIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI can add items to cart with customizations on behalf of the user."""
    svc.add_item(db, user, data.menu_item_id, data.quantity, data.customization, replace=False)
    db.commit()
    cart = svc.view(db, user, None, "DELIVERY")
    return {
        "message": f"Added {data.quantity}x to your cart" + (f" with customizations: {data.customization}" if data.customization else ""),
        "cart": cart
    }


@router.get("/ai/my-profile")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get what the AI has learned about your preferences."""
    return get_user_profile_summary(db, user)
