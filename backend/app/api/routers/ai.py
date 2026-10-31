from typing import Annotated

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session
import json
import asyncio

from app.api.deps import get_current_user, get_optional_user, require_roles
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.ai import CustomerPreference
from app.models.catalog import Vendor
from app.models.riders import DeliveryRider
from app.models.user import User, UserRole
from app.services import ai_service, role_ai_service, financial_analytics, ai_tools

router = APIRouter(tags=["ai"])


class ChatIn(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    conversation_id: int | None = None


class RoleChatIn(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


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


# -------------------------------------------------------------------------
# Role-specific AI endpoints (Admin, Vendor, Rider)
# -------------------------------------------------------------------------

@router.post("/ai/admin/chat", dependencies=[Depends(require_roles(UserRole.ADMIN))])
def admin_chat(data: RoleChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin AI assistant for analytics and platform insights. Requires admin authentication."""
    return role_ai_service.admin_chat(db, user.id, data.message)


@router.post("/ai/vendor/chat")
def vendor_chat(data: RoleChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Vendor AI assistant for business intelligence. Requires vendor authentication."""
    from app.api.routers.vendor import require_vendor
    vendor = require_vendor(user, db)
    return role_ai_service.vendor_chat(db, vendor.id, data.message)


@router.post("/ai/rider/chat")
def rider_chat(data: RoleChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rider AI assistant for delivery insights. Requires rider authentication."""
    from app.api.routers.rider import require_rider
    rider = require_rider(user, db)
    return role_ai_service.rider_chat(db, rider.id, data.message)


# -------------------------------------------------------------------------
# Financial Analytics Endpoints
# -------------------------------------------------------------------------

@router.get("/vendor/earnings")
def vendor_earnings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Vendor earnings breakdown with 10% commission details."""
    from app.api.routers.vendor import require_vendor
    vendor = require_vendor(user, db)
    return financial_analytics.vendor_earnings_summary(db, vendor.id)


@router.get("/rider/earnings")
def rider_earnings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rider earnings breakdown with 10% commission details."""
    from app.api.routers.rider import require_rider
    rider = require_rider(user, db)
    return financial_analytics.rider_earnings_summary(db, rider.id)


@router.get("/admin/financial-overview", dependencies=[Depends(require_roles(UserRole.ADMIN))])
def financial_overview(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Platform financial overview for admin dashboard."""
    return financial_analytics.platform_financial_overview(db, days)


@router.get("/order/{order_id}/earnings")
def order_earnings(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Detailed earnings breakdown for a specific order."""
    return financial_analytics.order_earnings_detail(db, order_id)


# -------------------------------------------------------------------------
# Enhanced AI Tools with Database Access
# -------------------------------------------------------------------------

class ToolRequest(BaseModel):
    tool: str
    parameters: dict = Field(default_factory={})


@router.post("/ai/tools")
def ai_tools_execute(data: ToolRequest, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    """Execute AI tools with role-based authorization."""
    if data.tool == "get_analytics":
        if not user:
            raise HTTPException(status_code=401, detail={"code": "auth_required", "message": "Please log in to access your account analytics."})
        return ai_tools.get_user_role_analytics(db, user, data.parameters.get("days", 30))
    elif data.tool == "get_vendor_analytics":
        return ai_tools.get_vendor_analytics(db, user, data.parameters.get("days", 30))
    elif data.tool == "get_rider_analytics":
        return ai_tools.get_rider_analytics(db, user, data.parameters.get("days", 30))
    elif data.tool == "get_admin_analytics":
        return ai_tools.get_admin_analytics(db, user, data.parameters.get("days", 30))
    elif data.tool == "get_customer_orders":
        return ai_tools.get_customer_orders(db, user, data.parameters.get("limit", 10))
    else:
        raise HTTPException(status_code=400, detail={"code": "unknown_tool", "message": f"Unknown tool: {data.tool}"})


async def _stream_response(text: str):
    """Stream response text in chunks for progressive display."""
    words = text.split()
    for i, word in enumerate(words):
        chunk = word + (" " if i < len(words) - 1 else "")
        yield chunk
        await asyncio.sleep(0.02)  # Small delay for visual effect


@router.post("/ai/chat/stream", dependencies=[Depends(rate_limit("ai_chat", 40, 60))])
def chat_stream(data: ChatIn, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    """Streaming version of the food assistant for progressive response display."""
    result = ai_service.chat(db, user, data.message, _pos(data.lat, data.lng), data.conversation_id)
    return StreamingResponse(
        _stream_response(result["reply"]),
        media_type="text/plain",
        headers={"X-Conversation-ID": str(result["conversation_id"] or "")}
    )
