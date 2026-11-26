from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.promos import GroupCreateIn, GroupItemIn, GroupItemPatch
from app.services import group_service as svc

router = APIRouter(prefix="/groups", tags=["groups"])
guard = [Depends(rate_limit("group", 120, 300))]     # slows down anyone guessing share codes


@router.post("", status_code=201, dependencies=guard)
def create(data: GroupCreateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(svc.create(db, user, data.vendor_id, data.name), user)


@router.get("/{code}", dependencies=guard)
def get_group(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(svc.get(db, code), user)


@router.post("/{code}/join", dependencies=guard)
def join(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(svc.join(db, user, code), user)


@router.post("/{code}/items", dependencies=guard)
def add_item(code: str, data: GroupItemIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(svc.add_item(db, user, code, data.menu_item_id, data.quantity, data.special_instructions), user)


@router.patch("/{code}/items/{line_id}", dependencies=guard)
def patch_item(code: str, line_id: int, data: GroupItemPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(svc.update_item(db, user, code, line_id, data.quantity, data.special_instructions), user)


@router.delete("/{code}/items/{line_id}", dependencies=guard)
def delete_item(code: str, line_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(svc.update_item(db, user, code, line_id, 0, None), user)


@router.post("/{code}/checkout", dependencies=guard)
def checkout(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Copies the whole group's food into the organizer's cart; returns that cart."""
    return svc.checkout(db, user, code)


@router.post("/{code}/close", response_model=MessageResponse, dependencies=guard)
def close(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.close(db, user, code)
    return MessageResponse(message="Group order closed.")


# ====================================================================== Group Food AI
from decimal import Decimal  # noqa: E402
from typing import Literal  # noqa: E402
from uuid import UUID  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from app.api.routers.recommend import _clean, _cravings, _pos  # noqa: E402
from app.services import food_taxonomy as tx  # noqa: E402


def _terms(values: list[str]) -> list[str]:
    import re
    out = []
    for v in values:
        w = re.sub(r"[^a-z0-9 ]", "", v.lower()).strip()
        if w and w not in out:
            out.append(w[:30])
    return out[:5]


class GroupPrefIn(BaseModel):
    cravings: list[str] = []
    terms: list[str] = Field([], max_length=8)
    max_price: Decimal | None = Field(None, gt=0, le=5000, decimal_places=2)
    dietary: list[str] = []
    avoid_allergens: list[str] = []
    no_spice: bool = False


@router.put("/{code}/preferences", dependencies=guard)
def put_preferences(code: str, data: GroupPrefIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Say what you want from the group meal. Only you can read this back; the organizer's AI uses it to find a plan."""
    svc.set_preferences(db, user, code, {"cravings": _cravings(data.cravings), "terms": _terms(data.terms), "max_price": data.max_price,
                                         "dietary": _clean(data.dietary, tx.DIETARY, "dietary"),
                                         "avoid_allergens": _clean(data.avoid_allergens, tx.ALLERGENS, "allergens"), "no_spice": data.no_spice})
    return svc.my_preferences(db, user, code)


@router.get("/{code}/preferences", dependencies=guard)
def get_my_preferences(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.my_preferences(db, user, code)


class SuggestIn(BaseModel):
    budget: Decimal | None = Field(None, gt=0, le=5000, decimal_places=2)       # the group's TOTAL, delivery included
    fulfillment: Literal["DELIVERY", "PICKUP", "BEST"] = "BEST"
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    any_vendor: bool = False


@router.post("/{code}/suggest", dependencies=guard)
def suggest(code: str, data: SuggestIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Group Food AI: one dish per person that satisfies everyone as much as possible (organizer only)."""
    return svc.suggest(db, user, code, data.budget, data.fulfillment, _pos(data.lat, data.lng), data.any_vendor)


class Assignment(BaseModel):
    user_id: UUID
    menu_item_id: int


class ApplyIn(BaseModel):
    vendor_id: int
    assignments: list[Assignment] = Field(min_length=1, max_length=20)


@router.post("/{code}/suggest/apply", dependencies=guard)
def apply_plan(code: str, data: ApplyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = svc.apply_plan(db, user, code, data.vendor_id, [(str(a.user_id), a.menu_item_id) for a in data.assignments])
    return svc.view(g, user)
