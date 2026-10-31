from datetime import time
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator, model_validator

from app.models.catalog import BusinessType
from app.schemas.ghana_types import DigitalAddress, GhanaPhone as Phone, Region, require_in_ghana
from app.services import food_taxonomy as tx

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
Desc = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class VendorProfileIn(BaseModel):
    name: Name
    owner_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    contact_email: EmailStr | None = None
    phone: Phone
    address_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    description: Desc = ""
    category_id: int | None = None
    avg_prep_minutes: int = Field(20, ge=1, le=240)
    delivery_radius_km: float | None = Field(None, ge=1, le=50)
    business_type: BusinessType = BusinessType.RESTAURANT
    region: Region | None = None
    area: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    digital_address: DigitalAddress | None = None

    @model_validator(mode="after")
    def inside_ghana(self):
        require_in_ghana(self.latitude, self.longitude)
        return self


class VendorApplyIn(VendorProfileIn):
    opens_at: time = time(8, 0)       # applied to every day; editable per day later
    closes_at: time = time(20, 0)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doc_type: str
    original_name: str


class VendorProfileOut(BaseModel):
    id: int
    name: str
    owner_name: str | None
    contact_email: str | None
    phone: str | None
    address_text: str
    latitude: float
    longitude: float
    description: str
    category_id: int | None
    avg_prep_minutes: int
    logo_url: str | None
    cover_url: str | None
    is_open: bool
    cod_enabled: bool = True
    approval_status: str
    rejection_reason: str | None
    delivery_radius_km: float | None = None
    business_type: str = "RESTAURANT"
    region: str | None = None
    area: str | None = None
    digital_address: str | None = None
    documents: list[DocumentOut] = []


class HourIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time
    is_closed: bool = False


class MenuItemIn(BaseModel):
    name: Name
    description: Desc = ""
    price: Decimal = Field(gt=0, le=10000, decimal_places=2)
    category_id: int | None = None
    prep_minutes: int = Field(15, ge=1, le=240)
    is_spicy: bool = False
    is_available: bool = True
    tags: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    traits: list[str] = []
    meal_types: list[str] = []
    dietary: list[str] = []
    allergens: list[str] = []
    dietary_declared: bool = False     # tick only when the allergen list above is complete
    spice_level: int = Field(0, ge=0, le=3)
    daily_prep_qty: int | None = Field(None, ge=1, le=2000)       # portions you plan to make per day (helps waste advice)

    @field_validator("traits")
    @classmethod
    def _t(cls, v):
        return tx.clean(v, tx.TRAITS)

    @field_validator("meal_types")
    @classmethod
    def _m(cls, v):
        return tx.clean(v, tx.MEAL_TYPES)

    @field_validator("dietary")
    @classmethod
    def _d(cls, v):
        return tx.clean(v, tx.DIETARY)

    @field_validator("allergens")
    @classmethod
    def _a(cls, v):
        return tx.clean(v, tx.ALLERGENS)

    @model_validator(mode="after")
    def consistent(self):
        if self.spice_level > 0:
            self.is_spicy = True
        elif self.is_spicy:
            self.spice_level = 2          # 'spicy' with no level means medium
        bad = set(self.allergens) & {a for d in self.dietary for a in tx.IMPLIES_NO_ALLERGEN.get(d, set())}
        if bad:
            raise ValueError(f"This dish can't be labelled {', '.join(self.dietary)} and also contain {', '.join(sorted(bad))}")
        return self


class MenuItemVendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    price: float
    category_id: int | None
    prep_minutes: int
    is_spicy: bool
    is_available: bool
    tags: str
    image_url: str | None
    order_count: int
    traits: list[str] = []
    meal_types: list[str] = []
    dietary: list[str] = []
    allergens: list[str] = []
    dietary_declared: bool = False
    spice_level: int = 0
    daily_prep_qty: int | None = None


class RejectIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
