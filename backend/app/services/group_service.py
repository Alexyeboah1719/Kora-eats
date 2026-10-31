"""Group ordering: one person starts a group order at a vendor and shares /group/ABC123.
Everyone adds their own food. The organizer checks out and pays for the whole group."""
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.models.groups import GroupOrder, GroupOrderItem, GroupOrderMember, GroupStatus
from app.models.user import User
from app.services import cart_service, catalog_service

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
MAX_MEMBERS, MAX_LINES, EXPIRY_HOURS = 20, 60, 6


def _code(db: Session) -> str:
    while True:
        c = "".join(secrets.choice(ALPHABET) for _ in range(6))
        if db.scalar(select(GroupOrder.id).where(GroupOrder.code == c)) is None:
            return c


def create(db: Session, user: User, vendor_id: int, name: str) -> GroupOrder:
    vendor = db.scalar(catalog_service.public_vendors_query().where(catalog_service.Vendor.id == vendor_id))
    if vendor is None:
        raise api_error(404, "not_found", "Vendor not found.")
    g = GroupOrder(code=_code(db), organizer_id=user.id, vendor_id=vendor.id, name=name or f"{user.profile.full_name.split()[0]}'s group order",
                   expires_at=utcnow() + timedelta(hours=EXPIRY_HOURS))
    g.members.append(GroupOrderMember(user_id=user.id, display_name=user.profile.full_name))
    db.add(g)
    db.commit()
    return g


def get(db: Session, code: str) -> GroupOrder:
    g = db.scalar(select(GroupOrder).where(GroupOrder.code == code.upper()))
    if g is None:
        raise api_error(404, "not_found", "Group order not found. Check the link.")
    return g


def _expired(g: GroupOrder) -> bool:
    return g.status == GroupStatus.OPEN and g.expires_at < utcnow()


def _require_open(g: GroupOrder) -> None:
    if g.status != GroupStatus.OPEN:
        raise api_error(409, "group_closed", "This group order is closed.")
    if _expired(g):
        raise api_error(410, "group_expired", "This group order has expired.")


def _member(g: GroupOrder, user: User) -> GroupOrderMember | None:
    return next((m for m in g.members if m.user_id == user.id), None)


def join(db: Session, user: User, code: str) -> GroupOrder:
    g = get(db, code)
    if _member(g, user) is None:
        _require_open(g)
        if len(g.members) >= MAX_MEMBERS:
            raise api_error(409, "group_full", f"This group already has {MAX_MEMBERS} people.")
        g.members.append(GroupOrderMember(user_id=user.id, display_name=user.profile.full_name))
        db.commit()
    return g


def add_item(db: Session, user: User, code: str, item_id: int, qty: int, instructions: str | None) -> GroupOrder:
    g = get(db, code)
    _require_open(g)
    if _member(g, user) is None:
        raise api_error(403, "not_member", "Join the group first.")
    it = cart_service._orderable_item(db, item_id)               # same rules as the cart: exists, available, vendor open
    if it.vendor_id != g.vendor_id:
        raise api_error(422, "wrong_vendor", f"This group orders from {g.vendor.name}.")
    line = next((i for i in g.items if i.user_id == user.id and i.menu_item_id == item_id), None)
    if line:
        line.quantity = min(20, line.quantity + qty)
        if instructions is not None:
            line.special_instructions = instructions
    else:
        if len(g.items) >= MAX_LINES:
            raise api_error(400, "group_full", "This group order has too many items.")
        g.items.append(GroupOrderItem(user_id=user.id, menu_item_id=item_id, quantity=qty, special_instructions=instructions))
    db.commit()
    return g


def _line(g: GroupOrder, user: User, line_id: int) -> GroupOrderItem:
    line = next((i for i in g.items if i.id == line_id), None)
    if line is None:
        raise api_error(404, "not_found", "Item not found.")
    if line.user_id != user.id and g.organizer_id != user.id:     # you may change your own lines; the organizer may change any
        raise api_error(403, "forbidden", "That's someone else's item.")
    return line


def update_item(db: Session, user: User, code: str, line_id: int, qty: int | None, instructions: str | None) -> GroupOrder:
    g = get(db, code)
    _require_open(g)
    line = _line(g, user, line_id)
    if qty is not None:
        g.items.remove(line) if qty == 0 else setattr(line, "quantity", qty)
    if instructions is not None and qty != 0:
        line.special_instructions = instructions or None
    db.commit()
    return g


def view(g: GroupOrder, viewer: User) -> dict:
    names = {m.user_id: m.display_name for m in g.members}
    lines, subtotal = [], 0.0
    for i in g.items:
        price, original = catalog_service.price_for(i.item)
        total = round(price * i.quantity, 2)
        subtotal += total
        lines.append({"id": i.id, "user_id": str(i.user_id), "member": names.get(i.user_id, "Someone"), "menu_item_id": i.menu_item_id,
                      "name": i.item.name, "unit_price": price, "quantity": i.quantity, "line_total": total,
                      "special_instructions": i.special_instructions, "mine": i.user_id == viewer.id, "available": i.item.is_available})
    return {"code": g.code, "name": g.name, "status": "EXPIRED" if _expired(g) else g.status.value,
            "vendor": {"id": g.vendor.id, "name": g.vendor.name}, "is_organizer": g.organizer_id == viewer.id,
            "organizer": names.get(g.organizer_id, ""), "members": [{"name": m.display_name, "is_organizer": m.user_id == g.organizer_id, "has_preferences": m.user_id in {p.user_id for p in g.preferences}} for m in g.members],
            "items": lines, "subtotal": round(subtotal, 2), "share_path": f"/group/{g.code}", "expires_at": g.expires_at.isoformat()}


def checkout(db: Session, user: User, code: str) -> dict:
    """Moves everyone's items into the organizer's cart. The organizer then checks out and pays as usual."""
    g = get(db, code)
    if g.organizer_id != user.id:
        raise api_error(403, "forbidden", "Only the person who started the group can check out.")
    _require_open(g)
    if not g.items:
        raise api_error(422, "empty_group", "Nobody has added any food yet.")
    names = {m.user_id: m.display_name.split()[0] for m in g.members}
    cart = cart_service.get_cart(db, user)
    cart.items.clear()
    cart.coupon_code = None
    cart.vendor_id = g.vendor_id
    merged: dict[int, dict] = {}
    for i in g.items:
        m = merged.setdefault(i.menu_item_id, {"qty": 0, "notes": []})
        m["qty"] += i.quantity
        if i.special_instructions:
            m["notes"].append(f"{names.get(i.user_id, '?')}: {i.special_instructions}")
    from app.models.orders import CartItem
    for item_id, m in merged.items():
        cart_service._orderable_item(db, item_id)               # fails clearly if something sold out meanwhile
        cart.items.append(CartItem(menu_item_id=item_id, quantity=min(20, m["qty"]), special_instructions="; ".join(m["notes"])[:200] or None))
    g.status = GroupStatus.CHECKED_OUT
    db.commit()
    return cart_service.view(db, user, None)


def close(db: Session, user: User, code: str) -> None:
    g = get(db, code)
    if g.organizer_id != user.id:
        raise api_error(403, "forbidden", "Only the organizer can close the group.")
    if g.status == GroupStatus.OPEN:
        g.status = GroupStatus.CLOSED
        db.commit()


# ====================================================================== Group Food AI
from decimal import Decimal as _D  # noqa: E402

from sqlalchemy import select as _select  # noqa: E402

from app.models.ai import CustomerPreference  # noqa: E402
from app.models.catalog import Vendor  # noqa: E402
from app.models.groups import GroupOrderPreference  # noqa: E402
from app.services.recommend import group as solver  # noqa: E402
from app.services.recommend.retrieve import retrieve  # noqa: E402
from app.services.recommend.types import Request  # noqa: E402


def set_preferences(db: Session, user: User, code: str, data: dict) -> GroupOrderPreference:
    g = get(db, code)
    _require_open(g)
    if _member(g, user) is None:
        raise api_error(403, "not_member", "Join the group first.")
    p = next((x for x in g.preferences if x.user_id == user.id), None)
    if p is None:
        p = GroupOrderPreference(group_id=g.id, user_id=user.id)
        g.preferences.append(p)
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    return p


def my_preferences(db: Session, user: User, code: str) -> dict:
    g = get(db, code)
    p = next((x for x in g.preferences if x.user_id == user.id), None)           # ONLY the caller's own wishes are ever returned
    return pref_out(p)


def pref_out(p: GroupOrderPreference | None) -> dict:
    return {"cravings": list(p.cravings or []) if p else [], "terms": list(p.terms or []) if p else [],
            "max_price": float(p.max_price) if p and p.max_price is not None else None, "dietary": list(p.dietary or []) if p else [],
            "avoid_allergens": list(p.avoid_allergens or []) if p else [], "no_spice": bool(p.no_spice) if p else False}


def _merge(a, b):
    return list(dict.fromkeys([*(a or []), *(b or [])]))


def persons_for(db: Session, g: GroupOrder) -> list[solver.Person]:
    """Each member's wishes for THIS group, plus the safety information from their saved food profile (diet, allergens, 'no spice')."""
    prefs = {p.user_id: p for p in g.preferences}
    profiles = {c.user_id: c for c in db.scalars(_select(CustomerPreference).where(CustomerPreference.user_id.in_([m.user_id for m in g.members])))}
    out = []
    for m in g.members:
        p, prof = prefs.get(m.user_id), profiles.get(m.user_id)
        out.append(solver.Person(
            label=m.display_name.split()[0], user_id=str(m.user_id), cravings=list(p.cravings or []) if p else [], terms=list(p.terms or []) if p else [],
            max_price=_D(p.max_price) if p and p.max_price is not None else None,
            diet=_merge(p.dietary if p else [], prof.dietary if prof else []), avoid=_merge(p.avoid_allergens if p else [], prof.avoid_allergens if prof else []),
            no_spice=bool((p and p.no_spice) or (prof and prof.spice_preference == "none"))))
    return out


def suggest(db: Session, user: User, code: str, budget, fulfillment: str, pos, any_vendor: bool) -> dict:
    g = get(db, code)
    if g.organizer_id != user.id:
        raise api_error(403, "forbidden", "Only the organizer can ask for meal suggestions.")
    _require_open(g)
    persons = persons_for(db, g)
    req = Request(pos=pos, budget=budget, fulfillment=fulfillment, vendor_id=None if any_vendor else g.vendor_id, surface="group")
    cands = retrieve(db, req, None)
    out = solver.build_plans(persons, cands, budget, fulfillment, pos)
    out["members_without_preferences"] = sum(1 for p in persons if not p.has_wishes)
    out["scope"] = "any_vendor" if any_vendor else "group_vendor"
    return out


def apply_plan(db: Session, user: User, code: str, vendor_id: int, assignments: list[tuple[str, int]]) -> GroupOrder:
    """Writes a suggested plan into the group: one dish per member. Everything is re-validated here; the client's word isn't trusted."""
    g = get(db, code)
    if g.organizer_id != user.id:
        raise api_error(403, "forbidden", "Only the organizer can apply a plan.")
    _require_open(g)
    vendor = db.scalar(catalog_service.public_vendors_query().where(Vendor.id == vendor_id))
    if vendor is None:
        raise api_error(404, "not_found", "Vendor not found.")
    member_ids = {str(m.user_id): m for m in g.members}
    seen = set()
    for uid, item_id in assignments:
        if uid not in member_ids or uid in seen:
            raise api_error(422, "bad_assignment", "Each assignment must name a different member of this group.")
        seen.add(uid)
        it = cart_service._orderable_item(db, item_id)
        if it.vendor_id != vendor.id:
            raise api_error(422, "wrong_vendor", f"That dish isn't sold by {vendor.name}.")
    if vendor.id != g.vendor_id:                       # switching vendor starts the basket over
        g.items.clear()
        g.vendor_id, g.vendor = vendor.id, vendor
    for uid, item_id in assignments:
        m = member_ids[uid]
        for line in [i for i in g.items if i.user_id == m.user_id]:
            g.items.remove(line)
        g.items.append(GroupOrderItem(user_id=m.user_id, menu_item_id=item_id, quantity=1))
    db.commit()
    return g
