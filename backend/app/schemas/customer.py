from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.ghana_types import DigitalAddress, GhanaPhone, Region, require_in_ghana

Text80 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class AddressIn(BaseModel):
    label: Text80
    address_line: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]
    city: Text80 = "Accra"
    region: Region | None = None
    area: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None   # neighbourhood, e.g. East Legon
    digital_address: DigitalAddress | None = None                                                  # Ghana Post GPS, e.g. GA-543-4531
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    delivery_notes: Annotated[str, StringConstraints(max_length=255)] | None = None
    is_default: bool = False

    @model_validator(mode="after")
    def inside_ghana(self):
        require_in_ghana(self.latitude, self.longitude)
        return self


class AddressOut(AddressIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ProfileIn(BaseModel):
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    phone: GhanaPhone | None = None


class ProfileOut(BaseModel):
    id: str
    email: str
    full_name: str
    phone: str | None
    role: str


class FavoriteIn(BaseModel):
    vendor_id: int | None = None
    menu_item_id: int | None = None
