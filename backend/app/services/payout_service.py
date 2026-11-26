"""Getting money OUT: payout accounts, withdrawals, and Paystack Transfers.

SAFETY RULES (each is tested):
  * Money leaves the wallet in the SAME database transaction that records the withdrawal, under a row lock, so two taps can never spend it twice.
  * If Paystack definitely refuses, the money goes straight back. If we simply don't know (network failure), we DO NOT refund: the transfer may
    have been sent. The withdrawal stays PROCESSING and a background job asks Paystack again until it gets a definite answer.
  * The transfer webhook is only a nudge: we always ask Paystack for the real status ourselves, as we do for payments.
  * A new payout account cannot be withdrawn to for a cooling-off period (protects against someone who steals a login)."""
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import ghana
from app.core.config import get_settings
from app.core.errors import api_error
from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.catalog import ApprovalStatus, Vendor
from app.models.riders import DeliveryRider
from app.models.wallet import (LedgerEntry, LedgerKind as K, PayoutAccount, PayoutAccountType, WalletAccount, WalletOwner, Withdrawal, WithdrawalStatus as W)
from app.services import audit_service, notification_service as ns, wallet_service as ws
from app.services.payment_providers import MOMO_PROVIDERS, ProviderError, ProviderUnreachable, TransferResult, get_provider

logger = logging.getLogger(__name__)
IN_FLIGHT = (W.PENDING_APPROVAL, W.PROCESSING)
COUNTED_TODAY = (W.PENDING_APPROVAL, W.PROCESSING, W.SUCCESS)
RECONCILE_AFTER = timedelta(minutes=3)


def q(x) -> Decimal:
    return ws.q(x)


# ------------------------------------------------------------------ who owns the wallet
def owner_of(db: Session, account: WalletAccount):
    if account.owner_type == WalletOwner.VENDOR:
        v = db.get(Vendor, account.vendor_id)
        return v, v.owner_user_id, v.approval_status
    r = db.get(DeliveryRider, account.rider_id)
    return r, r.user_id, r.approval_status


def mask(number: str) -> str:
    return "•••• " + number[-4:] if number else ""


# ------------------------------------------------------------------ payout accounts
def active_payout(db: Session, account_id: int) -> PayoutAccount | None:
    return db.scalar(select(PayoutAccount).where(PayoutAccount.account_id == account_id, PayoutAccount.is_active.is_(True)).order_by(PayoutAccount.id.desc()))


def payout_out(p: PayoutAccount | None) -> dict | None:
    if p is None:
        return None
    return {"id": p.id, "type": p.type.value, "provider_code": p.provider_code, "provider_name": p.provider_name, "account_name": p.account_name,
            "account_number_masked": mask(p.account_number), "added_at": p.created_at.isoformat()}


def set_payout_account(db: Session, account: WalletAccount, *, kind: str, provider_code: str, account_number: str, account_name: str, provider_name: str = "") -> PayoutAccount:
    name = re.sub(r"\s+", " ", account_name).strip()
    if len(name) < 2:
        raise api_error(422, "bad_account", "Enter the account holder's name exactly as it appears on the account.")
    if kind == "MOBILE_MONEY":
        if provider_code not in MOMO_PROVIDERS:
            raise api_error(422, "bad_account", "Choose MTN Mobile Money, Telecel Cash or AirtelTigo Money.")
        phone = ghana.normalize_phone(account_number)
        if phone is None:
            raise api_error(422, "bad_account", "Enter a valid Ghana mobile number, e.g. 024 123 4567.")
        number, provider_name = "0" + phone[4:], MOMO_PROVIDERS[provider_code]               # Paystack wants the local format: 0241234567
    else:
        number = re.sub(r"\D", "", account_number)
        if not (8 <= len(number) <= 20) or not provider_code.strip():
            raise api_error(422, "bad_account", "Enter the bank and a valid account number.")
    try:
        code = get_provider().create_recipient(db, kind=kind, name=name, account_number=number, provider_code=provider_code.strip())
    except ProviderError as e:
        logger.error("could not create payout recipient: %s", e)
        raise api_error(503, "payout_setup_failed", "We couldn't verify that account with our payment partner. Check the details and try again.") from e
    for old in db.scalars(select(PayoutAccount).where(PayoutAccount.account_id == account.id, PayoutAccount.is_active.is_(True))):
        old.is_active = False
    p = PayoutAccount(account_id=account.id, type=PayoutAccountType(kind), provider_code=provider_code.strip(), provider_name=provider_name, account_number=number,
                      account_name=name, recipient_code=code, is_active=True)
    db.add(p)
    db.flush()
    _, user_id, _ = owner_of(db, account)
    audit_service.record(db, "payout_account_set", user_id, detail=f"wallet={account.id} {kind} {provider_code} {mask(number)}")
    if user_id:
        ns.notify(db, user_id, "payout_account", "Payout account changed",
                  f"Your payout account is now {payout_out(p)['provider_name'] or provider_code} {mask(number)}. If this wasn't you, contact support now. "
                  f"Withdrawals pause for {get_settings().payout_account_cooldown_hours} hours after a change.", None)
    return p


# ------------------------------------------------------------------ the rules
def _today_total(db: Session, account_id: int) -> Decimal:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return Decimal(db.scalar(select(func.coalesce(func.sum(Withdrawal.amount), 0)).where(Withdrawal.account_id == account_id, Withdrawal.status.in_(COUNTED_TODAY),
                                                                                       Withdrawal.requested_at >= start)))


def eligibility(db: Session, account: WalletAccount, now=None) -> dict:
    """Everything standing between this person and a withdrawal, in plain words. The UI shows it; request_withdrawal enforces it."""
    now = now or utcnow()
    s = get_settings()
    b = ws.balances(db, account.id, now)
    _, _, approval = owner_of(db, account)
    pa = active_payout(db, account.id)
    reasons, withdrawable = [], b["withdrawable"]
    if approval != ApprovalStatus.APPROVED:
        reasons.append("Your account isn't approved yet.")
    if pa is None:
        reasons.append("Add a Mobile Money number or bank account first.")
    else:
        ready = pa.created_at + timedelta(hours=s.payout_account_cooldown_hours)
        if ready > now:
            reasons.append(f"You changed your payout account recently. For your safety you can withdraw from {ready:%d %b %H:%M}.")
    if b["total"] < 0:
        reasons.append(f"You owe KORA EATS {cedis(-b['total'])} (cash you collected or refunded orders). Settle it first.")
    if db.scalar(select(func.count()).select_from(Withdrawal).where(Withdrawal.account_id == account.id, Withdrawal.status.in_(IN_FLIGHT))):
        reasons.append("You already have a withdrawal in progress.")
    remaining_today = max(Decimal(0), Decimal(str(s.max_withdrawal_per_day)) - _today_total(db, account.id))
    cap = min(withdrawable, remaining_today)
    if withdrawable < Decimal(str(s.min_withdrawal)):
        reasons.append(f"The minimum withdrawal is {cedis(s.min_withdrawal)}.")
    elif remaining_today <= 0:
        reasons.append(f"You've reached today's withdrawal limit of {cedis(s.max_withdrawal_per_day)}.")
    info = []
    if b["pending"] > 0:
        info.append(f"{cedis(b['pending'])} is still being held for {s.payout_hold_hours} hours after each delivery, in case of disputes or refunds.")
    if s.withdrawal_approval_threshold > 0:
        info.append(f"Withdrawals above {cedis(s.withdrawal_approval_threshold)} are checked by our team before they are sent.")
    if s.withdrawal_fee > 0:
        info.append(f"A {cedis(s.withdrawal_fee)} fee applies to each withdrawal.")
    return {"can_withdraw": not reasons, "max_now": float(max(Decimal(0), cap)), "minimum": s.min_withdrawal, "reasons": reasons, "info": info,
            "fee": s.withdrawal_fee, "daily_limit": s.max_withdrawal_per_day}


def request_withdrawal(db: Session, account: WalletAccount, amount: Decimal, idempotency_key: str, background: BackgroundTasks | None = None) -> Withdrawal:
    s = get_settings()
    amount = q(amount)
    prior = db.scalar(select(Withdrawal).where(Withdrawal.idempotency_key == idempotency_key, Withdrawal.account_id == account.id))
    if prior is not None:
        return prior                                                         # the same tap again: return the same withdrawal, never a second one
    db.execute(select(WalletAccount.id).where(WalletAccount.id == account.id).with_for_update())       # one withdrawal at a time per wallet
    prior = db.scalar(select(Withdrawal).where(Withdrawal.idempotency_key == idempotency_key, Withdrawal.account_id == account.id))
    if prior is not None:
        return prior
    elig = eligibility(db, account)
    if elig["reasons"]:
        raise api_error(409, "cannot_withdraw", elig["reasons"][0])
    fee = q(s.withdrawal_fee)
    if amount < Decimal(str(s.min_withdrawal)):
        raise api_error(422, "below_minimum", f"The minimum withdrawal is {cedis(s.min_withdrawal)}.")
    if amount > Decimal(str(elig["max_now"])):
        raise api_error(422, "too_much", f"You can withdraw up to {cedis(elig['max_now'])} right now.")
    if amount <= fee:
        raise api_error(422, "below_minimum", f"The amount must be more than the {cedis(fee)} fee.")
    pa = active_payout(db, account.id)
    needs_approval = s.withdrawal_approval_threshold > 0 and amount > Decimal(str(s.withdrawal_approval_threshold))
    w = Withdrawal(account_id=account.id, payout_account_id=pa.id, amount=amount, fee=fee, status=W.PENDING_APPROVAL if needs_approval else W.PROCESSING,
                   reference="kora-wd-" + secrets.token_hex(10), idempotency_key=idempotency_key)
    db.add(w)
    db.flush()
    now = utcnow()
    db.add(LedgerEntry(account_id=account.id, kind=K.WITHDRAWAL, amount=-amount, available_at=now, withdrawal_id=w.id, note=f"Withdrawal to {mask(pa.account_number)}", meta={"reference": w.reference}))
    if fee > 0:
        db.add(LedgerEntry(account_id=ws.platform_account(db).id, kind=K.WITHDRAWAL_FEE, amount=fee, available_at=now, withdrawal_id=w.id, note="Withdrawal fee", meta={"reference": w.reference}))
    db.flush()
    _, user_id, _ = owner_of(db, account)
    if needs_approval:
        audit_service.record(db, "withdrawal_needs_approval", user_id, detail=f"{w.reference} {amount}")
        if user_id:
            ns.notify(db, user_id, "withdrawal", "Withdrawal received", f"{cedis(amount)} is waiting for a quick check by our team. We'll notify you when it's sent.", None, background=background)
    else:
        dispatch(db, w, background)
    return w


# ------------------------------------------------------------------ sending, and learning what happened
def dispatch(db: Session, w: Withdrawal, background: BackgroundTasks | None = None) -> None:
    pa = db.get(PayoutAccount, w.payout_account_id)
    try:
        result = get_provider().initiate_transfer(db, reference=w.reference, amount=w.amount - w.fee, recipient_code=pa.recipient_code, reason="KORA EATS payout")
    except ProviderUnreachable as e:
        logger.error("transfer %s: outcome unknown (%s); leaving it PROCESSING to be confirmed", w.reference, e)
        w.failure_reason = "Waiting for confirmation from our payment partner."
        return                                                              # NOT refunded: it may have been sent. The reconcile job will find out.
    except ProviderError as e:
        logger.error("transfer %s was refused: %s", w.reference, e)
        apply_result(db, w, TransferResult("FAILED", None, str(e)[:250]), background)
        return
    apply_result(db, w, result, background)


def apply_result(db: Session, w: Withdrawal, result: TransferResult, background: BackgroundTasks | None = None) -> bool:
    """Move a withdrawal to its next state exactly once. Returns True if something changed."""
    w = db.scalar(select(Withdrawal).where(Withdrawal.id == w.id).with_for_update())
    if result.transfer_code and not w.transfer_code:
        w.transfer_code = result.transfer_code
    if result.status == "PROCESSING":
        return False
    account = db.get(WalletAccount, w.account_id)
    _, user_id, _ = owner_of(db, account)
    now = utcnow()
    if result.status == "SUCCESS":
        if w.status in (W.SUCCESS,):
            return False
        w.status, w.processed_at, w.failure_reason = W.SUCCESS, now, None
        if user_id:
            ns.notify(db, user_id, "withdrawal", "Money sent", f"{cedis(w.amount - w.fee)} was sent to your payout account.", None, background=background)
        return True
    final = W.REVERSED if result.status == "REVERSED" else W.FAILED
    if w.status in (W.FAILED, W.REVERSED, W.REJECTED):
        return False
    _return_funds(db, w, "Withdrawal failed: money returned" if final == W.FAILED else "Withdrawal bounced back: money returned")
    w.status, w.processed_at, w.failure_reason = final, now, (result.reason or "")[:300]
    if user_id:
        ns.notify(db, user_id, "withdrawal", "Withdrawal didn't go through", f"{cedis(w.amount)} is back in your wallet. {result.reason or ''}".strip(), None, background=background)
    return True


def _return_funds(db: Session, w: Withdrawal, note: str) -> None:
    now = utcnow()
    db.add(LedgerEntry(account_id=w.account_id, kind=K.WITHDRAWAL_REVERSAL, amount=w.amount, available_at=now, withdrawal_id=w.id, note=note, meta={"reference": w.reference}))
    if w.fee > 0:
        db.add(LedgerEntry(account_id=ws.platform_account(db).id, kind=K.WITHDRAWAL_FEE, amount=-w.fee, available_at=now, withdrawal_id=w.id, note="Fee returned", meta={"reference": w.reference}))
    db.flush()


def handle_transfer_webhook(db: Session, reference: str | None, background: BackgroundTasks | None = None) -> None:
    """A transfer.success / failed / reversed webhook arrived (already signature-checked). We never trust its content: we ask Paystack."""
    w = db.scalar(select(Withdrawal).where(Withdrawal.reference == reference)) if reference else None
    if w is None or w.status not in (W.PROCESSING, W.SUCCESS):
        return
    try:
        apply_result(db, w, get_provider().verify_transfer(db, w.reference), background)
    except ProviderError as e:
        logger.warning("could not verify transfer %s from a webhook: %s", reference, e)


def reconcile_processing(db: Session) -> int:
    """Background job: ask Paystack about every withdrawal that has been PROCESSING for a while, until we have a definite answer."""
    n = 0
    old = utcnow() - RECONCILE_AFTER
    for w in db.scalars(select(Withdrawal).where(Withdrawal.status == W.PROCESSING, Withdrawal.requested_at < old).limit(50)).all():
        try:
            if apply_result(db, w, get_provider().verify_transfer(db, w.reference)):
                n += 1
        except ProviderError as e:
            logger.warning("withdrawal %s still unconfirmed: %s", w.reference, e)
    db.commit()
    return n


# ------------------------------------------------------------------ admin
def approve(db: Session, w: Withdrawal, admin_id, background: BackgroundTasks | None = None) -> Withdrawal:
    w = db.scalar(select(Withdrawal).where(Withdrawal.id == w.id).with_for_update())
    if w.status != W.PENDING_APPROVAL:
        raise api_error(409, "not_pending", "This withdrawal isn't waiting for approval.")
    w.status, w.approved_by = W.PROCESSING, admin_id
    audit_service.record(db, "withdrawal_approved", admin_id, detail=f"{w.reference} {w.amount}")
    dispatch(db, w, background)
    return w


def reject(db: Session, w: Withdrawal, admin_id, reason: str, background: BackgroundTasks | None = None) -> Withdrawal:
    w = db.scalar(select(Withdrawal).where(Withdrawal.id == w.id).with_for_update())
    if w.status != W.PENDING_APPROVAL:
        raise api_error(409, "not_pending", "This withdrawal isn't waiting for approval.")
    _return_funds(db, w, "Withdrawal declined: money returned")
    w.status, w.approved_by, w.processed_at, w.failure_reason = W.REJECTED, admin_id, utcnow(), reason[:300]
    audit_service.record(db, "withdrawal_rejected", admin_id, detail=f"{w.reference}: {reason[:100]}")
    _, user_id, _ = owner_of(db, db.get(WalletAccount, w.account_id))
    if user_id:
        ns.notify(db, user_id, "withdrawal", "Withdrawal declined", f"{cedis(w.amount)} is back in your wallet. Reason: {reason}", None, background=background)
    return w


def withdrawal_out(w: Withdrawal) -> dict:
    return {"id": w.id, "amount": float(w.amount), "fee": float(w.fee), "you_receive": float(w.amount - w.fee), "status": w.status.value, "reference": w.reference,
            "requested_at": w.requested_at.isoformat(), "processed_at": w.processed_at.isoformat() if w.processed_at else None, "failure_reason": w.failure_reason}
