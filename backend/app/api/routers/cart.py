from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.customer import Address
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.orders import CartAddIn, CartPatchIn, CouponIn
from app.services import cart_service as svc, coupon_service

router = APIRouter(prefix="/cart", tags=["cart"])


def _address(db: Session, user: User, address_id: int | None) -> Address | None:
    if address_id is None:
        return None
    return db.scalar(select(Address).where(Address.id == address_id, Address.user_id == user.id))


@router.get("")
def get_cart(address_id: int | None = None, fulfillment: str = Query("DELIVERY", pattern="^(DELIVERY|PICKUP)$"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(db, user, _address(db, user, address_id), fulfillment)


@router.post("/items")
def add(data: CartAddIn, address_id: int | None = None, fulfillment: str = Query("DELIVERY", pattern="^(DELIVERY|PICKUP)$"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.add_item(db, user, data.menu_item_id, data.quantity, data.special_instructions, data.replace)
    return svc.view(db, user, _address(db, user, address_id), fulfillment)


@router.patch("/items/{line_id}")
def patch(line_id: int, data: CartPatchIn, address_id: int | None = None, fulfillment: str = Query("DELIVERY", pattern="^(DELIVERY|PICKUP)$"), user: User = Depends(get_current_user),
          db: Session = Depends(get_db)):
    svc.update_line(db, user, line_id, data.quantity, data.special_instructions)
    return svc.view(db, user, _address(db, user, address_id), fulfillment)


@router.delete("/items/{line_id}")
def remove(line_id: int, address_id: int | None = None, fulfillment: str = Query("DELIVERY", pattern="^(DELIVERY|PICKUP)$"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.update_line(db, user, line_id, 0, None)
    return svc.view(db, user, _address(db, user, address_id), fulfillment)


@router.delete("", response_model=MessageResponse)
def clear(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.clear(db, user)
    return MessageResponse(message="Cart cleared.")


@router.post("/coupon")
def apply_coupon(data: CouponIn, address_id: int | None = None, fulfillment: str = Query("DELIVERY", pattern="^(DELIVERY|PICKUP)$"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Checks the code against every rule right now, so the customer gets instant feedback."""
    coupon_service.apply_to_cart(db, user, svc.get_cart(db, user), data.code)
    db.commit()
    return svc.view(db, user, _address(db, user, address_id), fulfillment)


@router.delete("/coupon")
def remove_coupon(address_id: int | None = None, fulfillment: str = Query("DELIVERY", pattern="^(DELIVERY|PICKUP)$"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.get_cart(db, user).coupon_code = None
    db.commit()
    return svc.view(db, user, _address(db, user, address_id), fulfillment)
