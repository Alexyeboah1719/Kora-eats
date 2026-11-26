from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.catalog import CategoryOut, HomeOut, MenuItemOut, VendorCard, VendorDetail
from app.core import ghana
from app.models.catalog import BUSINESS_LABELS
from app.services import catalog_service as svc, food_taxonomy as tx, vendor_policy

router = APIRouter(tags=["catalog"])
Lat = Query(None, ge=-90, le=90)
Lng = Query(None, ge=-180, le=180)


def _pos(lat, lng):
    return (lat, lng) if lat is not None and lng is not None else None


@router.get("/home", response_model=HomeOut)
def home(lat: float | None = Lat, lng: float | None = Lng, db: Session = Depends(get_db),
         user: User | None = Depends(get_optional_user)):
    return svc.home(db, user, _pos(lat, lng))


@router.get("/categories", response_model=list[CategoryOut])
def categories(db: Session = Depends(get_db)):
    return svc.list_categories(db)


@router.get("/vendors", response_model=list[VendorCard])
def vendors(lat: float | None = Lat, lng: float | None = Lng, q: str | None = Query(None, max_length=80),
            category_id: int | None = None, max_km: float | None = Query(None, gt=0, le=100),
            business_type: str | None = Query(None, pattern="^(RESTAURANT|CHOP_BAR|HOME_COOK|BAKER|STREET_FOOD|SMALL_BUSINESS|CATERER)$"),
            db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    return svc.search_vendors(db, user, _pos(lat, lng), q, category_id, max_km, business_type=business_type)


@router.get("/vendors/{vendor_id}", response_model=VendorDetail)
def vendor(vendor_id: int, lat: float | None = Lat, lng: float | None = Lng, db: Session = Depends(get_db),
           user: User | None = Depends(get_optional_user)):
    detail = svc.vendor_detail(db, user, vendor_id, _pos(lat, lng))
    if detail is None:
        raise HTTPException(404, {"code": "not_found", "message": "Vendor not found."})
    return detail


@router.get("/menu-items", response_model=list[MenuItemOut])
def menu_items(lat: float | None = Lat, lng: float | None = Lng, q: str | None = Query(None, max_length=80),
               category_id: int | None = None, vendor_id: int | None = None,
               min_price: float | None = Query(None, ge=0), max_price: float | None = Query(None, ge=0),
               spicy: bool | None = None, min_rating: float | None = Query(None, ge=0, le=5),
               max_prep: int | None = Query(None, ge=1), max_km: float | None = Query(None, gt=0, le=100),
               sort: str = Query("popular", pattern="^(popular|price_asc|price_desc|distance)$"),
               trait: list[str] = Query([]), meal_type: list[str] = Query([]), dietary: list[str] = Query([]), avoid_allergen: list[str] = Query([]),
               business_type: str | None = Query(None, pattern="^(RESTAURANT|CHOP_BAR|HOME_COOK|BAKER|STREET_FOOD|SMALL_BUSINESS|CATERER)$"),
               db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    try:
        traits, meals, diets, avoid = tx.clean(trait, tx.TRAITS), tx.clean(meal_type, tx.MEAL_TYPES), tx.clean(dietary, tx.DIETARY), tx.clean(avoid_allergen, tx.ALLERGENS)
    except ValueError as e:
        raise HTTPException(422, {"code": "bad_filter", "message": str(e)})
    return svc.search_items(db, user, _pos(lat, lng), q, category_id, vendor_id, min_price, max_price, spicy,
                            True, min_rating, max_prep, max_km, sort, traits=traits, meal_types=meals, dietary=diets,
                            avoid_allergens=avoid, business_type=business_type)


@router.get("/zone-check")
def zone_check(vendor_id: int, lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
               db: Session = Depends(get_db)):
    """Does this vendor deliver to a given point, how far is it and what would delivery cost?"""
    from app.core.geo import haversine_km
    v = db.scalar(svc.public_vendors_query().where(svc.Vendor.id == vendor_id))
    if v is None:
        raise HTTPException(404, {"code": "not_found", "message": "Vendor not found."})
    km = round(haversine_km(lat, lng, v.latitude, v.longitude), 2)
    radius = svc.vendor_radius_km(v)
    return {"deliverable": km <= radius, "distance_km": km, "radius_km": radius, "delivery_fee": svc.delivery_fee_for(km)}


def _opts(d: dict) -> list[dict]:
    return [{"id": k, "label": v} for k, v in d.items()]


@router.get("/taxonomy")
def taxonomy():
    """Every controlled option the app uses (food traits, dietary labels, allergens, meal times, business types, regions)."""
    return {"traits": _opts(tx.TRAITS), "meal_types": _opts(tx.MEAL_TYPES), "dietary": _opts(tx.DIETARY), "allergens": _opts(tx.ALLERGENS),
            "spice_levels": [{"id": k, "label": v} for k, v in tx.SPICE_LEVELS.items()], "business_types": _opts(BUSINESS_LABELS),
            "regions": ghana.REGIONS, "cities": [{"name": n, "lat": c[0], "lng": c[1]} for n, c in ghana.CITIES.items()]}


@router.get("/vendor-requirements")
def vendor_requirements():
    """What each kind of seller must upload to be verified (light for home cooks, fuller for restaurants)."""
    return {"types": vendor_policy.describe(), "document_labels": vendor_policy.DOC_LABELS}
