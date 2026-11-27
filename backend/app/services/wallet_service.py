"""Wallet service for vendor and rider earnings, COD balance management, and commission calculation."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.catalog import Vendor
from app.models.riders import DeliveryRider
from app.models.wallet import (
    LedgerEntry,
    LedgerKind,
    WalletAccount,
    WalletOwner,
    Withdrawal,
    WithdrawalStatus,
)

logger = logging.getLogger(__name__)


def vendor_account(db: Session, vendor_id: int) -> WalletAccount:
    """Get or create vendor wallet account."""
    wallet = db.scalar(
        select(WalletAccount).where(
            WalletAccount.owner_type == WalletOwner.VENDOR,
            WalletAccount.vendor_id == vendor_id
        )
    )
    if not wallet:
        wallet = WalletAccount(owner_type=WalletOwner.VENDOR, vendor_id=vendor_id)
        db.add(wallet)
        db.flush()
    return wallet


def rider_account(db: Session, rider_id: int) -> WalletAccount:
    """Get or create rider wallet account."""
    wallet = db.scalar(
        select(WalletAccount).where(
            WalletAccount.owner_type == WalletOwner.RIDER,
            WalletAccount.rider_id == rider_id
        )
    )
    if not wallet:
        wallet = WalletAccount(owner_type=WalletOwner.RIDER, rider_id=rider_id)
        db.add(wallet)
        db.flush()
    return wallet


def platform_account(db: Session) -> WalletAccount:
    """Get or create the platform's own wallet account."""
    wallet = db.scalar(
        select(WalletAccount).where(WalletAccount.owner_type == WalletOwner.PLATFORM)
    )
    if not wallet:
        wallet = WalletAccount(owner_type=WalletOwner.PLATFORM)
        db.add(wallet)
        db.flush()
    return wallet


def rates() -> tuple[float, int]:
    """Return (vendor_commission_percent, rider_share_percent)."""
    settings = get_settings()
    return settings.vendor_commission_percent, settings.rider_share_percent


def balances(db: Session, account_id: int, now=None) -> dict:
    """Return balance breakdown: available, pending, total, cash_in_hand."""
    if now is None:
        now = utcnow()
    
    # Available balance
    available = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.available_at <= now
        )
    ) or Decimal("0")
    
    # Pending balance
    pending = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.available_at > now
        )
    ) or Decimal("0")
    
    total = available + pending
    
    # Cash physically in the rider's hand: COD collections are negative ledger entries,
    # remittances positive, so the negated net is what is still unremitted.
    cash_net = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.kind.in_([LedgerKind.COD_CASH_COLLECTED, LedgerKind.COD_REMITTANCE])
        )
    ) or Decimal("0")
    cash_in_hand = -cash_net
    
    earning_kinds = [LedgerKind.ORDER_SALE, LedgerKind.ORDER_COMMISSION, LedgerKind.DISCOUNT_FUNDING,
                     LedgerKind.DELIVERY_PAY, LedgerKind.DELIVERY_COMMISSION, LedgerKind.COD_CASH_COLLECTED,
                     LedgerKind.COD_REMITTANCE]
    commission_kinds = [LedgerKind.ORDER_COMMISSION, LedgerKind.DISCOUNT_FUNDING, LedgerKind.DELIVERY_COMMISSION,
                        LedgerKind.PLATFORM_DISCOUNT]
    lifetime = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0))
                         .where(LedgerEntry.account_id == account_id, LedgerEntry.kind.in_(earning_kinds))) or Decimal("0")
    commission = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0))
                           .where(LedgerEntry.account_id == account_id, LedgerEntry.kind.in_(commission_kinds))) or Decimal("0")

    return {
        "available": available,
        "pending": pending,
        "total": total,
        "withdrawable": max(available, Decimal("0")),
        "cash_in_hand": cash_in_hand,
        "lifetime_earnings": lifetime,
        "commission_paid": -commission,
    }


def statement(db: Session, account: WalletAccount, limit: int = 30, offset: int = 0) -> list:
    """Per-order statement: every ledger entry grouped by the order it belongs to.

    Each row: {order_id, code, order_status, you_earn, state, parts, rates, warnings, entries}.
    """
    from app.models.orders import Order, OrderStatus as _S
    now = utcnow()
    # newest activity first: rank groups by their latest ledger entry
    grouped = db.execute(
        select(LedgerEntry.order_id, func.max(LedgerEntry.id).label("last_id"))
        .where(LedgerEntry.account_id == account.id)
        .group_by(LedgerEntry.order_id)
        .order_by(func.max(LedgerEntry.id).desc())
        .limit(limit).offset(offset)
    ).all()
    if not grouped:
        return []
    order_ids = [g.order_id for g in grouped if g.order_id is not None]
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == account.id,
                                                 LedgerEntry.order_id.in_(order_ids) if order_ids else False)
                         .order_by(LedgerEntry.id)).all() if order_ids else []
    misc = db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == account.id, LedgerEntry.order_id.is_(None))
                      .order_by(LedgerEntry.id)).all() if any(g.order_id is None for g in grouped) else []
    orders = {o.id: o for o in db.scalars(select(Order).where(Order.id.in_(order_ids)))} if order_ids else {}
    by_order: dict = {}
    for e in entries:
        by_order.setdefault(e.order_id, []).append(e)
    if misc:
        by_order[None] = misc

    out = []
    for g in grouped:
        es = by_order.get(g.order_id, [])
        if not es:
            continue
        o = orders.get(g.order_id) if g.order_id else None
        parts, rates_meta, warnings = [], {}, []
        held = False
        for e in es:
            parts.append({"kind": e.kind.value, "amount": float(e.amount), "note": e.note,
                          "available_at": e.available_at.isoformat(), "created_at": e.created_at.isoformat()})
            m = e.meta or {}
            if "vendor_rate" in m:
                rates_meta["vendor_commission"] = m["vendor_rate"]
            if "rider_rate" in m:
                rates_meta["rider_deduction"] = m["rider_rate"]
            if e.available_at > now:
                held = True
        you_earn = float(sum((e.amount for e in es), Decimal("0")))
        state = "REFUNDED" if o is not None and o.status.value == "REFUNDED" else ("HELD" if held else "AVAILABLE")
        if held:
            latest = max(e.available_at for e in es if e.available_at > now)
            warnings.append(f"Held until {latest:%d %b %Y %H:%M} — releases automatically for disputes and refunds.")
        if state == "REFUNDED":
            warnings.append("Refunded: this order was refunded and the earnings were reversed.")
        out.append({"order_id": g.order_id, "code": o.code if o else None,
                    "order_status": o.status.value if o else None, "you_earn": you_earn, "state": state,
                    "parts": parts, "rates": rates_meta, "warnings": warnings,
                    "entries": [{"id": e.id, "kind": e.kind.value, "amount": float(e.amount),
                                 "available_at": e.available_at.isoformat(), "created_at": e.created_at.isoformat(),
                                 "note": e.note} for e in es]})
    return out


def upcoming(db: Session, vendor_id: int = None, rider_id: int = None) -> list:
    """What in-progress orders will earn once they complete (nothing is in the ledger yet)."""
    from app.models.orders import Order, OrderStatus as _S
    from app.models.riders import Delivery as _D
    in_progress = [_S.PAID, _S.CONFIRMED, _S.PREPARING, _S.READY_FOR_PICKUP, _S.RIDER_ASSIGNED, _S.PICKED_UP, _S.OUT_FOR_DELIVERY, _S.RIDER_ARRIVED]
    q_ = select(Order).where(Order.status.in_(in_progress), Order.earnings_posted_at.is_(None))
    if vendor_id:
        orders = db.scalars(q_.where(Order.vendor_id == vendor_id).order_by(Order.id.desc()).limit(20)).all()
        vendor_pct, _ = rates()
        out = []
        for o in orders:
            e = economics(o, Decimal(str(vendor_pct)), 0, rider_involved=False)
            net = e.vendor_net
            if getattr(o, "payment_mode", "ONLINE") == "COD" and o.fulfillment_type == FulfillmentType.PICKUP:
                net = q(net - o.total)      # vendor keeps the cash; the wallet will show minus the commission owed
            out.append({"order_id": o.id, "code": o.code, "order_status": o.status.value,
                        "you_will_earn": float(net), "available_after": "delivery"})
        return out
    if rider_id:
        rows = db.execute(select(Order).join(_D, _D.order_id == Order.id).where(_D.rider_id == rider_id, Order.status.in_(in_progress),
                                                                               Order.earnings_posted_at.is_(None))
                          .order_by(Order.id.desc()).limit(20)).all()
        _, rider_keep = rates()
        out = []
        for (o,) in rows:
            e = economics(o, Decimal("0"), rider_keep, rider_involved=True)
            out.append({"order_id": o.id, "code": o.code, "order_status": o.status.value,
                        "you_will_earn": float(e.rider_net), "available_after": "delivery"})
        return out
    return []


def q(x: Decimal) -> Decimal:
    """Quantize to 2 decimal places."""
    return (x if isinstance(x, Decimal) else Decimal(str(x))).quantize(Decimal("0.01"))


def get_wallet_balance(db: Session, account_id: int) -> tuple[Decimal, Decimal]:
    """Returns (available_balance, pending_balance) for a wallet account.
    
    Available: ledger entries where available_at <= now
    Pending: ledger entries where available_at > now
    """
    now = utcnow()
    
    # Available balance
    available = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.available_at <= now
        )
    ) or Decimal("0")
    
    # Pending balance
    pending = db.scalar(
        select(func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.available_at > now
        )
    ) or Decimal("0")
    
    return available, pending


def get_rider_cod_balance(db: Session, rider_id: int) -> Decimal:
    """Calculate rider's COD balance (negative = owes money to platform).
    
    Positive balance means rider has paid the platform more than they collected.
    Negative balance means rider collected cash but hasn't remitted it yet.
    """
    settings = get_settings()
    
    wallet = db.scalar(
        select(WalletAccount).where(
            WalletAccount.owner_type == WalletOwner.RIDER,
            WalletAccount.rider_id == rider_id
        )
    )
    
    if not wallet:
        return Decimal("0")
    
    # COD_CASH_COLLECTED is stored negative (cash the rider owes back), COD_REMITTANCE positive.
    # The negated net is the cash still in hand: positive = unremitted, negative = over-remitted.
    net = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .where(
            LedgerEntry.account_id == wallet.id,
            LedgerEntry.kind.in_([LedgerKind.COD_CASH_COLLECTED, LedgerKind.COD_REMITTANCE])
        )
    ) or Decimal("0")
    return -net


def is_rider_cod_blocked(db: Session, rider_id: int) -> tuple[bool, str]:
    """Check if rider is blocked from taking COD orders due to negative balance.
    
    Returns (is_blocked, reason).
    """
    settings = get_settings()
    
    if not settings.cod_enabled:
        return True, "COD is currently disabled"
    
    cod_balance = get_rider_cod_balance(db, rider_id)
    max_cash = Decimal(str(settings.cod_rider_max_cash_in_hand))

    if cod_balance > max_cash:
        return True, f"You have GH₵{cod_balance:.2f} unremitted cash. Please remit at least GH₵{cod_balance - max_cash:.2f} to take more pay-on-delivery orders."
    
    return False, ""


def record_vendor_earnings(
    db: Session,
    order_id: int,
    vendor_id: int,
    subtotal: Decimal,
    discount: Decimal,
    delivery_fee: Decimal,
    commission_percent: float,
) -> None:
    """Record vendor earnings after platform commission.
    
    Ledger entries:
    - ORDER_SALE: +subtotal (food sales)
    - ORDER_COMMISSION: -commission (platform's cut)
    - DISCOUNT_FUNDING: -discount (if vendor funded the discount)
    """
    settings = get_settings()
    
    # Get or create vendor wallet
    wallet = db.scalar(
        select(WalletAccount).where(
            WalletAccount.owner_type == WalletOwner.VENDOR,
            WalletAccount.vendor_id == vendor_id
        )
    )
    
    if not wallet:
        # Create wallet for vendor
        from app.models.vendor import Vendor
        wallet = WalletAccount(owner_type=WalletOwner.VENDOR, vendor_id=vendor_id)
        db.add(wallet)
        db.flush()
    
    # Calculate commission
    commission_amount = (subtotal - discount) * Decimal(str(commission_percent / 100))
    earnings = subtotal - discount - commission_amount
    
    # Hold earnings for payout hold period
    available_at = utcnow() + timedelta(hours=settings.payout_hold_hours)
    
    # Record food sale
    db.add(LedgerEntry(
        account_id=wallet.id,
        order_id=order_id,
        kind=LedgerKind.ORDER_SALE,
        amount=subtotal,
        available_at=available_at,
        note=f"Order #{order_id} food sales"
    ))
    
    # Record platform commission
    db.add(LedgerEntry(
        account_id=wallet.id,
        order_id=order_id,
        kind=LedgerKind.ORDER_COMMISSION,
        amount=-commission_amount,
        available_at=available_at,
        note=f"Platform commission ({commission_percent}%)"
    ))
    
    # Record discount funding (if vendor funded)
    if discount > 0:
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=order_id,
            kind=LedgerKind.DISCOUNT_FUNDING,
            amount=-discount,
            available_at=available_at,
            note=f"Vendor-funded discount"
        ))
    
    db.commit()
    logger.info(f"Recorded vendor {vendor_id} earnings: GH₵{earnings:.2f} after {commission_percent}% commission")


def record_rider_earnings(
    db: Session,
    order_id: int,
    rider_id: int,
    delivery_fee: Decimal,
    rider_share_percent: float,
    payment_mode: str,
) -> None:
    """Record rider earnings after platform commission.
    
    Ledger entries:
    - DELIVERY_PAY: +rider_share (online payments only)
    - DELIVERY_COMMISSION: -platform_share (both online and COD)
    - COD_CASH_COLLECTED: -delivery_fee (COD: rider collected cash, owes platform)
    
    For COD: rider collects full fee, but still pays commission when remitting
    For online: rider gets their share immediately (minus platform cut)
    """
    settings = get_settings()
    
    # Get or create rider wallet
    wallet = db.scalar(
        select(WalletAccount).where(
            WalletAccount.owner_type == WalletOwner.RIDER,
            WalletAccount.rider_id == rider_id
        )
    )
    
    if not wallet:
        from app.models.rider import DeliveryRider
        wallet = WalletAccount(owner_type=WalletOwner.RIDER, rider_id=rider_id)
        db.add(wallet)
        db.flush()
    
    # Hold earnings for payout hold period
    available_at = utcnow() + timedelta(hours=settings.payout_hold_hours)
    
    # Calculate rider and platform shares (same for both online and COD)
    rider_share = delivery_fee * Decimal(str(rider_share_percent / 100))
    platform_share = delivery_fee - rider_share
    
    if payment_mode == "COD":
        # COD: rider owes the platform the full delivery fee
        # When they remit, they'll get their share minus commission
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=order_id,
            kind=LedgerKind.COD_CASH_COLLECTED,
            amount=-delivery_fee,
            available_at=available_at,
            note=f"COD: collected GH₵{delivery_fee:.2f} from customer (owes platform GH₵{platform_share:.2f} commission)"
        ))
        
        logger.info(f"Recorded rider {rider_id} COD collection: GH₵{delivery_fee:.2f} (commission GH₵{platform_share:.2f} will be deducted on remittance)")
    else:
        # Online payment: rider gets their share immediately
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=order_id,
            kind=LedgerKind.DELIVERY_PAY,
            amount=rider_share,
            available_at=available_at,
            note=f"Delivery fee (rider share {rider_share_percent}%)"
        ))
        
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=order_id,
            kind=LedgerKind.DELIVERY_COMMISSION,
            amount=-platform_share,
            available_at=available_at,
            note=f"Platform commission on delivery fee"
        ))
        
        logger.info(f"Recorded rider {rider_id} earnings: GH₵{rider_share:.2f} after GH₵{platform_share:.2f} platform cut")


def record_platform_earnings(
    db: Session,
    order_id: int,
    vendor_commission: Decimal,
    rider_commission: Decimal,
    platform_discount: Decimal,
) -> None:
    """Record platform earnings from commissions and discounts.
    
    Ledger entries:
    - ORDER_COMMISSION: +vendor_commission
    - DELIVERY_COMMISSION: +rider_commission
    - PLATFORM_DISCOUNT: -platform_discount (if platform funded discount)
    """
    # Get or create platform wallet
    wallet = platform_account(db)
    
    available_at = utcnow()
    
    # Record vendor commission
    if vendor_commission > 0:
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=order_id,
            kind=LedgerKind.ORDER_COMMISSION,
            amount=vendor_commission,
            available_at=available_at,
            note=f"Vendor commission from order #{order_id}"
        ))
    
    # Record rider commission
    if rider_commission > 0:
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=order_id,
            kind=LedgerKind.DELIVERY_COMMISSION,
            amount=rider_commission,
            available_at=available_at,
            note=f"Rider commission from order #{order_id}"
        ))
    
    # Record platform-funded discount
    if platform_discount > 0:
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=order_id,
            kind=LedgerKind.PLATFORM_DISCOUNT,
            amount=-platform_discount,
            available_at=available_at,
            note=f"Platform-funded discount"
        ))
    
    db.commit()
    logger.info(f"Recorded platform earnings: GH₵{vendor_commission + rider_commission:.2f}")


def process_cod_remittance(
    db: Session,
    rider_id: int,
    amount: Decimal,
    reference: str,
) -> tuple[Decimal, Decimal]:
    """Process rider remitting COD cash to the platform.
    
    When a rider pays the platform for COD cash collected:
    1. Record COD_REMITTANCE (+amount to rider wallet)
    2. For each COD order, deduct commission and give rider their share
    3. Record platform commission
    
    Returns (amount_processed, rider_net_earnings)
    """
    settings = get_settings()
    
    # Get rider wallet
    wallet = db.scalar(
        select(WalletAccount).where(
            WalletAccount.owner_type == WalletOwner.RIDER,
            WalletAccount.rider_id == rider_id
        )
    )
    
    if not wallet:
        raise ValueError("Rider wallet not found")
    
    # Get all unremitted COD collections
    cod_collections = db.scalars(
        select(LedgerEntry).where(
            LedgerEntry.account_id == wallet.id,
            LedgerEntry.kind == LedgerKind.COD_CASH_COLLECTED,
            LedgerEntry.withdrawal_id.is_(None)  # Not yet remitted
        )
    ).all()
    
    if not cod_collections:
        raise ValueError("No unremitted COD collections found")
    
    available_at = utcnow()
    total_processed = Decimal("0")
    rider_net = Decimal("0")
    
    # Process each COD collection
    for collection in cod_collections:
        if total_processed >= amount:
            break
        
        delivery_fee = abs(collection.amount)
        rider_share_percent = settings.rider_share_percent
        rider_share = delivery_fee * Decimal(str(rider_share_percent / 100))
        platform_share = delivery_fee - rider_share
        
        # Mark this collection as remitted by linking to a withdrawal
        # For now, we'll just add the remittance entry
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=collection.order_id,
            kind=LedgerKind.COD_REMITTANCE,
            amount=delivery_fee,
            available_at=available_at,
            note=f"COD remittance via {reference}"
        ))
        
        # Deduct platform commission
        db.add(LedgerEntry(
            account_id=wallet.id,
            order_id=collection.order_id,
            kind=LedgerKind.DELIVERY_COMMISSION,
            amount=-platform_share,
            available_at=available_at,
            note=f"Platform commission on COD delivery fee ({100 - rider_share_percent}%)"
        ))
        
        # Record platform commission
        platform_wallet = platform_account(db)
        
        db.add(LedgerEntry(
            account_id=platform_wallet.id,
            order_id=collection.order_id,
            kind=LedgerKind.DELIVERY_COMMISSION,
            amount=platform_share,
            available_at=available_at,
            note=f"Rider COD commission from order #{collection.order_id}"
        ))
        
        total_processed += delivery_fee
        rider_net += rider_share
        
        logger.info(f"Processed COD remittance for order {collection.order_id}: GH₵{delivery_fee:.2f} collected, GH₵{platform_share:.2f} commission, GH₵{rider_share:.2f} to rider")
    
    db.commit()
    
    return total_processed, rider_net


# ------------------------------------------------------------------ posting earnings when an order completes
from dataclasses import dataclass  # noqa: E402


@dataclass
class Economics:
    vendor_commission: Decimal
    vendor_net: Decimal
    rider_net: Decimal
    rider_commission: Decimal
    platform_net: Decimal
    platform_discount: Decimal
    customer_paid: Decimal
    funded_by: str | None
    subtotal: Decimal = Decimal("0")


def economics(order, vendor_pct: Decimal, rider_keep_pct: int, rider_involved: bool = True) -> Economics:
    """Who ends up with what from one completed order. Conservation: the three nets always sum to what the customer paid.

    - Vendor commission is a % of food sales. A VENDOR-funded coupon reduces the commission base; a PLATFORM-funded one does not.
    - A VENDOR-funded discount (including one that waived delivery) is borne by the vendor.
    - The rider keeps rider_keep_pct of the delivery fee; the rest is the platform's cut. Pickup orders have no rider share.
    """
    funded_by = getattr(order, "coupon_funded_by", None)
    discount = q(order.discount or Decimal("0"))
    delivery_part = q(getattr(order, "discount_delivery_part", Decimal("0")) or Decimal("0"))
    subtotal = q(order.subtotal)
    fee = q(order.delivery_fee or Decimal("0"))
    total = q(order.total)

    vendor_discount = discount if funded_by == "VENDOR" else Decimal("0")
    food_discount = discount - delivery_part if funded_by == "VENDOR" else Decimal("0")
    vendor_commission = q((subtotal - food_discount) * vendor_pct / Decimal("100"))
    vendor_net = q(subtotal - vendor_commission - vendor_discount)

    rider_net = q(fee * Decimal(rider_keep_pct) / Decimal("100")) if (rider_involved and fee > 0) else Decimal("0")
    rider_commission = q(fee - rider_net) if rider_involved else Decimal("0")
    platform_discount = discount if funded_by == "PLATFORM" else Decimal("0")
    platform_net = q(total - vendor_net - rider_net)
    return Economics(vendor_commission, vendor_net, rider_net, rider_commission, platform_net,
                     platform_discount, total, funded_by, subtotal)


def _ledger(db: Session, account_id: int, order: Order, kind: LedgerKind, amount: Decimal, available_at, note: str,
            meta: dict | None = None) -> None:
    db.add(LedgerEntry(account_id=account_id, order_id=order.id, kind=kind, amount=q(amount), available_at=available_at,
                       note=note[:200], meta=meta or {}))


def post_order(db: Session, order: Order) -> bool:
    """Write the vendor/rider/platform earnings for a completed order into the ledger. Idempotent."""
    if order.earnings_posted_at is not None:
        return False
    if db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.order_id == order.id,
                                                                     LedgerEntry.kind != LedgerKind.REFUND_REVERSAL)):
        order.earnings_posted_at = utcnow()                    # entries exist: the marker was lost, restore it, never pay twice
        return False
    now = utcnow()
    hold = now + timedelta(hours=get_settings().payout_hold_hours)
    vendor_pct, rider_keep = rates()
    vendor_involved = True
    delivery = db.scalar(select(Delivery).where(Delivery.order_id == order.id)) if order.fulfillment_type != FulfillmentType.PICKUP else None
    rider_involved = delivery is not None and order.delivery_fee and order.delivery_fee > 0
    e = economics(order, Decimal(str(vendor_pct)), rider_keep, rider_involved=rider_involved)
    code = order.code
    is_cod = getattr(order, "payment_mode", "ONLINE") == "COD"

    # --- vendor: food sales less commission and any discount the vendor agreed to fund
    v = vendor_account(db, order.vendor_id)
    _ledger(db, v.id, order, LedgerKind.ORDER_SALE, order.subtotal, hold, f"{code}: food sales")
    _ledger(db, v.id, order, LedgerKind.ORDER_COMMISSION, -e.vendor_commission, hold,
            f"{code}: platform commission ({vendor_pct}%)", {"vendor_rate": str(vendor_pct), "order": code})
    if e.funded_by == "VENDOR" and order.discount and order.discount > 0:
        _ledger(db, v.id, order, LedgerKind.DISCOUNT_FUNDING, -order.discount, hold, f"{code}: discount you funded")

    # COD pickup: no rider is involved, so the vendor collected the whole total in cash. The
    # COD_CASH_COLLECTED entry records the cash they physically hold; their wallet nets to minus
    # the commission they owe KORA EATS, which their future earnings repay automatically.
    if is_cod and order.fulfillment_type == FulfillmentType.PICKUP:
        _ledger(db, v.id, order, LedgerKind.COD_CASH_COLLECTED, -order.total, now,
                f"{code}: COD cash collected GH₵{order.total} — commission owed to KORA EATS")

    # --- rider
    if delivery is not None:
        r = rider_account(db, delivery.rider_id)
        if is_cod:
            # The rider physically holds the whole order total in cash. The DELIVERY_PAY entry is what they
            # keep; COD_CASH_COLLECTED is what they owe back (everything except their share).
            _ledger(db, r.id, order, LedgerKind.COD_CASH_COLLECTED, -order.total, now,
                    f"{code}: COD cash collected GH₵{order.total}")
            if e.rider_net > 0:
                _ledger(db, r.id, order, LedgerKind.DELIVERY_PAY, e.rider_net, hold, f"{code}: delivery share ({rider_keep}%)")
        else:
            _ledger(db, r.id, order, LedgerKind.DELIVERY_PAY, order.delivery_fee, hold, f"{code}: delivery fee")
            _ledger(db, r.id, order, LedgerKind.DELIVERY_COMMISSION, -e.rider_commission, hold,
                    f"{code}: platform deduction ({100 - rider_keep}%)", {"rider_rate": str(100 - rider_keep), "order": code})

    # --- platform: its cut, funded from the Paystack balance (online) or from the rider's remittance (COD)
    p = platform_account(db)
    if e.vendor_commission > 0:
        _ledger(db, p.id, order, LedgerKind.ORDER_COMMISSION, e.vendor_commission, hold, f"{code}: vendor commission")
    if e.rider_commission > 0:
        _ledger(db, p.id, order, LedgerKind.DELIVERY_COMMISSION, e.rider_commission, hold, f"{code}: rider deduction")
    if e.platform_discount > 0:
        _ledger(db, p.id, order, LedgerKind.PLATFORM_DISCOUNT, -e.platform_discount, hold, f"{code}: platform-funded coupon")

    order.earnings_posted_at = now
    _notify_earnings(db, order, delivery, e)
    return True


def _notify_earnings(db: Session, order: Order, delivery, e: Economics) -> None:
    from app.services import notification_service as ns
    vendor_owner = getattr(order.vendor, "owner_user_id", None)
    if vendor_owner:
        if getattr(order, "payment_mode", "ONLINE") == "COD" and order.fulfillment_type == FulfillmentType.PICKUP:
            owed = q(order.total - e.vendor_net)
            ns.notify(db, vendor_owner, "wallet", "COD pickup collected",
                      f"Order {order.code}: the customer paid {cedis(order.total)} cash at pickup. You owe KORA EATS "
                      f"{cedis(owed)} commission — it is deducted from your wallet earnings automatically.",
                      "/vendor/dashboard.html#wallet")
        else:
            ns.notify(db, vendor_owner, "wallet", "You earned money",
                      f"Order {order.code}: you earned {cedis(e.vendor_net)} (after commission). It becomes withdrawable after the holding period.",
                      "/vendor/dashboard.html#wallet")
    if delivery is not None:
        rider_user = db.scalar(select(DeliveryRider.user_id).where(DeliveryRider.id == delivery.rider_id))
        if rider_user:
            ns.notify(db, rider_user, "wallet", "You earned money",
                      f"Delivery {order.code}: you earned {cedis(e.rider_net)}. It becomes withdrawable after the holding period.",
                      "/rider/dashboard.html#wallet")


def post_return_earnings(db: Session, order: Order) -> tuple[Decimal, Decimal]:
    """Food came back undelivered: KORA EATS pays the vendor (food, less commission and any discount the vendor funded)
    and the rider (their share of the delivery fee) anyway. Returns (vendor_payout, rider_payout). Idempotent.

    For a COD order the customer never paid, so the platform fronts the money and records the whole payout as
    COD_REFUSED_LOSS. For a prepaid order the customer's money is already in the platform's Paystack balance; the
    food part goes back to the customer as a refund (see return_service) and the delivery fee stays with the platform,
    so nothing extra is lost there."""
    if order.earnings_posted_at is not None:
        return Decimal("0"), Decimal("0")
    now = utcnow()
    hold = now + timedelta(hours=get_settings().payout_hold_hours)
    vendor_pct, rider_keep = rates()
    vendor = vendor_account(db, order.vendor_id)
    vendor_payout = q(order.subtotal - (order.discount if getattr(order, "coupon_funded_by", None) == "VENDOR" else Decimal("0"))
                      - q((order.subtotal - (order.discount if getattr(order, "coupon_funded_by", None) == "VENDOR" else Decimal("0")))
                          * Decimal(str(vendor_pct)) / Decimal("100")))
    _ledger(db, vendor.id, order, LedgerKind.ORDER_SALE, vendor_payout, hold,
            f"{order.code}: food paid to you (order returned undelivered)", {"vendor_rate": str(vendor_pct), "order": order.code, "returned": True})
    rider_payout = Decimal("0")
    delivery = db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    if delivery is not None and order.delivery_fee and order.delivery_fee > 0:
        rider_payout = q(order.delivery_fee * Decimal(rider_keep) / Decimal("100"))
        r = rider_account(db, delivery.rider_id)
        _ledger(db, r.id, order, LedgerKind.DELIVERY_PAY, rider_payout, hold,
                f"{order.code}: delivery share ({rider_keep}%) — paid by KORA EATS (order not delivered)", {"rider_rate": str(100 - rider_keep), "order": order.code})
    if getattr(order, "payment_mode", "ONLINE") == "COD":
        p = platform_account(db)
        _ledger(db, p.id, order, LedgerKind.COD_REFUSED_LOSS, -(vendor_payout + rider_payout), now,
                f"{order.code}: paid vendor and rider; customer did not pay (COD)", {"order": order.code})
    order.earnings_posted_at = now
    db.flush()
    return vendor_payout, rider_payout


def reverse_order(db: Session, order: Order) -> bool:
    """A refunded/completed-then-undone order: negate everything posted for it, in one entry per account."""
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.order_id == order.id,
                                                 LedgerEntry.kind.notin_([LedgerKind.WITHDRAWAL, LedgerKind.WITHDRAWAL_FEE,
                                                                          LedgerKind.WITHDRAWAL_REVERSAL, LedgerKind.ADJUSTMENT]))).all()
    by_account: dict[int, Decimal] = {}
    for x in entries:
        by_account[x.account_id] = by_account.get(x.account_id, Decimal("0")) + x.amount
    outstanding = {a: t for a, t in by_account.items() if t != 0}
    if not outstanding:
        return False                                            # already fully reversed (or nothing was ever posted)
    now = utcnow()
    for account_id, total in outstanding.items():
        _ledger(db, account_id, order, LedgerKind.REFUND_REVERSAL, -total, now,
                f"{order.code}: earnings reversed (refund)")
    return True


from app.core.money import cedis  # noqa: E402
from app.models.orders import FulfillmentType, Order  # noqa: E402
from app.models.riders import Delivery, DeliveryRider  # noqa: E402
from app.services import order_events  # noqa: E402


@order_events.subscribe
def _wallet_on_order_event(db, order, old, new, actor_role, background):
    from app.models.orders import OrderStatus as _S
    if new in (_S.DELIVERED, _S.COLLECTED) and old not in (_S.DELIVERED, _S.COLLECTED):
        post_order(db, order)
    elif new == _S.REFUNDED and old != _S.REFUNDED:
        reverse_order(db, order)
