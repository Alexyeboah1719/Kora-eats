"""Vendor Intelligence endpoints (demand forecast, peak times, popular meals, waste advice). Approved vendors, their own data only."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.vendor import require_vendor
from app.core.errors import api_error
from app.db.session import get_db
from app.models.catalog import MenuItem, Vendor, WasteLog
from app.schemas.auth import MessageResponse
from app.services import forecast_service

router = APIRouter(prefix="/vendor", tags=["vendor-intelligence"])
MAX_AHEAD_DAYS = 14


@router.get("/intelligence")
def intelligence(on: date | None = Query(None, alias="date"), v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """Expected orders, peak periods, popular meals, preparation and waste advice for today (or a day up to two weeks ahead)."""
    today = forecast_service.utcnow().date()
    target = on or today
    if target < today or target > today + timedelta(days=MAX_AHEAD_DAYS):
        raise api_error(422, "bad_date", f"Choose today or a day up to {MAX_AHEAD_DAYS} days ahead.")
    return forecast_service.report(db, v, target)


class PrepQtyIn(BaseModel):
    daily_prep_qty: int | None = Field(None, ge=1, le=2000)


def _own_item(db: Session, v: Vendor, item_id: int) -> MenuItem:
    it = db.scalar(select(MenuItem).where(MenuItem.id == item_id, MenuItem.vendor_id == v.id))
    if it is None:
        raise api_error(404, "not_found", "Menu item not found.")
    return it


@router.put("/menu/{item_id}/prep-qty")
def set_prep_qty(item_id: int, data: PrepQtyIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """How many portions you plan to make per day. Optional; it lets the AI warn about over- and under-preparing."""
    it = _own_item(db, v, item_id)
    it.daily_prep_qty = data.daily_prep_qty
    db.commit()
    return {"menu_item_id": it.id, "daily_prep_qty": it.daily_prep_qty}


class WasteIn(BaseModel):
    menu_item_id: int
    log_date: date
    prepared_qty: int = Field(ge=1, le=5000)
    leftover_qty: int = Field(ge=0, le=5000)

    @model_validator(mode="after")
    def sane(self):
        if self.leftover_qty > self.prepared_qty:
            raise ValueError("Leftover can't be more than what you prepared")
        return self


@router.post("/waste", response_model=MessageResponse)
def log_waste(data: WasteIn, v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """End-of-day record: portions prepared and left over. Logging a few days makes the waste advice much sharper."""
    if data.log_date > forecast_service.utcnow().date():
        raise api_error(422, "bad_date", "You can't log leftovers for a future day.")
    it = _own_item(db, v, data.menu_item_id)
    row = db.scalar(select(WasteLog).where(WasteLog.menu_item_id == it.id, WasteLog.log_date == data.log_date))
    if row is None:
        row = WasteLog(vendor_id=v.id, menu_item_id=it.id, log_date=data.log_date, prepared_qty=data.prepared_qty, leftover_qty=data.leftover_qty)
        db.add(row)
    else:
        row.prepared_qty, row.leftover_qty = data.prepared_qty, data.leftover_qty
    db.commit()
    return MessageResponse(message="Saved. Thank you: this sharpens your waste advice.")


@router.get("/waste")
def waste_history(days: int = Query(30, ge=1, le=120), v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    since = forecast_service.utcnow().date() - timedelta(days=days)
    rows = db.execute(select(WasteLog, MenuItem.name).join(MenuItem, MenuItem.id == WasteLog.menu_item_id)
                      .where(WasteLog.vendor_id == v.id, WasteLog.log_date >= since).order_by(WasteLog.log_date.desc(), WasteLog.id.desc())).all()
    return [{"menu_item_id": w.menu_item_id, "item": name, "date": w.log_date.isoformat(), "prepared": w.prepared_qty, "leftover": w.leftover_qty,
             "leftover_percent": round(100 * w.leftover_qty / w.prepared_qty)} for w, name in rows]
