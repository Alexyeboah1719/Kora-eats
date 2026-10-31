"""The wallet engine: who earned what from each order, written to an append-only ledger.

THE RULES (all rates are admin-editable settings):
  Vendor   earns   food sales  -  commission (default 10% of the sales)  -  any discount the VENDOR agreed to fund.
  Rider    earns   the delivery fee  -  the platform's deduction (default 20%).
  Platform keeps   the two commissions  -  the cost of platform-funded coupons.
  For every order:  vendor net + rider net + platform net  =  exactly what the customer paid.  (tested)

Earnings are written when an order is COMPLETED (delivered / collected) and become withdrawable after a hold (default 24 hours) so that disputes and
refunds can still be handled. A refund after completion takes the earnings back. Past rows never change: the rates used are saved with each row."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.orders import Order, OrderStatus as S
from app.models.riders import Delivery, DeliveryRider
from app.models.wallet import LedgerEntry, LedgerKind as K, WalletAccount, WalletOwner
from app.services import order_events
from app.services.order_service import COMPLETED

logger = logging.getLogger(__name__)
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# Entries that are part of an order's earnings (as opposed to cash handling, withdrawals and corrections).
EARNING_KINDS = (K.ORDER_SALE, K.DISCOUNT_FUNDING, K.ORDER_COMMISSION, K.DELIVERY_PAY, K.DELIVERY_COMMISSION, K.REFUND_REVERSAL)


def q(x) -> Decimal:
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)


@dataclass
class Economics:
    """What an order is worth to each party. Pure arithmetic: no database."""
    subtotal: Decimal
    delivery_fee: Decimal
    discount: Decimal
    customer_paid: Decimal
    funded_by: str | None
    vendor_commission_rate: Decimal
    vendor_base: Decimal                 # the sales the commission is charged on (after any discount the vendor funded)
    vendor_commission: Decimal
    vendor_discount_cost: Decimal        # discount the vendor funds (food + delivery)
    vendor_net: Decimal
    rider_gross: Decimal
    rider_commission_rate: Decimal
    rider_commission: Decimal
    rider_net: Decimal
    platform_discount_cost: Decimal
    platform_net: Decimal
    notes: list[str] = field(default_factory=list)


def rates() -> tuple[Decimal, Decimal]:
    """(vendor commission %, rider keeps %)"""
    s = get_settings()
    return Decimal(str(s.vendor_commission_percent)), Decimal(str(s.rider_share_percent))


def economics(order: Order, *, rider_involved: bool = True, customer_paid: Decimal | None = None,
              vendor_pct: Decimal | None = None, rider_keep_pct: Decimal | None = None) -> Economics:
    vp, rk = rates()
    vendor_pct = vp if vendor_pct is None else vendor_pct
    rider_keep_pct = rk if rider_keep_pct is None else rider_keep_pct
    S_, D = Decimal(order.subtotal), Decimal(order.discount or 0)
    F = Decimal(order.delivery_fee or 0)
    dd = Decimal(order.discount_delivery_part or 0)
    paid = Decimal(order.total) if customer_paid is None else Decimal(customer_paid)
    funded = order.coupon_funded_by or ("PLATFORM" if D > 0 else None)     # an old order with an unknown funder never takes a vendor's money
    v_food_disc = (D - dd) if funded == "VENDOR" else ZERO
    v_deliv_sub = dd if funded == "VENDOR" else ZERO
    platform_disc = D - v_food_disc - v_deliv_sub
    base = S_ - v_food_disc
    v_comm = q(base * vendor_pct / 100) if base > 0 else ZERO
    v_net = base - v_deliv_sub - v_comm
    r_gross = F if rider_involved else ZERO
    r_comm = q(r_gross * (100 - rider_keep_pct) / 100)
    r_net = r_gross - r_comm
    notes = []
    if v_food_disc + v_deliv_sub > 0:
        notes.append(f"You funded {cedis(v_food_disc + v_deliv_sub)} of the customer's discount ({order.coupon_code}).")
    if platform_disc > 0:
        notes.append(f"KORA EATS funded {cedis(platform_disc)} of the customer's discount ({order.coupon_code}): it does not reduce your earnings.")
    return Economics(subtotal=q(S_), delivery_fee=q(F), discount=q(D), customer_paid=q(paid), funded_by=funded, vendor_commission_rate=vendor_pct, vendor_base=q(base),
                     vendor_commission=v_comm, vendor_discount_cost=q(v_food_disc + v_deliv_sub), vendor_net=q(v_net), rider_gross=q(r_gross),
                     rider_commission_rate=Decimal(100) - rider_keep_pct, rider_commission=r_comm, rider_net=q(r_net), platform_discount_cost=q(platform_disc),
                     platform_net=q(paid - v_net - r_net), notes=notes)


# ------------------------------------------------------------------ accounts
def _get_or_create(db: Session, **where) -> WalletAccount:
    acct = db.scalar(select(WalletAccount).filter_by(**where))
    if acct is None:
        try:
            with db.begin_nested():
                acct = WalletAccount(**where)
                db.add(acct)
                db.flush()
        except IntegrityError:
            acct = db.scalar(select(WalletAccount).filter_by(**where))
    return acct


def vendor_account(db: Session, vendor_id: int) -> WalletAccount:
    return _get_or_create(db, owner_type=WalletOwner.VENDOR, vendor_id=vendor_id)


def rider_account(db: Session, rider_id: int) -> WalletAccount:
    return _get_or_create(db, owner_type=WalletOwner.RIDER, rider_id=rider_id)


def platform_account(db: Session) -> WalletAccount:
    acct = db.scalar(select(WalletAccount).where(WalletAccount.owner_type == WalletOwner.PLATFORM))
    if acct is None:
        try:
            with db.begin_nested():
                acct = WalletAccount(owner_type=WalletOwner.PLATFORM)
                db.add(acct)
                db.flush()
        except IntegrityError:
            acct = db.scalar(select(WalletAccount).where(WalletAccount.owner_type == WalletOwner.PLATFORM))
    return acct


# ------------------------------------------------------------------ posting
def _add(db: Session, acct: WalletAccount, order: Order, kind: K, amount: Decimal, available_at: datetime, note: str, meta: dict | None = None) -> None:
    if amount == 0:
        return
    db.add(LedgerEntry(account_id=acct.id, order_id=order.id, kind=kind, amount=q(amount), available_at=available_at, note=note[:200], meta=meta or {}))


def post_order(db: Session, order: Order, refused: bool = False) -> bool:
    """Write a completed order's earnings to the ledger. Safe to call twice (it only ever posts once)."""
    if order.earnings_posted_at is not None:
        return False
    now = utcnow()
    delivery = db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    rider = db.get(DeliveryRider, delivery.rider_id) if delivery else None
    cod = order.payment_mode == "COD"
    paid = ZERO if refused else Decimal(order.total)
    e = economics(order, rider_involved=rider is not None, customer_paid=paid)
    when = (order.delivered_at or now) + timedelta(hours=get_settings().payout_hold_hours)     # earnings are held until then
    meta = {"order": order.code, "vendor_rate": str(e.vendor_commission_rate), "rider_deduction_rate": str(e.rider_commission_rate), "funded_by": e.funded_by,
            "payment_mode": order.payment_mode}
    try:
        with db.begin_nested():
            v = vendor_account(db, order.vendor_id)
            _add(db, v, order, K.ORDER_SALE, e.subtotal, when, "Food sales", meta)
            _add(db, v, order, K.DISCOUNT_FUNDING, -e.vendor_discount_cost, when, "Discount you funded", meta)
            _add(db, v, order, K.ORDER_COMMISSION, -e.vendor_commission, when, f"KORA EATS commission ({e.vendor_commission_rate}%)", meta)
            if rider is not None:
                r = rider_account(db, rider.id)
                _add(db, r, order, K.DELIVERY_PAY, e.rider_gross, when, "Delivery fee", meta)
                _add(db, r, order, K.DELIVERY_COMMISSION, -e.rider_commission, when, f"KORA EATS deduction ({e.rider_commission_rate}%)", meta)
                if cod and order.cod_collected_at and not refused:
                    _add(db, r, order, K.COD_CASH_COLLECTED, -Decimal(order.total), now, "Cash collected from the customer (you owe this to KORA EATS)", meta)
                
                # Auto-clear COD debt with earnings from this order
                if not cod:  # This is a non-COD order with positive earnings
                    # Check rider's current COD cash balance
                    cash_balance = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0))
                                          .where(LedgerEntry.account_id == r.id,
                                                LedgerEntry.kind == K.COD_CASH_COLLECTED))
                    if cash_balance < 0:  # Rider has negative COD debt
                        # Apply rider's net earnings from this order to clear debt
                        net_earning = e.rider_net
                        debt_to_clear = min(-cash_balance, net_earning)
                        if debt_to_clear > ZERO:
                            _add(db, r, order, K.COD_REMITTANCE, debt_to_clear, now, 
                                  f"Auto-applied earnings to clear COD debt ({cedis(debt_to_clear)})", meta)
                            meta["auto_cod_clearance"] = str(debt_to_clear)
            p = platform_account(db)
            if refused:
                _add(db, p, order, K.COD_REFUSED_LOSS, -(e.vendor_net + e.rider_net), now, "Customer refused to pay on delivery; vendor and rider were still paid", meta)
            else:
                _add(db, p, order, K.ORDER_COMMISSION, e.vendor_commission, now, "Vendor commission", meta)
                _add(db, p, order, K.DELIVERY_COMMISSION, e.rider_commission, now, "Rider deduction", meta)
                _add(db, p, order, K.PLATFORM_DISCOUNT, -e.platform_discount_cost, now, "Coupon funded by the platform", meta)
                db.flush()                                       # this session does not auto-flush: write the rows first, or the check below misses them
                booked = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.order_id == order.id))
                expected = e.customer_paid - (Decimal(order.total) if (cod and order.cod_collected_at) else ZERO)   # cash stays with the rider
                if q(Decimal(booked)) != q(expected):                                    # rounding or an unusual discount: keep the books balanced
                    _add(db, p, order, K.ADJUSTMENT, expected - Decimal(booked), now, "Rounding", meta)
            order.earnings_posted_at = now
            db.flush()
    except IntegrityError:
        logger.info("order %s earnings were already posted", order.code)
        return False
    _notify_earnings(db, order, e, rider, when)
    return True


def reverse_order(db: Session, order: Order) -> bool:
    """A refund after completion: take the earnings back (one reversing row per wallet). Cash a rider holds stays his to hand over."""
    if order.earnings_posted_at is None:
        return False
    now = utcnow()
    rows = db.execute(select(LedgerEntry.account_id, func.sum(LedgerEntry.amount)).where(LedgerEntry.order_id == order.id, LedgerEntry.kind.in_(
        [K.ORDER_SALE, K.DISCOUNT_FUNDING, K.ORDER_COMMISSION, K.DELIVERY_PAY, K.DELIVERY_COMMISSION, K.PLATFORM_DISCOUNT, K.COD_REFUSED_LOSS, K.ADJUSTMENT])).group_by(LedgerEntry.account_id)).all()
    try:
        with db.begin_nested():
            for account_id, total in rows:
                if q(Decimal(total)) != 0:
                    db.add(LedgerEntry(account_id=account_id, order_id=order.id, kind=K.REFUND_REVERSAL, amount=q(-Decimal(total)), available_at=now,
                                       note="Order refunded: earnings taken back", meta={"order": order.code}))
            db.flush()
    except IntegrityError:
        return False
    return True


@order_events.subscribe
def on_order_event(db, order, old, new, actor_role, background):
    if new in COMPLETED and old not in COMPLETED:
        post_order(db, order)
    elif new == S.CANCELLED and order.cod_failed_at is not None and order.payment_mode == "COD":
        post_order(db, order, refused=True)
    elif new == S.REFUNDED:
        reverse_order(db, order)


def _notify_earnings(db: Session, order: Order, e: Economics, rider, when: datetime) -> None:
    from app.services import notification_service as ns
    try:
        if order.vendor.owner_user_id:
            ns.notify(db, order.vendor.owner_user_id, "earnings", "You earned money", f"{cedis(e.vendor_net)} from order {order.code} (after {e.vendor_commission_rate}% commission). Available {when:%d %b}.",
                      "/vendor/dashboard.html#wallet")
        if rider is not None:
            msg = f"{cedis(e.rider_net)} from order {order.code}. Available {when:%d %b}."
            # Check if earnings were auto-applied to clear COD debt
            auto_clear = order.metadata.get("auto_cod_clearance") if order.metadata else None
            if auto_clear:
                msg = f"{cedis(e.rider_net)} from order {order.code}. {cedis(auto_clear)} auto-applied to clear COD debt. Available {when:%d %b}."
            ns.notify(db, rider.user_id, "earnings", "You earned money", msg, "/rider/dashboard.html#wallet")
    except Exception:                                    # a notification problem must never block an order from completing
        logger.exception("could not send earnings notification for %s", order.code)


# ------------------------------------------------------------------ balances and statements
def balances(db: Session, account_id: int, now: datetime | None = None) -> dict:
    now = now or utcnow()
    avail, pend = db.execute(select(
        func.coalesce(func.sum(case((LedgerEntry.available_at <= now, LedgerEntry.amount), else_=0)), 0),
        func.coalesce(func.sum(case((LedgerEntry.available_at > now, LedgerEntry.amount), else_=0)), 0)).where(LedgerEntry.account_id == account_id)).one()

    def kind_sum(*kinds):
        return Decimal(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.account_id == account_id, LedgerEntry.kind.in_(kinds))))
    commission = -kind_sum(K.ORDER_COMMISSION, K.DELIVERY_COMMISSION)
    earned = kind_sum(K.ORDER_SALE, K.DISCOUNT_FUNDING, K.ORDER_COMMISSION, K.DELIVERY_PAY, K.DELIVERY_COMMISSION, K.REFUND_REVERSAL)
    withdrawn = -kind_sum(K.WITHDRAWAL, K.WITHDRAWAL_REVERSAL)
    cash = -(kind_sum(K.COD_CASH_COLLECTED, K.COD_REMITTANCE))
    avail, pend = q(Decimal(avail)), q(Decimal(pend))
    return {"available": avail, "pending": pend, "total": q(avail + pend), "withdrawable": max(ZERO, avail), "lifetime_earnings": q(earned), "commission_paid": q(commission),
            "withdrawn": q(withdrawn), "cash_in_hand": q(max(ZERO, cash))}


def rider_balances(db: Session, rider_id: int, now: datetime | None = None) -> dict:
    """Rider-specific balance calculation showing actual delivery earnings."""
    now = now or utcnow()
    
    # Ensure rider has a wallet account
    rider = db.get(DeliveryRider, rider_id)
    if rider is None:
        return {
            "available": ZERO,
            "pending": ZERO,
            "total": ZERO,
            "withdrawable": ZERO,
            "lifetime_earnings": ZERO,
            "commission_paid": ZERO,
            "withdrawn": ZERO,
            "cash_in_hand": ZERO
        }
    
    # Get or create rider's wallet account
    rider_acct = rider_account(db, rider_id)
    
    avail, pend = db.execute(select(
        func.coalesce(func.sum(case((LedgerEntry.available_at <= now, LedgerEntry.amount), else_=0)), 0),
        func.coalesce(func.sum(case((LedgerEntry.available_at > now, LedgerEntry.amount), else_=0)), 0)).where(LedgerEntry.account_id == rider_acct.id)).one()

    def kind_sum(*kinds):
        return Decimal(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.account_id == rider_acct.id, LedgerEntry.kind.in_(kinds))))
    
    # Rider earnings come from DELIVERY_PAY
    delivery_earnings = kind_sum(K.DELIVERY_PAY)
    # Deductions from DELIVERY_COMMISSION
    commission = -kind_sum(K.DELIVERY_COMMISSION)
    # Net earnings = delivery pay - commission
    net_earnings = delivery_earnings + commission
    # Cash collected from COD orders
    cash = -(kind_sum(K.COD_CASH_COLLECTED, K.COD_REMITTANCE))
    # Withdrawals
    withdrawn = -kind_sum(K.WITHDRAWAL, K.WITHDRAWAL_REVERSAL)
    
    avail, pend = q(Decimal(avail)), q(Decimal(pend))
    return {
        "available": avail,
        "pending": pend,
        "total": q(avail + pend),
        "withdrawable": max(ZERO, avail),
        "lifetime_earnings": q(net_earnings),
        "commission_paid": q(commission),
        "withdrawn": q(withdrawn),
        "cash_in_hand": q(max(ZERO, cash))
    }


def statement(db: Session, account: WalletAccount, limit: int = 40, offset: int = 0, now: datetime | None = None) -> list[dict]:
    """One row per order: the sale, what was deducted and why, what you keep, and when it is yours to withdraw."""
    now = now or utcnow()
    ids = [i for (i,) in db.execute(select(LedgerEntry.order_id).where(LedgerEntry.account_id == account.id, LedgerEntry.order_id.is_not(None))
                                    .group_by(LedgerEntry.order_id).order_by(LedgerEntry.order_id.desc()).limit(limit).offset(offset))]
    if not ids:
        return []
    orders = {o.id: o for o in db.scalars(select(Order).where(Order.id.in_(ids))).unique()}
    by_order: dict[int, list[LedgerEntry]] = {i: [] for i in ids}
    for en in db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == account.id, LedgerEntry.order_id.in_(ids)).order_by(LedgerEntry.id)):
        by_order[en.order_id].append(en)
    out = []
    for oid in ids:
        o, entries = orders.get(oid), by_order[oid]
        parts = [{"label": en.note or en.kind.value, "kind": en.kind.value, "amount": float(en.amount)} for en in entries if en.kind != K.COD_CASH_COLLECTED]
        net = sum((en.amount for en in entries if en.kind in EARNING_KINDS), ZERO)
        reversed_ = any(en.kind == K.REFUND_REVERSAL for en in entries)
        held_until = max((en.available_at for en in entries if en.available_at > now), default=None)
        meta = entries[0].meta if entries else {}
        warnings = []
        if reversed_:
            warnings.append("Refunded: the earnings from this order were taken back.")
        if any(en.kind == K.COD_REFUSED_LOSS for en in entries):
            warnings.append("The customer refused to pay at the door.")
        if o is not None and o.cod_failed_at:
            warnings.append("The customer refused to pay on delivery. You were still paid; KORA EATS covers it.")
        if any(en.kind == K.COD_CASH_COLLECTED for en in entries):
            warnings.append(f"You collected {cedis(o.total)} in cash. Hand it over to KORA EATS (see Cash to hand over).")
        if held_until and not reversed_:
            warnings.append(f"Held until {held_until:%d %b %H:%M} in case of a dispute or refund.")
        if net < 0:
            warnings.append("This order costs you money: a discount you funded is larger than the sale.")
        out.append({"order_id": oid, "code": o.code if o else meta.get("order"), "date": (o.delivered_at or o.created_at).isoformat() if o else None,
                    "order_status": o.status.value if o else None, "payment_mode": o.payment_mode if o else None, "parts": parts, "you_earn": float(net),
                    "state": "REFUNDED" if reversed_ else ("HELD" if held_until else "AVAILABLE"), "available_at": held_until.isoformat() if held_until else None,
                    "rates": {"vendor_commission": meta.get("vendor_rate"), "rider_deduction": meta.get("rider_deduction_rate")}, "warnings": warnings})
    return out


def upcoming(db: Session, vendor_id: int | None = None, rider_id: int | None = None) -> list[dict]:
    """Orders still in progress, with what the owner will earn if they complete as planned (an estimate, at today's rates)."""
    active = [S.PLACED, S.PAID, S.CONFIRMED, S.PREPARING, S.READY_FOR_PICKUP, S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY]
    if vendor_id is not None:
        q_ = select(Order).where(Order.vendor_id == vendor_id, Order.status.in_(active))
    else:
        q_ = select(Order).join(Delivery, Delivery.order_id == Order.id).where(Delivery.rider_id == rider_id, Order.status.in_(active))
    out = []
    for o in db.scalars(q_.order_by(Order.id.desc()).limit(30)).unique():
        e = economics(o, rider_involved=o.fulfillment_type.value == "DELIVERY")
        out.append({"order_id": o.id, "code": o.code, "status": o.status.value, "payment_mode": o.payment_mode,
                    "you_will_earn": float(e.vendor_net if vendor_id is not None else e.rider_net), "notes": e.notes if vendor_id is not None else [],
                    "warnings": ["Pay on delivery: the customer pays the rider in cash."] if o.payment_mode == "COD" and vendor_id is not None else
                                (["Pay on delivery: collect " + cedis(o.total) + " in cash from the customer."] if o.payment_mode == "COD" else [])})
    return out
