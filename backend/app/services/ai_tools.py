"""Secure AI tools for database access with role-based authorization.
Each tool validates authentication and enforces record-level access control."""
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.catalog import ApprovalStatus, MenuItem, Vendor
from app.models.orders import Order, OrderStatus
from app.models.riders import Delivery, DeliveryRider
from app.models.user import User, UserRole
from app.services.wallet_service import balances, vendor_account, rider_account


class AIToolAuth:
    """Helper to validate user authentication and role for AI tools."""

    @staticmethod
    def require_role(user: User | None, required_role: UserRole) -> User:
        """Ensure user is authenticated and has the required role."""
        if user is None:
            raise api_error(401, "auth_required", f"Please log in to your {required_role.value.lower()} account to access this information.")
        if user.role != required_role:
            raise api_error(403, "role_required", f"This information is only available to {required_role.value}s. Please log in with the correct account.")
        return user

    @staticmethod
    def require_authenticated(user: User | None) -> User:
        """Ensure user is authenticated (any role)."""
        if user is None:
            raise api_error(401, "auth_required", "Please log in to access your account information.")
        return user


def get_vendor_analytics(db: Session, user: User, days: int = 30) -> dict:
    """Vendor-specific analytics - only for authenticated vendors."""
    AIToolAuth.require_role(user, UserRole.VENDOR)

    vendor = db.scalar(select(Vendor).where(Vendor.owner_user_id == user.id))
    if not vendor:
        raise api_error(404, "vendor_not_found", "Vendor account not found.")

    since = utcnow() - timedelta(days=days)

    # Order statistics
    total_orders = db.scalar(
        select(func.count(Order.id))
        .where(Order.vendor_id == vendor.id, Order.created_at >= since)
    ) or 0

    completed_orders = db.scalar(
        select(func.count(Order.id))
        .where(Order.vendor_id == vendor.id, Order.status == OrderStatus.DELIVERED, Order.created_at >= since)
    ) or 0

    pending_orders = db.scalar(
        select(func.count(Order.id))
        .where(Order.vendor_id == vendor.id, Order.status.in_([OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PREPARING]))
    ) or 0

    # Revenue
    total_revenue = db.scalar(
        select(func.sum(Order.total))
        .where(Order.vendor_id == vendor.id, Order.paid_at.is_not(None), Order.paid_at >= since)
    ) or Decimal("0")

    # Top menu items
    top_items = db.execute(
        select(MenuItem.name, func.count(Order.id).label('count'), func.sum(Order.total).label('revenue'))
        .join(Order, Order.vendor_id == MenuItem.vendor_id)
        .where(MenuItem.vendor_id == vendor.id, Order.created_at >= since, Order.status.in_([OrderStatus.DELIVERED, OrderStatus.COLLECTED]))
        .group_by(MenuItem.id, MenuItem.name)
        .order_by(func.count(Order.id).desc())
        .limit(5)
    ).all()

    # Wallet balance
    account = vendor_account(db, vendor.id)
    bal = balances(db, account.id, utcnow())

    return {
        "vendor_name": vendor.name,
        "period_days": days,
        "total_orders": total_orders,
        "completed_orders": completed_orders,
        "pending_orders": pending_orders,
        "total_revenue": float(total_revenue),
        "available_balance": float(bal["withdrawable"]),
        "pending_balance": float(bal["pending"]),
        "total_balance": float(bal["total"]),
        "top_items": [{"name": name, "orders": count, "revenue": float(revenue)} for name, count, revenue in top_items],
    }


def get_rider_analytics(db: Session, user: User, days: int = 30) -> dict:
    """Rider-specific analytics - only for authenticated riders."""
    AIToolAuth.require_role(user, UserRole.RIDER)

    rider = db.scalar(select(DeliveryRider).where(DeliveryRider.user_id == user.id))
    if not rider:
        raise api_error(404, "rider_not_found", "Rider account not found.")

    since = utcnow() - timedelta(days=days)

    # Delivery statistics
    total_deliveries = db.scalar(
        select(func.count(Delivery.id))
        .where(Delivery.rider_id == rider.id, Delivery.created_at >= since)
    ) or 0

    completed_deliveries = db.scalar(
        select(func.count(Delivery.id))
        .where(Delivery.rider_id == rider.id, Delivery.delivered_at.is_not(None), Delivery.delivered_at >= since)
    ) or 0

    pending_deliveries = db.scalar(
        select(func.count(Delivery.id))
        .where(Delivery.rider_id == rider.id, Delivery.status.in_(["ASSIGNED", "PICKING_UP"]))
    ) or 0

    # Wallet balance
    account = rider_account(db, rider.id)
    bal = balances(db, account.id, utcnow())

    return {
        "rider_name": rider.name or "Rider",
        "period_days": days,
        "total_deliveries": total_deliveries,
        "completed_deliveries": completed_deliveries,
        "pending_deliveries": pending_deliveries,
        "available_balance": float(bal["withdrawable"]),
        "pending_balance": float(bal["pending"]),
        "total_balance": float(bal["total"]),
        "is_online": rider.is_online,
    }


def get_admin_analytics(db: Session, user: User, days: int = 30) -> dict:
    """Admin platform analytics - only for authenticated admins."""
    AIToolAuth.require_role(user, UserRole.ADMIN)

    since = utcnow() - timedelta(days=days)

    # Platform-wide statistics
    total_users = db.scalar(select(func.count(User.id))) or 0
    total_vendors = db.scalar(select(func.count(Vendor.id))) or 0
    total_riders = db.scalar(select(func.count(DeliveryRider.id))) or 0

    total_orders = db.scalar(
        select(func.count(Order.id))
        .where(Order.created_at >= since)
    ) or 0

    total_revenue = db.scalar(
        select(func.sum(Order.total))
        .where(Order.paid_at.is_not(None), Order.paid_at >= since)
    ) or Decimal("0")

    # Order status breakdown
    order_status_counts = db.execute(
        select(Order.status, func.count(Order.id))
        .where(Order.created_at >= since)
        .group_by(Order.status)
    ).all()

    return {
        "period_days": days,
        "total_users": total_users,
        "total_vendors": total_vendors,
        "total_riders": total_riders,
        "total_orders": total_orders,
        "total_revenue": float(total_revenue),
        "order_status_breakdown": {status.value: count for status, count in order_status_counts},
    }


def get_customer_orders(db: Session, user: User, limit: int = 10) -> dict:
    """Customer's own orders - only for authenticated customers."""
    AIToolAuth.require_role(user, UserRole.CUSTOMER)

    orders = db.scalars(
        select(Order)
        .where(Order.customer_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    ).all()

    return {
        "orders": [
            {
                "code": o.code,
                "status": o.status.value,
                "total": float(o.total),
                "created_at": o.created_at.isoformat(),
                "vendor_name": o.vendor.name if o.vendor else None,
            }
            for o in orders
        ],
    }


def get_user_role_analytics(db: Session, user: User, days: int = 30) -> dict:
    """Route to appropriate analytics based on user role."""
    if user.role == UserRole.ADMIN:
        return get_admin_analytics(db, user, days)
    elif user.role == UserRole.VENDOR:
        return get_vendor_analytics(db, user, days)
    elif user.role == UserRole.RIDER:
        return get_rider_analytics(db, user, days)
    elif user.role == UserRole.CUSTOMER:
        return {"orders": get_customer_orders(db, user), "role": "customer"}
    else:
        raise api_error(403, "unknown_role", "Unable to determine account permissions.")
