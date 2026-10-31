"""Admin: the platform's revenue, what is owed to vendors and riders, and payout controls."""
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.db.session import get_db
from app.models.catalog import Vendor
from app.models.orders import Order, OrderStatus as S
from app.models.riders import DeliveryRider
from app.models.user import User, UserRole
from app.models.wallet import LedgerEntry, LedgerKind as K, WalletAccount, WalletOwner, Withdrawal, WithdrawalStatus as W
from app.services import audit_service, payout_service as ps, wallet_service as ws

router = APIRouter(prefix="/admin/wallets", tags=["admin"], dependencies=[Depends(require_roles(UserRole.ADMIN))])
Admin = Depends(require_roles(UserRole.ADMIN))


def _sum(db: Session, *where) -> Decimal:
    return Decimal(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(*where)))


@router.get("/overview")
def overview(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Where the money is. 'Platform' = your revenue; 'owed' = what vendors and riders have earned but not withdrawn."""
    now, since = utcnow(), utcnow() - __import__("datetime").timedelta(days=days)
    p = ws.platform_account(db)
    by_kind = {k.value: float(v) for k, v in db.execute(select(LedgerEntry.kind, func.sum(LedgerEntry.amount)).where(LedgerEntry.account_id == p.id, LedgerEntry.created_at >= since)
                                                         .group_by(LedgerEntry.kind))}
    revenue = {"vendor_commission": by_kind.get("ORDER_COMMISSION", 0.0), "rider_deduction": by_kind.get("DELIVERY_COMMISSION", 0.0),
               "withdrawal_fees": by_kind.get("WITHDRAWAL_FEE", 0.0), "coupons_funded_by_platform": by_kind.get("PLATFORM_DISCOUNT", 0.0),
               "cash_refusal_losses": by_kind.get("COD_REFUSED_LOSS", 0.0), "refund_clawbacks": by_kind.get("REFUND_REVERSAL", 0.0), "adjustments": by_kind.get("ADJUSTMENT", 0.0)}
    revenue["net_revenue"] = round(sum(revenue.values()), 2)
    owed = {}
    for owner in (WalletOwner.VENDOR, WalletOwner.RIDER):
        ids = [a for (a,) in db.execute(select(WalletAccount.id).where(WalletAccount.owner_type == owner))]
        bal = [ws.balances(db, i, now) for i in ids]
        owed[owner.value.lower() + "s"] = {"accounts": len(ids), "available": float(sum((b["available"] for b in bal if b["available"] > 0), Decimal(0))),
                                           "pending": float(sum((b["pending"] for b in bal if b["pending"] > 0), Decimal(0))),
                                           "owed_to_platform": float(sum((-b["total"] for b in bal if b["total"] < 0), Decimal(0)))}
    wd = {s.value: {"count": c, "amount": float(a)} for s, c, a in db.execute(select(Withdrawal.status, func.count(), func.coalesce(func.sum(Withdrawal.amount), 0)).group_by(Withdrawal.status))}
    cash = db.execute(select(WalletAccount.id, WalletAccount.rider_id).where(WalletAccount.owner_type == WalletOwner.RIDER)).all()
    holders = []
    for aid, rid in cash:
        c = ws.balances(db, aid, now)["cash_in_hand"]
        if c > 0:
            holders.append({"rider_id": rid, "name": db.get(DeliveryRider, rid).user.profile.full_name, "cash_in_hand": float(c)})
    in_progress = Decimal(db.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(Order.payment_mode == "ONLINE", Order.earnings_posted_at.is_(None),
                                                                                           Order.status.in_([S.PAID, S.CONFIRMED, S.PREPARING, S.READY_FOR_PICKUP, S.RIDER_ASSIGNED, S.PICKED_UP, S.OUT_FOR_DELIVERY]))))
    ledger_total = _sum(db)
    recent = db.execute(select(Order.id, Order.code, func.sum(LedgerEntry.amount)).join(LedgerEntry, LedgerEntry.order_id == Order.id).where(LedgerEntry.account_id == p.id)
                        .group_by(Order.id, Order.code).order_by(Order.id.desc()).limit(20)).all()
    return {"days": days, "revenue": revenue, "owed": owed, "withdrawals": wd, "cash_with_riders": {"total": float(sum(h["cash_in_hand"] for h in holders)), "riders": holders},
            "reconciliation": {"ledger_balance": float(ledger_total), "paid_orders_in_progress": float(in_progress), "expected_paystack_balance": float(ledger_total + in_progress),
                               "note": "Roughly what your Paystack balance should be: completed earnings still held in the system, plus paid orders not yet completed. Refunds paid out of Paystack, and Paystack's own fees, are not included."},
            "recent_orders": [{"order_id": i, "code": c, "platform_net": float(n)} for i, c, n in recent]}


@router.get("/withdrawals")
def withdrawals(status: str | None = None, db: Session = Depends(get_db)):
    q_ = select(Withdrawal).order_by(Withdrawal.id.desc()).limit(100)
    if status:
        q_ = q_.where(Withdrawal.status == W(status)) if status in W.__members__ else q_
    out = []
    for w in db.scalars(q_):
        acct = db.get(WalletAccount, w.account_id)
        owner, _, _ = ps.owner_of(db, acct)
        name = owner.name if acct.owner_type == WalletOwner.VENDOR else owner.user.profile.full_name
        pa = db.get(__import__("app.models.wallet", fromlist=["PayoutAccount"]).PayoutAccount, w.payout_account_id)
        out.append({**ps.withdrawal_out(w), "owner": name, "owner_type": acct.owner_type.value, "sends_to": f"{pa.provider_name or pa.provider_code} {ps.mask(pa.account_number)} ({pa.account_name})",
                    "balance_after": float(ws.balances(db, acct.id)["total"])})
    return out


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/withdrawals/{withdrawal_id}/approve")
def approve(withdrawal_id: int, background: BackgroundTasks, admin: User = Admin, db: Session = Depends(get_db)):
    w = db.get(Withdrawal, withdrawal_id)
    if w is None:
        raise api_error(404, "not_found", "Withdrawal not found.")
    w = ps.approve(db, w, admin.id, background)
    db.commit()
    return ps.withdrawal_out(w)


@router.post("/withdrawals/{withdrawal_id}/reject")
def reject(withdrawal_id: int, data: RejectIn, background: BackgroundTasks, admin: User = Admin, db: Session = Depends(get_db)):
    w = db.get(Withdrawal, withdrawal_id)
    if w is None:
        raise api_error(404, "not_found", "Withdrawal not found.")
    w = ps.reject(db, w, admin.id, data.reason, background)
    db.commit()
    return ps.withdrawal_out(w)


class AdjustIn(BaseModel):
    amount: Decimal = Field(decimal_places=2, ge=-100000, le=100000)
    reason: str = Field(min_length=5, max_length=200)


@router.post("/accounts/{account_id}/adjust")
def adjust(account_id: int, data: AdjustIn, admin: User = Admin, db: Session = Depends(get_db)):
    """A correction. Always needs a reason and is recorded in the audit log. (The only way to change a balance by hand.)"""
    a = db.get(WalletAccount, account_id)
    if a is None or a.owner_type == WalletOwner.PLATFORM or data.amount == 0:
        raise api_error(404, "not_found", "Wallet not found, or the amount is zero.")
    db.add(LedgerEntry(account_id=a.id, kind=K.ADJUSTMENT, amount=ws.q(data.amount), available_at=utcnow(), note=f"Admin adjustment: {data.reason}"[:200], meta={"admin": str(admin.id)}))
    audit_service.record(db, "wallet_adjusted", admin.id, detail=f"wallet={a.id} {data.amount} {data.reason[:100]}")
    db.commit()
    return {"account_id": a.id, "balance": {k: float(v) for k, v in ws.balances(db, a.id).items()}}
