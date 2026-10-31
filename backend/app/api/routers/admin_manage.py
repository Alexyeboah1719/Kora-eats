"""Admin management: dashboard, users, orders, payments, categories, reviews, support, broadcasts, reports, settings."""
import csv
import io
import re
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.db.session import get_db
from app.models.admin import SupportTicket, TicketStatus
from app.models.audit import AuditLog
from app.models.auth import RefreshToken
from app.models.catalog import ApprovalStatus, Category, MenuItem, Vendor
from app.models.notifications import Notification
from app.models.orders import Order, OrderStatus
from app.models.payments import Payment
from app.models.reviews import Review
from app.models.riders import DeliveryRider
from app.models.user import User, UserRole
from app.schemas.auth import MessageResponse
from app.services import (analytics_service, audit_service, email_templates, notification_service, order_service,
                          review_service, settings_service)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_roles(UserRole.ADMIN))])
Admin = Depends(require_roles(UserRole.ADMIN))
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    return analytics_service.admin_overview(db)


# ------------------------------------------------------------------ users
@router.get("/users")
def users(role: str | None = None, q: str | None = Query(None, max_length=80), limit: int = Query(50, le=200), offset: int = 0,
          db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.created_at.desc())
    if role:
        try:
            stmt = stmt.where(User.role == UserRole(role))
        except ValueError:
            raise api_error(422, "bad_role", "Unknown role.")
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q}%"))
    rows = db.scalars(stmt.limit(limit).offset(offset)).unique().all()
    spent = dict(db.execute(select(Order.customer_id, func.coalesce(func.sum(Order.total), 0)).where(Order.status.in_(analytics_service.COUNTED))
                            .group_by(Order.customer_id)).all())
    return [{"id": str(u.id), "email": u.email, "name": u.profile.full_name, "phone": u.profile.phone, "role": u.role.value, "is_active": u.is_active,
             "verified": u.is_email_verified, "created_at": u.created_at.isoformat(), "total_spent": float(spent.get(u.id, 0))} for u in rows]


@router.patch("/users/{user_id}/active", response_model=MessageResponse)
def set_user_active(user_id: str, is_active: bool, admin: User = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    import uuid
    try:
        u = db.get(User, uuid.UUID(user_id))
    except ValueError:
        u = None
    if u is None:
        raise api_error(404, "not_found", "User not found.")
    if u.id == admin.id:
        raise api_error(409, "self", "You can't deactivate your own account.")
    if u.role == UserRole.ADMIN:
        raise api_error(409, "admin", "Admin accounts can't be deactivated here.")
    u.is_active = is_active
    if not is_active:      # kick them out of every device immediately
        db.execute(update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit_service.record(db, "user_activated" if is_active else "user_deactivated", admin.id, detail=f"user={u.id}")
    db.commit()
    return MessageResponse(message="User updated.")


# ------------------------------------------------------------------ orders
@router.get("/orders")
def orders(status: str | None = None, q: str | None = Query(None, max_length=80), limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    stmt = select(Order, User.email).join(User, User.id == Order.customer_id).order_by(Order.id.desc())
    if status:
        try:
            stmt = stmt.where(Order.status == OrderStatus(status))
        except ValueError:
            raise api_error(422, "bad_status", "Unknown status.")
    if q:
        stmt = stmt.where(or_(Order.code.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    return [{"id": o.id, "code": o.code, "status": o.status.value, "vendor": o.vendor.name, "fulfillment": o.fulfillment_type.value, "customer": email, "total": float(o.total),
             "created_at": o.created_at.isoformat(), "paid": o.paid_at is not None, "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None}
            for o, email in db.execute(stmt.limit(limit)).unique()]


class OrderStatusIn(BaseModel):
    status: str
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


@router.get("/orders/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if o is None:
        raise api_error(404, "not_found", "Order not found.")
    return order_service.order_out(db, o)


@router.post("/orders/{order_id}/status")
def admin_set_status(order_id: int, data: OrderStatusIn, background: BackgroundTasks, admin: User = Depends(require_roles(UserRole.ADMIN)),
                     db: Session = Depends(get_db)):
    """Admin override. It still goes through the state machine, so impossible jumps are refused.
    REFUNDED only RECORDS a refund: return the money via your payment provider's dashboard first."""
    o = order_service.lock_order(db, order_id)
    if o is None:
        raise api_error(404, "not_found", "Order not found.")
    try:
        new = OrderStatus(data.status)
    except ValueError:
        raise api_error(422, "bad_status", "Unknown status.")
    order_service.change_status(db, o, new, "admin", admin.id, data.note, background)
    audit_service.record(db, "admin_order_status", admin.id, detail=f"order={o.code} -> {new.value}")
    db.commit()
    return order_service.order_out(db, o)


# ------------------------------------------------------------------ payments
@router.get("/payments")
def payments(status: str | None = None, limit: int = Query(100, le=300), db: Session = Depends(get_db)):
    stmt = select(Payment, Order.code).join(Order, Order.id == Payment.order_id).order_by(Payment.id.desc())
    if status:
        stmt = stmt.where(Payment.status == status)
    return [{"id": p.id, "order_id": p.order_id, "order_code": code, "provider": p.provider, "reference": p.reference, "amount": float(p.amount),
             "currency": p.currency, "method": p.method, "status": p.status.value, "failure_reason": p.failure_reason,
             "created_at": p.created_at.isoformat(), "verified_at": p.verified_at.isoformat() if p.verified_at else None}
            for p, code in db.execute(stmt.limit(limit)).all()]


# ------------------------------------------------------------------ categories
class CategoryIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
    icon: Annotated[str, StringConstraints(pattern=r"^bi-[a-z0-9-]{2,40}$")] = "bi-egg-fried"
    sort_order: int = Field(0, ge=0, le=1000)
    is_active: bool = True


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _cat(c: Category) -> dict:
    return {"id": c.id, "name": c.name, "slug": c.slug, "icon": c.icon, "sort_order": c.sort_order, "is_active": c.is_active}


@router.get("/categories")
def categories(db: Session = Depends(get_db)):
    used = dict(db.execute(select(MenuItem.category_id, func.count()).group_by(MenuItem.category_id)).all())
    return [{**_cat(c), "items": used.get(c.id, 0)} for c in db.scalars(select(Category).order_by(Category.sort_order, Category.name))]


@router.post("/categories", status_code=201)
def create_category(data: CategoryIn, db: Session = Depends(get_db)):
    if db.scalar(select(Category.id).where(or_(Category.name == data.name, Category.slug == _slug(data.name)))):
        raise api_error(409, "exists", "A category with that name already exists.")
    c = Category(name=data.name, slug=_slug(data.name), icon=data.icon, sort_order=data.sort_order, is_active=data.is_active)
    db.add(c); db.commit()
    return _cat(c)


@router.put("/categories/{cat_id}")
def update_category(cat_id: int, data: CategoryIn, db: Session = Depends(get_db)):
    c = db.get(Category, cat_id)
    if c is None:
        raise api_error(404, "not_found", "Category not found.")
    clash = db.scalar(select(Category.id).where(or_(Category.name == data.name, Category.slug == _slug(data.name)), Category.id != cat_id))
    if clash:
        raise api_error(409, "exists", "A category with that name already exists.")
    c.name, c.slug, c.icon, c.sort_order, c.is_active = data.name, _slug(data.name), data.icon, data.sort_order, data.is_active
    db.commit()
    return _cat(c)


@router.delete("/categories/{cat_id}", response_model=MessageResponse)
def delete_category(cat_id: int, db: Session = Depends(get_db)):
    c = db.get(Category, cat_id)
    if c is None:
        raise api_error(404, "not_found", "Category not found.")
    if db.scalar(select(MenuItem.id).where(MenuItem.category_id == c.id).limit(1)) or db.scalar(select(Vendor.id).where(Vendor.category_id == c.id).limit(1)):
        raise api_error(409, "in_use", "This category is in use. Turn it off instead of deleting it.")
    db.delete(c); db.commit()
    return MessageResponse(message="Category deleted.")


# ------------------------------------------------------------------ reviews
@router.get("/reviews")
def reviews(hidden: bool | None = None, sentiment: str | None = None, limit: int = Query(100, le=300), db: Session = Depends(get_db)):
    stmt = select(Review).order_by(Review.id.desc())
    if hidden is not None:
        stmt = stmt.where(Review.is_hidden == hidden)
    if sentiment:
        stmt = stmt.where(Review.sentiment == sentiment.upper())
    vendors = {v.id: v.name for v in db.scalars(select(Vendor)).unique()}
    return [{**review_service.review_out(r), "vendor": vendors.get(r.vendor_id), "is_hidden": r.is_hidden, "customer_email": r.customer.email}
            for r in db.scalars(stmt.limit(limit)).unique()]


@router.post("/reviews/{review_id}/hide", response_model=MessageResponse)
def hide_review(review_id: int, hidden: bool, admin: User = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    r = db.get(Review, review_id)
    if r is None:
        raise api_error(404, "not_found", "Review not found.")
    r.is_hidden = hidden
    review_service.recompute_vendor_rating(db, r.vendor_id)
    audit_service.record(db, "review_hidden" if hidden else "review_restored", admin.id, detail=f"review={r.id}")
    db.commit()
    return MessageResponse(message="Review updated.")


# ------------------------------------------------------------------ support
class ReplyIn(BaseModel):
    reply: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=2000)]
    resolve: bool = True


@router.get("/support")
def tickets(status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(SupportTicket, User.email).join(User, User.id == SupportTicket.user_id).order_by(SupportTicket.id.desc())
    if status:
        stmt = stmt.where(SupportTicket.status == TicketStatus(status))
    codes = dict(db.execute(select(Order.id, Order.code)).all())
    return [{"id": t.id, "subject": t.subject, "message": t.message, "status": t.status.value, "email": email, "order_code": codes.get(t.order_id),
             "admin_reply": t.admin_reply, "created_at": t.created_at.isoformat()} for t, email in db.execute(stmt.limit(200)).all()]


@router.post("/support/{ticket_id}/reply", response_model=MessageResponse)
def reply_ticket(ticket_id: int, data: ReplyIn, background: BackgroundTasks, admin: User = Depends(require_roles(UserRole.ADMIN)),
                 db: Session = Depends(get_db)):
    t = db.get(SupportTicket, ticket_id)
    if t is None:
        raise api_error(404, "not_found", "Ticket not found.")
    t.admin_reply, t.replied_at = data.reply, utcnow()
    t.status = TicketStatus.RESOLVED if data.resolve else TicketStatus.OPEN
    u = db.get(User, t.user_id)
    notification_service.notify(db, u.id, "support_reply", "Support replied to you", data.reply[:200], "/support.html",
                                email=email_templates.notification_email("Reply from KORA EATS support", data.reply, "/support.html", "View conversation"),
                                email_to=u.email, background=background)
    db.commit()
    return MessageResponse(message="Reply sent.")


# ------------------------------------------------------------------ broadcast (promotions / announcements)
class BroadcastIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=280)]
    url: Annotated[str, StringConstraints(pattern=r"^/[^/\\].*|^/$")] | None = None
    audience: str = Field("CUSTOMERS", pattern="^(ALL|CUSTOMERS|VENDORS|RIDERS)$")


MAX_BROADCAST = 2000


@router.post("/notifications/broadcast")
def broadcast(data: BroadcastIn, background: BackgroundTasks, admin: User = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    stmt = select(User.id).where(User.is_active.is_(True))
    if data.audience != "ALL":
        stmt = stmt.where(User.role == UserRole[data.audience[:-1]])
    ids = db.scalars(stmt.limit(MAX_BROADCAST)).all()
    for uid in ids:
        notification_service.notify(db, uid, "promotion", data.title, data.body, data.url, background=background)
    audit_service.record(db, "broadcast", admin.id, detail=f"{data.audience}:{len(ids)}")
    db.commit()
    return {"sent_to": len(ids), "capped": len(ids) == MAX_BROADCAST}


# ------------------------------------------------------------------ reports (CSV)
def _safe(v):
    """Spreadsheet formula-injection guard: text starting with = + - @ would run as a formula in Excel."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv(name: str, header: list[str], rows) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(c) for c in r])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}-{utcnow():%Y%m%d}.csv"'})


@router.get("/reports/{kind}.csv")
def report(kind: str, db: Session = Depends(get_db)):
    if kind == "sales":
        rows = db.execute(select(Order, User.email).join(User, User.id == Order.customer_id).order_by(Order.id.desc()).limit(20000)).unique()
        return _csv("sales", ["Order", "Date", "Status", "Vendor", "Customer", "Subtotal", "Delivery", "Discount", "Total", "Coupon"],
                    [(o.code, o.created_at.date(), o.status.value, o.vendor.name, e, o.subtotal, o.delivery_fee, o.discount, o.total, o.coupon_code) for o, e in rows])
    if kind == "payments":
        rows = db.execute(select(Payment, Order.code).join(Order, Order.id == Payment.order_id).order_by(Payment.id.desc()).limit(20000)).all()
        return _csv("payments", ["Date", "Order", "Reference", "Provider", "Method", "Amount", "Currency", "Status"],
                    [(p.created_at.date(), c, p.reference, p.provider, p.method, p.amount, p.currency, p.status.value) for p, c in rows])
    if kind == "vendors":
        agg = {r[0]: r for r in db.execute(select(Order.vendor_id, func.count(), func.coalesce(func.sum(Order.subtotal), 0)).where(
            Order.status.in_(analytics_service.COUNTED)).group_by(Order.vendor_id)).all()}
        return _csv("vendors", ["Vendor", "Status", "Orders", "Food sales", "Rating", "Reviews"],
                    [(v.name, v.approval_status.value, agg.get(v.id, (0, 0, 0))[1], agg.get(v.id, (0, 0, 0))[2], round(v.rating_avg, 2), v.rating_count)
                     for v in db.scalars(select(Vendor).order_by(Vendor.name)).unique()])
    raise api_error(404, "not_found", "Unknown report. Use sales, payments or vendors.")


# ------------------------------------------------------------------ audit log + settings
@router.get("/audit")
def audit(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    emails = dict(db.execute(select(User.id, User.email)).all())
    return [{"at": a.created_at.isoformat(), "action": a.action, "user": emails.get(a.user_id), "ip": a.ip_address, "detail": a.detail}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))]


@router.get("/settings")
def get_settings_list(db: Session = Depends(get_db)):
    return settings_service.current(db)


class SettingIn(BaseModel):
    value: float


@router.put("/settings/{key}", response_model=MessageResponse)
def put_setting(key: str, data: SettingIn, admin: User = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    settings_service.update(db, key, data.value, admin.id)
    audit_service.record(db, "setting_changed", admin.id, detail=f"{key}={data.value}")
    db.commit()
    return MessageResponse(message="Setting saved.")
