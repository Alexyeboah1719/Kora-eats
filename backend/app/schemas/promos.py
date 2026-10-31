import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models.promos import CouponType


class CouponIn(BaseModel):
    code: str
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    type: CouponType
    value: Decimal = Field(0, ge=0, le=100000, decimal_places=2)
    min_order: Decimal = Field(0, ge=0, le=100000, decimal_places=2)
    max_discount: Decimal | None = Field(None, gt=0, le=100000, decimal_places=2)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    usage_limit: int | None = Field(None, ge=1, le=1000000)
    per_user_limit: int = Field(1, ge=1, le=1000)
    first_order_only: bool = False
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def clean_code(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]{3,30}", v):
            raise ValueError("Code must be 3-30 letters, digits, - or _")
        return v

    @model_validator(mode="after")
    def rules(self):
        if self.type == CouponType.PERCENT and not (1 <= self.value <= 100):
            raise ValueError("A percentage discount must be between 1 and 100")
        if self.type == CouponType.FIXED and self.value <= 0:
            raise ValueError("A fixed discount must be more than 0")
        if self.type == CouponType.FREE_DELIVERY:
            self.value = Decimal("0")
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("Expiry must be after the start date")
        return self


class PromotionIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    discount_percent: int = Field(ge=1, le=90)
    menu_item_id: int | None = None         # None = the whole menu
    starts_at: datetime
    ends_at: datetime
    is_active: bool = True

    @model_validator(mode="after")
    def order(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("The promotion must end after it starts")
        return self


class GroupCreateIn(BaseModel):
    vendor_id: int
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""


class GroupItemIn(BaseModel):
    menu_item_id: int
    quantity: int = Field(1, ge=1, le=20)
    special_instructions: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None


class GroupItemPatch(BaseModel):
    quantity: int | None = Field(None, ge=0, le=20)
    special_instructions: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
