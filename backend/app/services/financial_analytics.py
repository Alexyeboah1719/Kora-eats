"""Financial analytics for dashboards - earnings breakdown, commissions, and withdrawal history."""
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.catalog import Vendor
from app.models.orders import Order, OrderStatus
from app.models.riders import Delivery, DeliveryRider
from app.models.wallet import LedgerEntry, LedgerKind as K, WalletAccount, WalletOwner, Withdrawal, WithdrawalStatus
from app.services.wallet_service import balances, vendor_account, rider_account, platform_account, rates


def vendor_earnings_summary(db: Session, vendor_id: int) -> dict:
    """Complete earnings breakdown for vendor dashboard."""
    account = vendor_account(db, vendor_id)
    bal = balances(db, account.id, utcnow())

    # Calculate totals from ledger
    total_earnings = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(LedgerEntry.account_id == account.id, LedgerEntry.kind == K.ORDER_SALE)
    ) or Decimal("0")

    total_commission = db.scalar(
        select(func.sum(func.abs(LedgerEntry.amount)))
        .where(LedgerEntry.account_id == account.id, LedgerEntry.kind == K.ORDER_COMMISSION)
    ) or Decimal("0")

    total_discount_funded = db.scalar(
        select(func.sum(func.abs(LedgerEntry.amount)))
        .where(LedgerEntry.account_id == account.id, LedgerEntry.kind == K.DISCOUNT_FUNDING)
    ) or Decimal("0")

    # Withdrawal history
    withdrawals = db.scalars(
        select(Withdrawal)
        .where(Withdrawal.account_id == account.id)
        .order_by(Withdrawal.requested_at.desc())
        .limit(20)
    ).all()

    total_withdrawn = sum(w.amount for w in withdrawals if w.status == WithdrawalStatus.SUCCESS)
    pending_withdrawals = sum(w.amount for w in withdrawals if w.status in (WithdrawalStatus.PENDING_APPROVAL, WithdrawalStatus.PROCESSING))

    vendor = db.get(Vendor, vendor_id)
    commission_rate = rates()[0]

    return {
        "vendor_name": vendor.name if vendor else "Unknown",
        "total_earnings": float(total_earnings),
        "platform_commission": float(total_commission),
        "commission_rate": float(commission_rate),
        "vendor_discount_cost": float(total_discount_funded),
        "net_earnings": float(total_earnings - total_commission - total_discount_funded),
        "withdrawn": float(total_withdrawn),
        "pending_withdrawals": float(pending_withdrawals),
        "available_balance": float(bal["withdrawable"]),
        "total_balance": float(bal["total"]),
        "pending_hold": float(bal["pending"]),
        "withdrawal_history": [
            {
                "id": w.id,
                "amount": float(w.amount),
                "fee": float(w.fee),
                "status": w.status.value,
                "requested_at": w.requested_at.isoformat(),
                "processed_at": w.processed_at.isoformat() if w.processed_at else None,
                "reference": w.reference,
                "failure_reason": w.failure_reason,
            }
            for w in withdrawals
        ],
    }


def rider_earnings_summary(db: Session, rider_id: int) -> dict:
    """Complete earnings breakdown for rider dashboard."""
    account = rider_account(db, rider_id)
    bal = balances(db, account.id, utcnow())

    # Calculate totals from ledger
    total_earnings = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(LedgerEntry.account_id == account.id, LedgerEntry.kind == K.DELIVERY_PAY)
    ) or Decimal("0")

    total_commission = db.scalar(
        select(func.sum(func.abs(LedgerEntry.amount)))
        .where(LedgerEntry.account_id == account.id, LedgerEntry.kind == K.DELIVERY_COMMISSION)
    ) or Decimal("0")

    # Withdrawal history
    withdrawals = db.scalars(
        select(Withdrawal)
        .where(Withdrawal.account_id == account.id)
        .order_by(Withdrawal.requested_at.desc())
        .limit(20)
    ).all()

    total_withdrawn = sum(w.amount for w in withdrawals if w.status == WithdrawalStatus.SUCCESS)
    pending_withdrawals = sum(w.amount for w in withdrawals if w.status in (WithdrawalStatus.PENDING_APPROVAL, WithdrawalStatus.PROCESSING))

    rider = db.get(DeliveryRider, rider_id)
    commission_rate = Decimal(100) - rates()[1]  # Platform deduction rate

    return {
        "rider_name": rider.name if rider else "Unknown",
        "total_delivery_earnings": float(total_earnings),
        "platform_commission": float(total_commission),
        "commission_rate": float(commission_rate),
        "net_earnings": float(total_earnings - total_commission),
        "withdrawn": float(total_withdrawn),
        "pending_withdrawals": float(pending_withdrawals),
        "available_balance": float(bal["withdrawable"]),
        "total_balance": float(bal["total"]),
        "pending_hold": float(bal["pending"]),
        "withdrawal_history": [
            {
                "id": w.id,
                "amount": float(w.amount),
                "fee": float(w.fee),
                "status": w.status.value,
                "requested_at": w.requested_at.isoformat(),
                "processed_at": w.processed_at.isoformat() if w.processed_at else None,
                "reference": w.reference,
                "failure_reason": w.failure_reason,
            }
            for w in withdrawals
        ],
    }


def platform_financial_overview(db: Session, days: int = 30) -> dict:
    """Complete financial overview for admin dashboard."""
    now = utcnow()
    since = now - timedelta(days=days)

    # Platform wallet
    platform = platform_account(db)
    platform_bal = balances(db, platform.id, now)

    # Total vendor earnings (before commission)
    vendor_commission_total = db.scalar(
        select(func.sum(func.abs(LedgerEntry.amount)))
        .where(LedgerEntry.kind == K.ORDER_COMMISSION, LedgerEntry.created_at >= since)
    ) or Decimal("0")

    # Total rider earnings (before commission)
    rider_commission_total = db.scalar(
        select(func.sum(func.abs(LedgerEntry.amount)))
        .where(LedgerEntry.kind == K.DELIVERY_COMMISSION, LedgerEntry.created_at >= since)
    ) or Decimal("0")

    # Platform discount costs
    platform_discount_total = db.scalar(
        select(func.sum(func.abs(LedgerEntry.amount)))
        .where(LedgerEntry.kind == K.PLATFORM_DISCOUNT, LedgerEntry.created_at >= since)
    ) or Decimal("0")

    # Withdrawal fees
    withdrawal_fees = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(LedgerEntry.kind == K.WITHDRAWAL_FEE, LedgerEntry.created_at >= since)
    ) or Decimal("0")

    # All withdrawals
    all_withdrawals = db.scalars(
        select(Withdrawal)
        .where(Withdrawal.requested_at >= since)
        .order_by(Withdrawal.requested_at.desc())
    ).all()

    pending_count = sum(1 for w in all_withdrawals if w.status in (WithdrawalStatus.PENDING_APPROVAL, WithdrawalStatus.PROCESSING))
    failed_count = sum(1 for w in all_withdrawals if w.status == WithdrawalStatus.FAILED)
    successful_count = sum(1 for w in all_withdrawals if w.status == WithdrawalStatus.SUCCESS)
    total_withdrawn_amount = sum(w.amount for w in all_withdrawals if w.status == WithdrawalStatus.SUCCESS)

    # Revenue from orders
    total_orders_revenue = db.scalar(
        select(func.sum(Order.total))
        .where(Order.paid_at.is_not(None), Order.paid_at >= since, Order.status.in_([OrderStatus.DELIVERED, OrderStatus.COLLECTED]))
    ) or Decimal("0")

    commission_rate_vendor, rider_keep_rate = rates()
    platform_deduction_rate = Decimal(100) - rider_keep_rate

    return {
        "period_days": days,
        "platform_balance": float(platform_bal["total"]),
        "platform_withdrawable": float(platform_bal["withdrawable"]),

        # Revenue breakdown
        "total_orders_revenue": float(total_orders_revenue),
        "vendor_commission_revenue": float(vendor_commission_total),
        "rider_commission_revenue": float(rider_commission_total),
        "platform_discount_cost": float(platform_discount_total),
        "withdrawal_fees": float(withdrawal_fees),
        "total_platform_revenue": float(vendor_commission_total + rider_commission_total + withdrawal_fees - platform_discount_total),

        # Commission rates
        "vendor_commission_rate": float(commission_rate_vendor),
        "rider_commission_rate": float(platform_deduction_rate),

        # Withdrawal stats
        "total_withdrawals": len(all_withdrawals),
        "pending_withdrawals": pending_count,
        "failed_withdrawals": failed_count,
        "successful_withdrawals": successful_count,
        "total_withdrawn_amount": float(total_withdrawn_amount),

        # Recent withdrawals for admin review
        "recent_withdrawals": [
            {
                "id": w.id,
                "amount": float(w.amount),
                "fee": float(w.fee),
                "status": w.status.value,
                "requested_at": w.requested_at.isoformat(),
                "owner_type": db.get(WalletAccount, w.account_id).owner_type.value,
                "reference": w.reference,
            }
            for w in all_withdrawals[:50]
        ],
    }


def order_earnings_detail(db: Session, order_id: int) -> dict:
    """Detailed earnings breakdown for a single order (for vendor/rider dashboards)."""
    order = db.get(Order, order_id)
    if not order:
        return None

    from app.services.wallet_service import economics

    delivery = db.scalar(select(Delivery).where(Delivery.order_id == order_id))
    rider = db.get(DeliveryRider, delivery.rider_id) if delivery else None

    econ = economics(order, rider_involved=rider is not None)

    return {
        "order_code": order.code,
        "order_total": float(order.total),
        "customer_paid": float(econ.customer_paid),

        # Vendor breakdown
        "vendor_food_sales": float(econ.subtotal),
        "vendor_commission": float(econ.vendor_commission),
        "vendor_commission_rate": float(econ.vendor_commission_rate),
        "vendor_discount_cost": float(econ.vendor_discount_cost),
        "vendor_net": float(econ.vendor_net),

        # Rider breakdown
        "rider_delivery_fee": float(econ.delivery_fee),
        "rider_commission": float(econ.rider_commission),
        "rider_commission_rate": float(econ.rider_commission_rate),
        "rider_net": float(econ.rider_net),

        # Platform
        "platform_commission_total": float(econ.vendor_commission + econ.rider_commission),
        "platform_net": float(econ.platform_net),

        "notes": econ.notes,
    }
