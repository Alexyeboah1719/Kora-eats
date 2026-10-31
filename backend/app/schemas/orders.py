from typing import Annotated

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, StringConstraints

Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


from app.schemas.ghana_types import GhanaPhone  # noqa: E402


class CartAddIn(BaseModel):
    menu_item_id: int
    quantity: int = Field(1, ge=1, le=20)
    special_instructions: Note | None = None
    replace: bool = False          # true = discard a cart from another vendor


class CartPatchIn(BaseModel):
    quantity: int | None = Field(None, ge=0, le=20)
    special_instructions: Note | None = None


class CheckoutIn(BaseModel):
    fulfillment_type: Literal["DELIVERY", "PICKUP"] = "DELIVERY"
    address_id: int | None = None      # required for DELIVERY, ignored for PICKUP
    phone: GhanaPhone | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    scheduled_for: datetime | None = None      # None = deliver now
    payment_mode: Literal["ONLINE", "COD"] | None = None  # COD = Cash on Delivery


class CouponIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=30)]


class CancelIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


class VendorStatusIn(BaseModel):
    status: str = Field(pattern="^(CONFIRMED|PREPARING|READY_FOR_PICKUP|CANCELLED)$")
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
