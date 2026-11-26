"""Reusable validated types for Ghanaian data."""
from typing import Annotated

from pydantic import AfterValidator

from app.core import ghana


def _phone(v: str) -> str:
    n = ghana.normalize_phone(v)
    if n is None:
        raise ValueError("Enter a valid Ghana phone number, e.g. 024 123 4567")
    return n


def _digital(v: str) -> str:
    n = ghana.normalize_digital_address(v)
    if n is None:
        raise ValueError("Ghana Post digital address looks like GA-543-4531")
    return n


def _region(v: str) -> str:
    for r in ghana.REGIONS:
        if r.lower() == v.strip().lower():
            return r
    raise ValueError("Choose one of Ghana's 16 regions")


GhanaPhone = Annotated[str, AfterValidator(_phone)]              # stored as +233XXXXXXXXX
DigitalAddress = Annotated[str, AfterValidator(_digital)]
Region = Annotated[str, AfterValidator(_region)]


def require_in_ghana(lat: float, lng: float) -> None:
    if not ghana.in_ghana(lat, lng):
        raise ValueError("KORA EATS currently serves Ghana only. Please pin a location inside Ghana.")
