"""Wallet and payouts for vendors and riders (same screens, two owners)."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.payments import KEY_RE
from app.api.routers.rider import require_rider
from app.api.routers.vendor import require_vendor
from app.core.errors import api_error
from app.db.session import get_db
from app.models.catalog import Vendor
from app.models.riders import DeliveryRider
from app.models.wallet import WalletAccount, Withdrawal
from app.services import payout_service as ps, wallet_service as ws
from app.services.payment_providers import MOMO_PROVIDERS, ProviderError, get_provider


class PayoutAccountIn(BaseModel):
    type: Literal["MOBILE_MONEY", "BANK"]
    provider_code: str = Field(min_length=1, max_length=20)          # MTN / VOD / ATL, or a bank code
    account_number: str = Field(min_length=6, max_length=32)
    account_name: str = Field(min_length=2, max_length=100)


class WithdrawIn(BaseModel):
    amount: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)


def vendor_wallet(v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)) -> WalletAccount:
    return ws.vendor_account(db, v.id)


def rider_wallet(r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)) -> WalletAccount:
    return ws.rider_account(db, r.id)


def build(prefix: str, tag: str, account_dep, owner: str) -> APIRouter:
    r = APIRouter(prefix=prefix, tags=[tag])

    @r.get("/wallet")
    def summary(a: WalletAccount = Depends(account_dep), db: Session = Depends(get_db)):
        """Balance, what the platform deducts, payout account, and exactly what stands in the way of a withdrawal (if anything)."""
        vp, keep = ws.rates()
        b = ws.balances(db, a.id)
        rates = {"commission_percent": float(vp)} if owner == "vendor" else {"deduction_percent": float(100 - keep)}
        recent = db.scalars(select(Withdrawal).where(Withdrawal.account_id == a.id).order_by(Withdrawal.id.desc()).limit(5)).all()
        return {"owner": owner, "balance": {k: float(v) for k, v in b.items()}, "rates": rates, "payout_account": ps.payout_out(ps.active_payout(db, a.id)),
                "eligibility": ps.eligibility(db, a), "recent_withdrawals": [ps.withdrawal_out(w) for w in recent],
                "explain": ("You keep your food sales minus a {:.0f}% KORA EATS commission and any discount you chose to fund.".format(vp) if owner == "vendor" else
                            "You keep the delivery fee minus a {:.0f}% KORA EATS deduction.".format(100 - keep))}

    @r.get("/wallet/statement")
    def statement(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), a: WalletAccount = Depends(account_dep), db: Session = Depends(get_db)):
        return {"orders": ws.statement(db, a, limit, offset)}

    @r.get("/wallet/upcoming")
    def upcoming(a: WalletAccount = Depends(account_dep), db: Session = Depends(get_db)):
        return {"orders": ws.upcoming(db, vendor_id=a.vendor_id, rider_id=a.rider_id)}

    @r.get("/wallet/withdrawals")
    def withdrawals(a: WalletAccount = Depends(account_dep), db: Session = Depends(get_db)):
        return [ps.withdrawal_out(w) for w in db.scalars(select(Withdrawal).where(Withdrawal.account_id == a.id).order_by(Withdrawal.id.desc()).limit(50))]

    @r.get("/wallet/banks")
    def banks(a: WalletAccount = Depends(account_dep)):
        try:
            banks = get_provider().list_banks()
        except ProviderError:
            banks = []
        return {"mobile_money": [{"code": k, "name": v} for k, v in MOMO_PROVIDERS.items()], "banks": banks}

    @r.put("/wallet/payout-account")
    def payout_account(data: PayoutAccountIn, a: WalletAccount = Depends(account_dep), db: Session = Depends(get_db)):
        """Where your money is sent. Changing it pauses withdrawals for a short safety period."""
        p = ps.set_payout_account(db, a, kind=data.type, provider_code=data.provider_code, account_number=data.account_number, account_name=data.account_name)
        db.commit()
        return ps.payout_out(p)

    @r.post("/wallet/withdrawals", status_code=201)
    def withdraw(data: WithdrawIn, background: BackgroundTasks, a: WalletAccount = Depends(account_dep), db: Session = Depends(get_db),
                 idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        if not idempotency_key or not KEY_RE.match(idempotency_key):
            raise api_error(400, "idempotency_key_required", "Send an Idempotency-Key header (8-64 letters, digits, - or _).")
        w = ps.request_withdrawal(db, a, data.amount, idempotency_key, background)
        db.commit()
        return ps.withdrawal_out(w)

    return r


vendor_router = build("/vendor", "vendor-wallet", vendor_wallet, "vendor")
rider_router = build("/rider", "rider-wallet", rider_wallet, "rider")
