"""What happens when food comes back undelivered.

Flow (the ONLY path for a return):
  rider reports the problem at the door  -> order RETURNED            (vendor must ACCEPT it)
  vendor accepts                          -> ACCEPTED_RETURN          (rider takes it back)
  rider hands it to the vendor            -> RETURNED_TO_VENDOR       (terminal) + settlement
  vendor declines (food spoiled, wrong address...) -> RETURN_DECLINED (terminal) + settlement, no debt

Settlement always pays the vendor (food, less commission and any discount the vendor funded) and the rider
(their share of the delivery fee): KORA EATS fronts the money because the customer didn't get the food.
  * COD, customer refused / not there : the customer OWES the full order total (food + delivery) to KORA EATS.
    Nothing was refunded (there was nothing to refund).
  * Paid online, customer not there    : the customer REQUESTS a refund of 80% of the food cost and an admin
    approves it before money moves (the delivery fee they already paid is kept — the rider made the trip,
    and the vendor was paid for the food; KORA EATS keeps the 20%). No debt: they settled everything upfront.
  * Vendor declined the return         : nobody is charged; KORA EATS carries the whole cost (platform loss).
The food is NEVER put back on sale: it was prepared for one customer and comes back to the vendor as a loss."""
import logging
from decimal import Decimal

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.orders import Order, OrderStatus as S
from app.models.payments import Payment, PaymentStatus
from app.models.riders import Delivery, DeliveryRider
from app.models.user import User, UserRole
from app.services import debt_service, notification_service as ns, order_events, order_service, payment_providers, wallet_service
from app.services.pricing_service import money

logger = logging.getLogger(__name__)
REASONS = {"COD_REFUSED": "Customer refused to pay", "NO_SHOW": "Customer was not there", "WRONG_ADDRESS": "Wrong address",
           "OTHER": "Other problem"}
NO_DEBT_REASONS = {"WRONG_ADDRESS", "OTHER"}          # ambiguous cases: the platform carries the cost, the customer isn't charged


def _notify_vendor(db, order: Order, title: str, body: str, background) -> None:
    owner = getattr(order.vendor, "owner_user_id", None)
    if owner:
        ns.notify(db, owner, "order_return", title, body, "/vendor/dashboard.html#orders", background=background)


def _notify_customer(db, order: Order, type_: str, title: str, body: str, background) -> None:
    ns.notify(db, order.customer_id, type_, title, body, f"/order.html?id={order.id}", background=background)


def _notify_rider(db, order: Order, d: Delivery | None, title: str, body: str, background) -> None:
    if d is not None:
        ns.notify(db, d.rider.user_id, "return_update", title, body, "/rider/dashboard.html", background=background)


def start(db: Session, order: Order, reason: str, note: str | None, actor_id, actor_role: str, background) -> Order:
    """Rider (or admin) says the food is coming back. The vendor must accept before the rider sets off."""
    if reason not in REASONS:
        raise api_error(422, "bad_reason", "Choose a reason for the return.")
    if order.status not in (S.PICKED_UP, S.OUT_FOR_DELIVERY, S.RIDER_ARRIVED):
        raise api_error(409, "cannot_return", f"An order that is {order.status.value} can't be returned.")
    order.return_reason = reason
    order_service.change_status(db, order, S.RETURNED, actor_role, actor_id,
                                f"Returning to vendor: {REASONS[reason]}" + (f" — {note}" if note else ""), background)
    _notify_vendor(db, order, "Food is coming back — please accept the return",
                   f"Order {order.code}: {REASONS[reason]}. Accept the return so the rider can bring it back to you. "
                   "The food will NOT go back on sale. You'll be paid for it once the rider confirms receipt.", background)
    return order


def vendor_decide(db: Session, order: Order, accept: bool, actor_id, note: str | None, background) -> Order:
    """Vendor accepts (rider may bring it back) or declines (nobody is charged; the platform carries the cost)."""
    if order.status != S.RETURNED:
        raise api_error(409, "not_returned", "This order has no return waiting for your answer.")
    d = db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    if accept:
        order_service.change_status(db, order, S.ACCEPTED_RETURN, "vendor", actor_id,
                                    note or "Vendor accepted the return", background)
        _notify_rider(db, order, d, "Return accepted", f"{order.vendor.name} accepted the return of order {order.code}. Bring the food back to them.", background)
        _notify_customer(db, order, "order_return", "Your order is being returned",
                         f"Order {order.code} is coming back to {order.vendor.name}. We'll let you know what happens next.", background)
    else:
        order_service.change_status(db, order, S.RETURN_DECLINED, "vendor", actor_id,
                                    note or "Vendor declined the returned food", background)
        _notify_rider(db, order, d, "Return declined", f"{order.vendor.name} couldn't take order {order.code} back. Contact support if you're unsure what to do.", background)
        settle(db, order, background)
    return order


def rider_confirm(db: Session, order: Order, d: Delivery, actor_id, background) -> Order:
    """The rider physically handed the food to the vendor. Terminal state + money."""
    if order.status != S.ACCEPTED_RETURN:
        raise api_error(409, "not_accepted", "The vendor hasn't accepted this return yet.")
    d.returned_at = utcnow()
    order_service.change_status(db, order, S.RETURNED_TO_VENDOR, "rider", actor_id, "Food handed back to the vendor", background)
    settle(db, order, background)
    return order


# ------------------------------------------------------------------ money
@order_events.subscribe
def _settle_on_terminal_return(db, order, old, new, actor_role, background):
    """Whatever path closed the return (vendor confirm/decline, admin override), money is settled exactly once."""
    if new in (S.RETURNED_TO_VENDOR, S.RETURN_DECLINED):
        settle(db, order, background)


def settle(db: Session, order: Order, background: BackgroundTasks | None = None) -> None:
    """Idempotent. Pays vendor + rider, then applies the customer side (debt or food refund)."""
    if order.return_outcome is not None:
        return
    cod = order.payment_mode == "COD"
    reason = order.return_reason or "OTHER"
    vendor_payout, rider_payout = wallet_service.post_return_earnings(db, order)
    order.return_outcome = "PLATFORM_LOSS" if order.status == S.RETURN_DECLINED or reason in NO_DEBT_REASONS else ("DEBT" if cod else "FOOD_REFUND")
    if cod and order.return_outcome == "DEBT":
        order.paid_at = order.paid_at or utcnow()          # treated as 'account settled by debt' for notifications/history
        debt_service.create(db, order, "COD_REFUSED" if reason == "COD_REFUSED" else "NO_SHOW", reason,
                            food=order_food_amount(order), delivery=money(order.delivery_fee or 0))
    # FOOD_REFUND needs no action here: the customer already paid the delivery fee as part of their order
    # total, so it is simply kept — no debt is created (that would charge it twice). Nothing is refunded
    # automatically: the customer must request it and an admin approves it (refund_amount = 80% of food).
    if order.return_outcome in ("DEBT", "FOOD_REFUND"):
        debt_service.record_no_show(db, order, background)
    _notify_customer(db, order, "order_returned", "Your order came back",
                     _customer_message(order, vendor_payout, rider_payout), background)


def _customer_message(order: Order, vendor_payout, rider_payout) -> str:
    code = order.code
    if order.return_outcome == "DEBT":
        return (f"Order {code} was not delivered. You owe KORA EATS {cedis(order.total)} for it (food {cedis(order_food_amount(order))} + "
                f"delivery {cedis(order.delivery_fee)}). KORA EATS has already paid the restaurant and the rider, so this is owed to KORA EATS only. "
                "It will be added to your next online payment; pay on delivery is paused until then.")
    if order.return_outcome == "FOOD_REFUND":
        return (f"Order {code} was not delivered because you weren't available. You can request a refund of {cedis(refund_amount(order))} "
                f"(80% of the food cost) from this order — our team reviews each request. The {cedis(order.delivery_fee)} delivery fee isn't "
                "refunded because the rider made the trip.")
    return f"Order {code} was returned to {order.vendor.name} and could not be completed. Contact support if you have questions."


def order_food_amount(order: Order) -> Decimal:
    """What the food part of the order cost the customer (after any discount, which we treat as coming off the food)."""
    return money(max(Decimal("0"), order.total - money(order.delivery_fee or 0)))


def refund_amount(order: Order) -> Decimal:
    """What an approved prepaid refund pays back: 80% of the food cost. KORA EATS keeps 20% to cover its
    costs; the delivery fee was already kept and paid to the rider, and the vendor was paid for the food."""
    return money(order_food_amount(order) * Decimal("0.8"))


def request_refund(db: Session, order: Order, background: BackgroundTasks | None = None) -> None:
    """The customer asks for their prepaid refund. An admin must approve it before any money moves."""
    if order.return_outcome != "FOOD_REFUND" or order.status != S.RETURNED_TO_VENDOR:
        raise api_error(409, "not_refundable", "This order isn't eligible for a refund.")
    if order.refund_status in ("PENDING", "APPROVED") or order.food_refund_reference or order.food_refunded_at:
        raise api_error(409, "refund_exists", "A refund has already been requested for this order.")
    order.refund_status = "PENDING"
    order.refund_requested_at = utcnow()
    amount = cedis(refund_amount(order))
    for admin_id in db.scalars(select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))).all():
        ns.notify(db, admin_id, "refund_request", "Refund requested",
                  f"Order {order.code}: the customer asked for a refund of {amount} (80% of the food cost). "
                  "Review it in Admin → Orders.", f"/admin/dashboard.html#orders", background=background)


def decide_refund(db: Session, order: Order, approve: bool, admin: User, note: str | None,
                  background: BackgroundTasks | None = None) -> None:
    """Admin approves or declines a customer's refund request."""
    if order.refund_status != "PENDING":
        raise api_error(409, "no_request", "There's no pending refund request for this order.")
    if approve:
        order.refund_status = "APPROVED"
        order.food_refund_pending = True                    # the scheduler's refund job sends it; we try immediately too
        try:
            refund_food(db, order)                          # talks to Paystack: POST /refund on the original transaction
        except Exception:
            logger.exception("immediate food refund for order %s failed; the scheduler will retry", order.id)
        _close_refunded_order(db, order, admin.id, background)
        if order.status == S.RETURNED_TO_VENDOR:
            _notify_customer(db, order, "refund_approved", "Refund approved",
                             f"Your refund of {cedis(refund_amount(order))} for order {order.code} was approved and will be sent "
                             "to your original payment method shortly.", background)
    else:
        order.refund_status = "DECLINED"
        order.refund_review_note = (note or "").strip()[:300] or None
        _notify_customer(db, order, "refund_declined", "Refund request declined",
                         f"Your refund request for order {order.code} was declined."
                         + (f" Note from our team: {order.refund_review_note}" if order.refund_review_note else "")
                         + " Contact support if you have questions.", background)


def _close_refunded_order(db: Session, order: Order, admin_id, background) -> None:
    """Once the provider accepted the refund, the order itself becomes REFUNDED (which notifies the customer)."""
    if order.status == S.RETURNED_TO_VENDOR and order.food_refund_reference:
        order_service.change_status(db, order, S.REFUNDED, "admin", admin_id, "Refund sent to customer", background)


def refund_pending_orders(db: Session, limit: int = 20) -> int:
    """Background job: send food refunds that are still waiting (a provider call failed earlier). Returns how many were handled."""
    rows = db.scalars(select(Order).where(Order.status == S.RETURNED_TO_VENDOR, Order.food_refund_pending.is_(True),
                                          Order.food_refund_reference.is_(None)).order_by(Order.id).limit(limit)).all()
    for o in rows:
        try:
            refund_food(db, o)
            _close_refunded_order(db, o, None, None)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("food refund for order %s failed again", o.id)
    return len(rows)


def refund_food(db: Session, order: Order) -> bool:
    """Send the food-only refund to the provider. Idempotent. Raises if the provider refuses (caller decides what to do)."""
    if order.food_refund_reference or order.food_refunded_at or not order.food_refund_pending:
        return False
    p = db.scalar(select(Payment).where(Payment.order_id == order.id, Payment.status == PaymentStatus.SUCCESS).order_by(Payment.id.desc()).limit(1))
    if p is None:
        logger.error("order %s needs a food refund but has no successful payment", order.id)
        return False
    amount = refund_amount(order)
    if amount <= 0:
        order.food_refund_pending = False
        return False
    result = payment_providers.get_provider(p.provider).refund(db, reference=p.reference, amount=amount, currency=p.currency,
                                                                reason=f"KORA EATS order {order.code}: food not delivered")
    if result.status == "FAILED":
        raise RuntimeError(f"provider refused the food refund for order {order.id}: {result.reason}")
    order.food_refund_reference = (result.refund_reference or "pending")[:60]
    if result.status == "SUCCESS":
        order.food_refunded_at = utcnow()
        order.food_refund_pending = False
    db.flush()
    return True
