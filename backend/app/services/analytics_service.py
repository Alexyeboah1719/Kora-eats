"""Dashboard numbers. 'Gross sales' = order totals of orders that were paid and not cancelled/refunded."""
from datetime import date, timedelta

from sqlalchemy import Date, cast, func, literal_column, select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models.catalog import ApprovalStatus, MenuItem, Vendor
from app.models.orders import Order, OrderItem, OrderStatus as S
from app.models.reviews import Review
from app.models.riders import DeliveryRider
from app.models.user import User, UserRole

COUNTED = [S.PAID, S.CONFIRMED, S.PREPARING, S.READY_FOR_PICKUP, S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY, S.DELIVERED, S.COLLECTED]
OPEN_STATES = [S.PAID, S.CONFIRMED, S.PREPARING, S.READY_FOR_PICKUP, S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY]
DAYS = 30


def _day(col):
    return cast(func.timezone("UTC", col), Date)           # Ghana runs on UTC


def _series(rows: list[tuple], days: int = DAYS) -> dict:
    """[(date, value)] -> {labels, values} with zero-filled gaps for the last `days` days."""
    by = {d: float(v or 0) for d, v in rows}
    today = utcnow().date()
    ds = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    return {"labels": [d.strftime("%d %b") for d in ds], "values": [round(by.get(d, 0), 2) for d in ds]}


def admin_overview(db: Session) -> dict:
    now = utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = now - timedelta(days=DAYS)
    revenue = lambda *w: float(db.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(Order.status.in_(COUNTED), *w)))
    count = lambda model, *w: db.scalar(select(func.count()).select_from(model).where(*w))
    cards = {
        "total_revenue": revenue(), "today_revenue": revenue(Order.paid_at >= midnight),
        "total_orders": count(Order, Order.status.in_(COUNTED + [S.CANCELLED, S.REFUNDED])),
        "pending_orders": count(Order, Order.status.in_(OPEN_STATES)),
        "active_vendors": count(Vendor, Vendor.approval_status == ApprovalStatus.APPROVED),
        "active_riders": count(DeliveryRider, DeliveryRider.approval_status == ApprovalStatus.APPROVED),
        "online_riders": count(DeliveryRider, DeliveryRider.is_online.is_(True)),
        "customers": count(User, User.role == UserRole.CUSTOMER, User.is_active.is_(True)),
        "cancelled_orders": count(Order, Order.status == S.CANCELLED),
        "pending_vendor_approvals": count(Vendor, Vendor.approval_status == ApprovalStatus.PENDING),
        "pending_rider_approvals": count(DeliveryRider, DeliveryRider.approval_status == ApprovalStatus.PENDING),
    }
    rev_rows = db.execute(select(_day(Order.paid_at), func.sum(Order.total)).where(Order.status.in_(COUNTED), Order.paid_at >= since).group_by(literal_column("1"))).all()
    ord_rows = db.execute(select(_day(Order.created_at), func.count()).where(Order.created_at >= since, Order.status.not_in([S.PENDING_PAYMENT, S.PAYMENT_FAILED])).group_by(literal_column("1"))).all()
    cust_rows = db.execute(select(_day(User.created_at), func.count()).where(User.role == UserRole.CUSTOMER, User.created_at >= since).group_by(literal_column("1"))).all()
    vend_rows = db.execute(select(_day(Vendor.approved_at), func.count()).where(Vendor.approved_at.is_not(None), Vendor.approved_at >= since).group_by(literal_column("1"))).all()
    foods = db.execute(select(OrderItem.name, func.sum(OrderItem.quantity).label("n")).join(Order, Order.id == OrderItem.order_id)
                       .where(Order.status.in_(COUNTED)).group_by(OrderItem.name).order_by(func.sum(OrderItem.quantity).desc()).limit(8)).all()
    vendors = db.execute(select(Vendor.name, func.count().label("n"), func.sum(Order.total)).join(Order, Order.vendor_id == Vendor.id)
                         .where(Order.status.in_(COUNTED)).group_by(Vendor.name).order_by(func.count().desc()).limit(8)).all()
    return {"cards": cards,
            "charts": {"revenue": _series(rev_rows), "orders": _series(ord_rows), "customer_growth": _series(cust_rows), "vendor_growth": _series(vend_rows),
                       "popular_foods": {"labels": [f[0] for f in foods], "values": [int(f[1]) for f in foods]},
                       "popular_vendors": {"labels": [v[0] for v in vendors], "values": [int(v[1]) for v in vendors], "revenue": [float(v[2] or 0) for v in vendors]}}}


def vendor_overview(db: Session, v: Vendor) -> dict:
    now = utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base = [Order.vendor_id == v.id, Order.status.in_(COUNTED)]
    sales = lambda *w: float(db.scalar(select(func.coalesce(func.sum(Order.subtotal), 0)).where(*base, *w)))
    n_orders = db.scalar(select(func.count()).select_from(Order).where(*base))
    since = now - timedelta(days=DAYS)
    rows = db.execute(select(_day(Order.paid_at), func.sum(Order.subtotal)).where(*base, Order.paid_at >= since).group_by(literal_column("1"))).all()
    hours = db.execute(select(func.extract("hour", func.timezone("UTC", Order.paid_at)), func.count()).where(*base, Order.paid_at >= since).group_by(literal_column("1"))).all()
    by_hour = {int(h): c for h, c in hours}
    foods = db.execute(select(OrderItem.name, func.sum(OrderItem.quantity), func.sum(OrderItem.line_total)).join(Order, Order.id == OrderItem.order_id)
                       .where(*base).group_by(OrderItem.name).order_by(func.sum(OrderItem.quantity).desc()).limit(8)).all()
    cust = db.execute(select(Order.customer_id, func.count()).where(*base).group_by(Order.customer_id)).all()
    repeat = sum(1 for _, c in cust if c > 1)
    return {"food_sales": {"total": sales(), "today": sales(Order.paid_at >= midnight), "week": sales(Order.paid_at >= now - timedelta(days=7)),
                           "month": sales(Order.paid_at >= now - timedelta(days=30))},
            "orders": {"total": n_orders, "active": db.scalar(select(func.count()).select_from(Order).where(Order.vendor_id == v.id, Order.status.in_(OPEN_STATES))),
                       "cancelled": db.scalar(select(func.count()).select_from(Order).where(Order.vendor_id == v.id, Order.status == S.CANCELLED))},
            "average_order_value": round(sales() / n_orders, 2) if n_orders else 0.0, "rating": round(v.rating_avg, 2), "reviews": v.rating_count,
            "repeat_customer_percent": round(100 * repeat / len(cust)) if cust else 0,
            "revenue_by_day": _series(rows), "orders_by_hour": {"labels": [f"{h:02d}:00" for h in range(24)], "values": [by_hour.get(h, 0) for h in range(24)]},
            "popular_foods": [{"name": n, "quantity": int(q), "sales": float(t)} for n, q, t in foods]}
