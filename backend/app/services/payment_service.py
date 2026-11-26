"""Payment flow. The golden rule: an order becomes PAID only after THIS server has asked the provider
and the provider confirmed the right amount. A browser redirect proves nothing."""
import json
import logging
from datetime import timedelta
from decimal import Decimal

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import api_error
from app.core.security import generate_opaque_token
from app.core.timeutil import utcnow
from app.models.orders import Order, OrderStatus as S
from app.models.payments import Payment, PaymentPurpose, PaymentStatus
from app.models.user import User
from app.services import audit_service, order_service
from app.core.money import CURRENCY_CODE
from app.services.payment_providers import KNOWN_METHODS, ProviderError, enabled_methods, get_provider

logger = logging.getLogger(__name__)
PENDING_REUSE_MINUTES = 25


def _provider_error(e: ProviderError):
    logger.error("Payment provider error: %s", e)
    return api_error(503, "payment_unavailable", "Payments are temporarily unavailable. Please try again shortly.")


def initiate(db: Session, user: User, order_id: int, idem_key: str, method: str, background: BackgroundTasks) -> Payment:
    if method not in KNOWN_METHODS:
        raise api_error(422, "bad_method", "Unknown payment method.")
    if method not in enabled_methods():
        raise api_error(422, "method_unavailable", "That payment method isn't available yet. Please choose another.")
    order = order_service.lock_order(db, order_id)
    if order is None or order.customer_id != user.id:
        raise api_error(404, "not_found", "Order not found.")
    key = f"{user.id}:{idem_key}"

    # 1) Same idempotency key = same request being retried -> return the original payment, create nothing.
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == key))
    if existing:
        if existing.order_id != order.id:
            raise api_error(409, "idempotency_conflict", "That idempotency key was used for a different order.")
        return existing

    if order.status not in (S.PENDING_PAYMENT, S.PAYMENT_FAILED):
        raise api_error(409, "not_payable", "This order can't be paid right now.")

    # 2) Different key but a fresh unpaid attempt exists -> reuse it (protects against double clicks / two tabs).
    fresh = db.scalar(select(Payment).where(
        Payment.order_id == order.id, Payment.status == PaymentStatus.PENDING,
        Payment.created_at > utcnow() - timedelta(minutes=PENDING_REUSE_MINUTES)).order_by(Payment.id.desc()))
    if fresh:
        return fresh

    if order.status == S.PAYMENT_FAILED:
        order_service.change_status(db, order, S.PENDING_PAYMENT, "customer", user.id, "Retrying payment", background)

    # Any outstanding balance is collected with this payment: the customer pays order + debt in one go.
    from app.services import debt_service
    debt = debt_service.collectible(db, user.id, lock=True)
    payable = order.total + debt

    settings, provider = get_settings(), get_provider()
    reference = "KEP-" + generate_opaque_token()[:20].replace("-", "x").replace("_", "y")
    payment = Payment(order_id=order.id, customer_id=user.id, purpose=PaymentPurpose.ORDER, debt_amount=debt,
                      provider=provider.name, reference=reference, idempotency_key=key,
                      amount=payable, currency=CURRENCY_CODE, method=method)
    db.add(payment)
    db.flush()
    try:
        session = provider.create_checkout(
            db, reference=reference, amount=payable, currency=CURRENCY_CODE, method=method, customer_email=user.email,
            customer_name=user.profile.full_name, phone=order.customer_phone,
            description=f"KORA EATS order {order.code}" + (f" + outstanding balance {debt}" if debt > 0 else ""),
            return_url=f"{settings.frontend_base_url}/payment-result.html?order={order.id}")
    except ProviderError as e:
        payment.status, payment.failure_reason = PaymentStatus.FAILED, "provider_error"
        db.commit()
        raise _provider_error(e)
    payment.provider_reference, payment.checkout_url = session.provider_reference, session.checkout_url
    audit_service.record(db, "payment_initiated", user.id, detail=f"order={order.code} ref={reference}")
    db.commit()
    return payment


def initiate_debt(db: Session, user: User, amount: Decimal, method: str, idem_key: str, background: BackgroundTasks) -> Payment:
    """Customer pays off an outstanding balance with no order attached (purpose=DEBT)."""
    if method not in KNOWN_METHODS:
        raise api_error(422, "bad_method", "Unknown payment method.")
    if method not in enabled_methods():
        raise api_error(422, "method_unavailable", "That payment method isn't available yet. Please choose another.")
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise api_error(409, "no_debt", "You don't owe anything right now.")
    key = f"{user.id}:{idem_key}"
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == key))
    if existing:
        if existing.purpose != PaymentPurpose.DEBT or existing.customer_id != user.id:
            raise api_error(409, "idempotency_conflict", "That idempotency key was used for something else.")
        return existing

    settings, provider = get_settings(), get_provider()
    reference = "KED-" + generate_opaque_token()[:20].replace("-", "x").replace("_", "y")
    payment = Payment(order_id=None, customer_id=user.id, purpose=PaymentPurpose.DEBT, debt_amount=amount,
                      provider=provider.name, reference=reference, idempotency_key=key,
                      amount=amount, currency=CURRENCY_CODE, method=method)
    db.add(payment)
    db.flush()
    try:
        session = provider.create_checkout(
            db, reference=reference, amount=amount, currency=CURRENCY_CODE, method=method, customer_email=user.email,
            customer_name=user.profile.full_name, phone=user.profile.phone, description="KORA EATS outstanding balance",
            return_url=f"{settings.frontend_base_url}/payments.html?debt={payment.id}")
    except ProviderError as e:
        payment.status, payment.failure_reason = PaymentStatus.FAILED, "provider_error"
        db.commit()
        raise _provider_error(e)
    payment.provider_reference, payment.checkout_url = session.provider_reference, session.checkout_url
    audit_service.record(db, "debt_payment_initiated", user.id, detail=f"amount={amount} ref={reference}")
    db.commit()
    return payment


def verify_debt_payment(db: Session, p: Payment, background: BackgroundTasks | None = None) -> Payment:
    """Ask the provider about a standalone debt payment; on success the money is applied to debts (oldest first)."""
    if p.purpose != PaymentPurpose.DEBT:
        raise api_error(404, "not_found", "Payment not found.")
    if p.status == PaymentStatus.SUCCESS:
        return p
    if p.status != PaymentStatus.PENDING:
        return p
    try:
        result = get_provider(p.provider).verify(db, p.reference)
    except ProviderError as e:
        logger.error("verify failed for debt payment %s: %s", p.reference, e)
        return p                                            # stay PENDING; we'll check again
    if result.status == "SUCCESS":
        if result.amount is None or Decimal(result.amount) != p.amount or (result.currency or "").upper() != p.currency:
            p.status, p.failure_reason, p.verified_at = PaymentStatus.FAILED, "amount_mismatch", utcnow()
            audit_service.record(db, "payment_amount_mismatch", p.customer_id,
                                 detail=f"ref={p.reference} expected={p.amount} got={result.amount} {result.currency}")
            logger.critical("DEBT PAYMENT AMOUNT MISMATCH ref=%s expected=%s got=%s", p.reference, p.amount, result.amount)
        else:
            p.status, p.verified_at = PaymentStatus.SUCCESS, utcnow()
            audit_service.record(db, "debt_payment_verified", p.customer_id, detail=f"amount={p.amount} ref={p.reference}")
            from app.services import debt_service
            debt_service.record_repayment(db, p.customer_id, p.id, p.debt_amount or p.amount)
    elif result.status == "FAILED":
        p.status, p.failure_reason, p.verified_at = PaymentStatus.FAILED, "declined_or_cancelled", utcnow()
    db.commit()
    return p


def verify_order(db: Session, order_id: int, background: BackgroundTasks | None = None, user: User | None = None) -> Order:
    """Asks the provider about every pending payment of the order and updates our records.
    Safe to call any number of times (idempotent)."""
    order = order_service.lock_order(db, order_id)
    if order is None or (user is not None and order.customer_id != user.id):
        raise api_error(404, "not_found", "Order not found.")
    pending = db.scalars(select(Payment).where(Payment.order_id == order.id, Payment.status == PaymentStatus.PENDING)
                         .order_by(Payment.id)).all()
    for p in pending:
        try:
            result = get_provider(p.provider).verify(db, p.reference)
        except ProviderError as e:
            logger.error("verify failed for %s: %s", p.reference, e)
            continue                                    # stay PENDING; we'll check again
        if result.status == "SUCCESS":
            _settle_success(db, order, p, result, background)
        elif result.status == "FAILED":
            p.status, p.failure_reason, p.verified_at = PaymentStatus.FAILED, "declined_or_cancelled", utcnow()
            if order.status == S.PENDING_PAYMENT and not _has_other_open_payment(db, order, p):
                order_service.change_status(db, order, S.PAYMENT_FAILED, "system", None, "Payment failed", background)
    db.commit()
    return order


def _has_other_open_payment(db: Session, order: Order, current: Payment) -> bool:
    return db.scalar(select(Payment.id).where(Payment.order_id == order.id, Payment.id != current.id,
                                              Payment.status == PaymentStatus.PENDING)) is not None


def _settle_success(db: Session, order: Order, p: Payment, result, background) -> None:
    # The provider says it succeeded. Make sure it's the RIGHT money before releasing food.
    if result.amount is None or Decimal(result.amount) != p.amount or (result.currency or "").upper() != p.currency:
        p.status, p.failure_reason, p.verified_at = PaymentStatus.FAILED, "amount_mismatch", utcnow()
        audit_service.record(db, "payment_amount_mismatch", order.customer_id,
                             detail=f"ref={p.reference} expected={p.amount} got={result.amount} {result.currency}")
        logger.critical("PAYMENT AMOUNT MISMATCH ref=%s expected=%s got=%s", p.reference, p.amount, result.amount)
        return
    p.status, p.verified_at = PaymentStatus.SUCCESS, utcnow()
    audit_service.record(db, "payment_verified", order.customer_id, detail=f"order={order.code} ref={p.reference}")
    if p.debt_amount and p.debt_amount > 0:
        from app.services import debt_service
        debt_service.record_repayment(db, order.customer_id, p.id, p.debt_amount)
    if order.status in (S.PENDING_PAYMENT, S.PAYMENT_FAILED):
        if order.status == S.PAYMENT_FAILED:                       # paid late after we'd marked it failed
            order_service.change_status(db, order, S.PENDING_PAYMENT, "system", None, "Late payment received", background)
        order_service.change_status(db, order, S.PAID, "system", None, "Payment confirmed", background)
    else:
        # Paid for an order that was already cancelled/expired: flag for a refund, never lose track of money.
        audit_service.record(db, "payment_for_closed_order", order.customer_id, detail=f"order={order.code} status={order.status.value}")
        logger.warning("Payment %s succeeded for order %s in status %s - needs refund review", p.reference, order.code, order.status.value)


def handle_webhook(db: Session, provider_name: str, headers: dict[str, str], body: bytes, background: BackgroundTasks) -> None:
    provider = get_provider(provider_name)
    try:
        reference = provider.authenticate_webhook(headers, body)
    except ProviderError as e:
        raise api_error(400, "bad_webhook", "Webhook rejected.") from e
    try:
        event = str(json.loads(body).get("event", ""))
    except (ValueError, AttributeError):
        event = ""
    if event.startswith("transfer."):                # a payout changed state: handled by the payout service (which asks Paystack, never trusts the body)
        from app.services import payout_service
        payout_service.handle_transfer_webhook(db, reference, background)
        db.commit()
        return
    p = db.scalar(select(Payment).where(Payment.reference == reference)) if reference else None
    if p is not None:                                # unknown reference or an unrelated event: acknowledge (200) and ignore
        verify_order(db, p.order_id, background)     # trust nothing in the body: ask the provider


def history(db: Session, user: User) -> list[dict]:
    from sqlalchemy import or_
    rows = db.execute(select(Payment, Order.code).outerjoin(Order, Order.id == Payment.order_id)
                      .where(or_(Order.customer_id == user.id, Payment.customer_id == user.id))
                      .order_by(Payment.id.desc()).limit(100)).all()
    return [{"id": p.id, "order_id": p.order_id, "order_code": code or "Balance", "amount": float(p.amount), "currency": p.currency,
             "method": p.method, "status": p.status.value, "reference": p.reference,
             "created_at": p.created_at.isoformat()} for p, code in rows]


@order_service.register_serializer
def _payment_summary(db: Session, order: Order) -> dict:
    p = db.scalar(select(Payment).where(Payment.order_id == order.id).order_by(Payment.id.desc()).limit(1))
    return {"payment": None if p is None else {"status": p.status.value, "method": p.method, "reference": p.reference,
                                                "checkout_url": p.checkout_url if p.status == PaymentStatus.PENDING else None}}
