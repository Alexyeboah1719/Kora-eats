from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.schemas.ghana_types import GhanaPhone as Phone  # noqa: E402
Plate = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=20)]


class RiderApplyIn(BaseModel):
    phone: Phone
    vehicle_type: str
    plate_number: Plate | None = None


class RiderUpdateIn(RiderApplyIn):
    pass
