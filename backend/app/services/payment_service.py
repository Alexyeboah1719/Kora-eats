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
from app.models.payments import Payment, PaymentStatus
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

    settings, provider = get_settings(), get_provider()
    reference = "KEP-" + generate_opaque_token()[:20].replace("-", "x").replace("_", "y")
    payment = Payment(order_id=order.id, provider=provider.name, reference=reference, idempotency_key=key,
                      amount=order.total, currency=CURRENCY_CODE, method=method)
    db.add(payment)
    db.flush()
    try:
        session = provider.create_checkout(
            db, reference=reference, amount=order.total, currency=CURRENCY_CODE, method=method, customer_email=user.email,
            customer_name=user.profile.full_name, phone=order.customer_phone, description=f"KORA EATS order {order.code}",
            return_url=f"{settings.frontend_base_url}/payment-result.html?order={order.id}")
    except ProviderError as e:
        payment.status, payment.failure_reason = PaymentStatus.FAILED, "provider_error"
        db.commit()
        raise _provider_error(e)
    payment.provider_reference, payment.checkout_url = session.provider_reference, session.checkout_url
    audit_service.record(db, "payment_initiated", user.id, detail=f"order={order.code} ref={reference}")
    db.commit()
    return payment


def verify_order(db: Session, order_id: int, background: BackgroundTasks | None = None, user: User | None = None) -> Order:
    """Asks the provider about every pending payment of the order and updates our records.
    Safe to call any number of times (idempotent)."""
    order = order_service.lock_order(db, order_id)
    if order is None or (user is not None and order.customer_id != user.id):
        raise api_error(404, "not_found", "Order not found.")
    
    # COD orders skip payment verification and go directly to PAID status
    if order.payment_mode == "COD":
        if order.status == S.PENDING_PAYMENT:
            order_service.change_status(db, order, S.PAID, "system", None, "Cash on Delivery - payment on delivery", background)
        db.commit()
        return order
    
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
    rows = db.execute(select(Payment, Order.code).join(Order, Order.id == Payment.order_id)
                      .where(Order.customer_id == user.id).order_by(Payment.id.desc()).limit(100)).all()
    return [{"id": p.id, "order_id": p.order_id, "order_code": code, "amount": float(p.amount), "currency": p.currency,
             "method": p.method, "status": p.status.value, "reference": p.reference,
             "created_at": p.created_at.isoformat()} for p, code in rows]


@order_service.register_serializer
def _payment_summary(db: Session, order: Order) -> dict:
    p = db.scalar(select(Payment).where(Payment.order_id == order.id).order_by(Payment.id.desc()).limit(1))
    return {"payment": None if p is None else {"status": p.status.value, "method": p.method, "reference": p.reference,
                                                "checkout_url": p.checkout_url if p.status == PaymentStatus.PENDING else None}}


def initiate_standalone(db: Session, user: User, reference: str, amount: Decimal, currency: str, method: str, 
                        description: str, callback_url: str | None = None) -> Payment:
    """Initiate a payment not tied to an order (e.g., rider top-up)."""
    if method not in KNOWN_METHODS:
        raise api_error(422, "bad_method", "Unknown payment method.")
    if method not in enabled_methods():
        raise api_error(422, "method_unavailable", "That payment method isn't available yet. Please choose another.")
    
    settings, provider = get_settings(), get_provider()
    
    payment = Payment(
        provider=provider.name, 
        reference=reference, 
        idempotency_key=f"STANDALONE_{user.id}_{reference}",
        amount=amount, 
        currency=currency, 
        method=method,
        description=description
    )
    db.add(payment)
    db.flush()
    
    try:
        session = provider.create_checkout(
            db, reference=reference, amount=amount, currency=currency, method=method, 
            customer_email=user.email,
            customer_name=user.profile.full_name, 
            phone=user.profile.phone or "",
            description=description,
            return_url=callback_url or f"{settings.frontend_base_url}/rider/dashboard.html"
        )
    except ProviderError as e:
        payment.status, payment.failure_reason = PaymentStatus.FAILED, "provider_error"
        db.commit()
        raise _provider_error(e)
    
    payment.provider_reference, payment.checkout_url = session.provider_reference, session.checkout_url
    audit_service.record(db, "standalone_payment_initiated", user.id, detail=f"ref={reference} amount={amount}")
    db.commit()
    return payment


def verify_payment(db: Session, reference: str) -> Payment | None:
    """Verify a standalone payment by reference (for rider top-ups)."""
    payment = db.scalar(select(Payment).where(Payment.reference == reference))
    if not payment:
        return None
    
    if payment.status == PaymentStatus.SUCCESS:
        return payment
    
    if payment.status != PaymentStatus.PENDING:
        return payment
    
    try:
        result = get_provider(payment.provider).verify(db, reference)
    except ProviderError as e:
        logger.error("verify failed for %s: %s", reference, e)
        return payment
    
    if result.status == "SUCCESS":
        payment.status, payment.verified_at = PaymentStatus.SUCCESS, utcnow()
        db.commit()
    elif result.status == "FAILED":
        payment.status, payment.failure_reason, payment.verified_at = PaymentStatus.FAILED, "declined_or_cancelled", utcnow()
        db.commit()
    
    return payment
