from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import api_error
from app.db.session import get_db
from app.models.catalog import MenuItem, Vendor
from app.models.customer import Address, Favorite
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.catalog import MenuItemOut, VendorCard
from app.schemas.customer import AddressIn, AddressOut, FavoriteIn, ProfileIn, ProfileOut
from app.services import catalog_service as cat

router = APIRouter(prefix="/me", tags=["customer"])
MAX_ADDRESSES = 10


# ----------------------------------------------------------------- profile
def _profile(u: User) -> ProfileOut:
    return ProfileOut(id=str(u.id), email=u.email, full_name=u.profile.full_name, phone=u.profile.phone, role=u.role.value)


@router.get("/profile", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user)):
    return _profile(user)


@router.put("/profile", response_model=ProfileOut)
def update_profile(data: ProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.profile.full_name, user.profile.phone = data.full_name, data.phone
    db.commit()
    return _profile(user)


# ----------------------------------------------------------------- addresses
def _own_address(db: Session, user: User, address_id: int) -> Address:
    a = db.scalar(select(Address).where(Address.id == address_id, Address.user_id == user.id))
    if a is None:
        raise api_error(404, "not_found", "Address not found.")
    return a


def _clear_default(db: Session, user: User) -> None:
    db.execute(update(Address).where(Address.user_id == user.id).values(is_default=False))


@router.get("/addresses", response_model=list[AddressOut])
def list_addresses(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Address).where(Address.user_id == user.id).order_by(Address.is_default.desc(), Address.id)).all()


@router.post("/addresses", response_model=AddressOut, status_code=201)
def add_address(data: AddressIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.scalars(select(Address).where(Address.user_id == user.id)).all()
    if len(existing) >= MAX_ADDRESSES:
        raise api_error(400, "too_many", f"You can save up to {MAX_ADDRESSES} addresses.")
    make_default = data.is_default or not existing  # first address is the default
    if make_default:
        _clear_default(db, user)
    a = Address(user_id=user.id, **data.model_dump(exclude={"is_default"}), is_default=make_default)
    db.add(a)
    db.commit()
    return a


@router.put("/addresses/{address_id}", response_model=AddressOut)
def update_address(address_id: int, data: AddressIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _own_address(db, user, address_id)
    if data.is_default:
        _clear_default(db, user)
    for k, v in data.model_dump().items():
        setattr(a, k, v)
    db.commit()
    return a


@router.delete("/addresses/{address_id}", response_model=MessageResponse)
def delete_address(address_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _own_address(db, user, address_id)
    was_default = a.is_default
    db.delete(a)
    db.flush()
    if was_default:  # promote another address so there is always a default
        nxt = db.scalar(select(Address).where(Address.user_id == user.id).order_by(Address.id).limit(1))
        if nxt:
            nxt.is_default = True
    db.commit()
    return MessageResponse(message="Address deleted.")


# ----------------------------------------------------------------- favorites
@router.get("/favorites")
def list_favorites(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    vfav, ifav = cat.favorite_ids(db, user)
    vendors = [cat.vendor_card(v, None, vfav) for v in db.scalars(cat.public_vendors_query().where(Vendor.id.in_(vfav))).unique()] if vfav else []
    items = [cat.item_out(i, None, ifav) for i in db.scalars(select(MenuItem).where(MenuItem.id.in_(ifav))).unique()] if ifav else []
    return {"vendors": [v.model_dump() for v in vendors], "items": [i.model_dump() for i in items]}


@router.post("/favorites", response_model=MessageResponse, status_code=201)
def add_favorite(data: FavoriteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if (data.vendor_id is None) == (data.menu_item_id is None):
        raise api_error(422, "invalid", "Provide exactly one of vendor_id or menu_item_id.")
    target = db.get(Vendor, data.vendor_id) if data.vendor_id else db.get(MenuItem, data.menu_item_id)
    if target is None:
        raise api_error(404, "not_found", "Not found.")
    q = select(Favorite.id).where(Favorite.user_id == user.id)
    q = q.where(Favorite.vendor_id == data.vendor_id) if data.vendor_id else q.where(Favorite.menu_item_id == data.menu_item_id)
    if db.scalar(q) is None:
        db.add(Favorite(user_id=user.id, vendor_id=data.vendor_id, menu_item_id=data.menu_item_id))
        db.commit()
    return MessageResponse(message="Saved to favorites.")


@router.delete("/favorites", response_model=MessageResponse)
def remove_favorite(vendor_id: int | None = None, menu_item_id: int | None = None,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(Favorite).where(Favorite.user_id == user.id)
    q = q.where(Favorite.vendor_id == vendor_id) if vendor_id else q.where(Favorite.menu_item_id == menu_item_id)
    fav = db.scalar(q)
    if fav:
        db.delete(fav)
        db.commit()
    return MessageResponse(message="Removed from favorites.")
