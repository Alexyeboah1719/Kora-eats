"""Customer debts and their repayment.

Rules this module enforces:
  * A debt is created once per order (unique constraint), when food comes back undelivered.
  * Outstanding = amount - (applied - reversed), summed over the customer's debts. Never stored.
  * Money only counts once the payment provider has confirmed it (payment_service does that).
  * Repayment is applied oldest debt first. A DebtRepayment with debt_id NULL is CREDIT: money the
    customer paid that no debt needed (yet). Creating a debt first consumes standing credit.
  * While a debt is outstanding the customer must pay ONLINE: cash on delivery is refused at checkout."""
import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import api_error
from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.ai import CustomerPreference
from app.models.debt import CustomerDebt, DebtRepayment
from app.models.orders import Order
from app.models.payments import Payment, PaymentPurpose
from app.models.user import User
from app.services import notification_service as ns
from app.services.pricing_service import money

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ balances
def outstanding_of(d: CustomerDebt) -> Decimal:
    return money(max(Decimal("0"), d.amount - sum((r.applied - r.reversed for r in d.repayments), Decimal("0"))))


def _sums(db: Session, user_id) -> tuple[Decimal, Decimal]:
    """(money applied to debts, money paid in total) for a customer."""
    paid = db.scalar(select(func.coalesce(func.sum(DebtRepayment.applied - DebtRepayment.reversed), 0))
                     .select_from(CustomerDebt).outerjoin(DebtRepayment, DebtRepayment.debt_id == CustomerDebt.id)
                     .where(CustomerDebt.customer_id == user_id)) or Decimal("0")
    owed = db.scalar(select(func.coalesce(func.sum(CustomerDebt.amount), 0)).where(CustomerDebt.customer_id == user_id,
                                                                                  CustomerDebt.kind != "CREDIT")) or Decimal("0")
    return money(paid), money(owed)


def balance(db: Session, user_id) -> Decimal:
    """What the customer owes right now."""
    applied, owed = _sums(db, user_id)
    return money(max(Decimal("0"), owed - applied))


def credit(db: Session, user_id) -> Decimal:
    """Money the customer paid that is not attached to any debt yet."""
    applied, owed = _sums(db, user_id)
    return money(max(Decimal("0"), applied - owed))


def _debts(db: Session, user_id, lock: bool = False):
    q = select(CustomerDebt).where(CustomerDebt.customer_id == user_id, CustomerDebt.kind != "CREDIT").order_by(CustomerDebt.id)
    if lock:
        q = q.with_for_update(of=CustomerDebt)
    return list(db.scalars(q).unique())


def summary(db: Session, user: User) -> dict:
    debts = []
    for d in _debts(db, user.id):
        outstanding = outstanding_of(d)
        if outstanding <= 0:
            continue
        debts.append({"id": d.id, "order_id": d.order_id, "order_code": d.order_code, "kind": d.kind, "reason": d.reason,
                      "amount": float(d.amount), "food_amount": float(d.food_amount), "delivery_amount": float(d.delivery_amount),
                      "outstanding": float(outstanding), "note": d.note, "created_at": d.created_at.isoformat()})
    owed = balance(db, user.id)
    return {"owed": float(owed), "credit": float(credit(db, user.id)), "debts": debts,
            "no_shows": no_show_count(db, user.id),
            "must_pay_online": owed > 0,
            "explanation": ("You owe KORA EATS for food that was ordered but never delivered. KORA EATS has already paid the restaurant "
                            "and the rider for that order, so this debt is owed to KORA EATS only. It is added to your next online "
                            "payment; pay on delivery is unavailable until it is settled.") if owed > 0 else ""}


def blocking_message(owed: Decimal) -> str:
    return (f"You have an outstanding balance of {cedis(owed)} with KORA EATS. Pay it online with your next order "
            "(it is added to your order total) before using pay on delivery.")


# ------------------------------------------------------------------ creating a debt
def create(db: Session, order: Order, kind: str, reason: str, food: Decimal, delivery: Decimal, note: str | None = None) -> CustomerDebt | None:
    """Record what the customer owes for an undelivered order. Returns None if a debt already exists for it."""
    if db.scalar(select(CustomerDebt.id).where(CustomerDebt.order_id == order.id, CustomerDebt.kind == kind)):
        return None
    amount = money(food + delivery)
    if amount <= 0:
        return None
    d = CustomerDebt(customer_id=order.customer_id, order_id=order.id, order_code=order.code, kind=kind, reason=reason,
                     amount=amount, food_amount=money(food), delivery_amount=money(delivery), note=note)
    db.add(d)
    db.flush()
    settle_from_credit(db, order.customer_id)
    if outstanding_of(d) <= 0:
        notify_paid(db, order.customer_id, d.order_code, d.amount)
    else:
        notify_debt_created(db, order.customer_id, d.order_code, d.amount, d.food_amount, d.delivery_amount)
    return d


# ------------------------------------------------------------------ credit
def _credit_rows(db: Session, user_id):
    """Confirmed repayments not yet attached to a debt (oldest first)."""
    return list(db.scalars(select(DebtRepayment).where(DebtRepayment.debt_id.is_(None), DebtRepayment.applied > DebtRepayment.reversed)
                           .order_by(DebtRepayment.id)).unique())


def settle_from_credit(db: Session, user_id) -> Decimal:
    """Attach standing credit to the customer's oldest open debts. Returns the amount used."""
    used_total = Decimal("0")
    rows = _credit_rows(db, user_id)
    if not rows:
        return used_total
    for d in _debts(db, user_id, lock=True):
        for r in rows:
            spare = r.applied - r.reversed
            if spare <= 0:
                continue
            take = min(spare, outstanding_of(d))
            if take <= 0:
                continue
            db.add(DebtRepayment(debt_id=d.id, payment_id=r.payment_id, amount=take, applied=take, meta={"credit_of_repayment": r.id}))
            r.reversed += take
            used_total += take
            db.flush()
            if outstanding_of(d) <= 0:
                notify_paid(db, user_id, d.order_code, d.amount)
                break
    return used_total


# ------------------------------------------------------------------ applying a confirmed payment
def record_repayment(db: Session, user_id, payment_id: int, amount: Decimal) -> Decimal:
    """A payment was confirmed by the provider. Attach `amount` to this customer's debts (oldest first);
    whatever no debt needs becomes credit (debt_id NULL). Returns the amount attached to debts."""
    amount = money(amount)
    if amount <= 0:
        return Decimal("0")
    left = amount
    for d in _debts(db, user_id, lock=True):
        if left <= 0:
            break
        take = min(left, outstanding_of(d))
        if take <= 0:
            continue
        db.add(DebtRepayment(debt_id=d.id, payment_id=payment_id, amount=take, applied=take))
        db.flush()
        left -= take
        if outstanding_of(d) <= 0:
            notify_paid(db, user_id, d.order_code, d.amount)
    if left > 0:
        db.add(DebtRepayment(debt_id=None, payment_id=payment_id, amount=left, applied=left))
        db.flush()
    return money(amount - left)


def has_repayment(db: Session, payment_id: int) -> bool:
    return db.scalar(select(DebtRepayment.id).where(DebtRepayment.payment_id == payment_id).limit(1)) is not None


def reverse_repayment(db: Session, payment_id: int) -> None:
    """A confirmed debt repayment was refunded by the provider: the money is no longer applied."""
    for r in db.scalars(select(DebtRepayment).where(DebtRepayment.payment_id == payment_id,
                                                   DebtRepayment.debt_id.is_not(None))).all():
        r.reversed += r.applied
        r.applied = Decimal("0")


def notify_paid(db, user_id, code: str, amount) -> None:
    ns.notify(db, user_id, "debt_paid", "Balance cleared",
              f"Your payment covered the {cedis(amount)} you owed for order {code}. Thank you!", "/profile.html")


def notify_debt_created(db: Session, user_id, code: str, amount, food, delivery) -> None:
    ns.notify(db, user_id, "debt_created", "You have an outstanding balance",
              f"Order {code} could not be delivered, so you owe KORA EATS {cedis(amount)} (food {cedis(food)} + delivery {cedis(delivery)}). "
              "It will be added to your next online payment. Pay on delivery is paused until it is settled.",
              "/profile.html")


# ------------------------------------------------------------------ reliability
# Customer-fault undelivered orders. Tracked on the ORDER (not the debt) so prepaid no-shows count too.
FAULT_REASONS = ("COD_REFUSED", "NO_SHOW")
FAULT_OUTCOMES = ("DEBT", "FOOD_REFUND")


def no_show_count(db: Session, user_id) -> int:
    """How many settled orders came back because the customer didn't take delivery (COD or prepaid)."""
    return db.scalar(select(func.count()).select_from(Order).where(
        Order.customer_id == user_id, Order.return_reason.in_(FAULT_REASONS),
        Order.return_outcome.in_(FAULT_OUTCOMES))) or 0


def record_no_show(db: Session, order: Order, background=None) -> int:
    """Count one customer-fault undelivered order. COD customers lose pay-on-delivery for escalating
    periods once they pass cod_max_failures; everyone gets warned, and repeat offenders are flagged."""
    n = no_show_count(db, order.customer_id)
    pref = db.get(CustomerPreference, order.customer_id)
    if pref is None:
        pref = CustomerPreference(user_id=order.customer_id)
        db.add(pref)
        db.flush()
    s = get_settings()
    if order.payment_mode == "COD":
        pref.cod_failures = max(pref.cod_failures or 0, n)
        if n >= s.cod_max_failures:
            days = min(90, s.cod_block_days * (n // s.cod_max_failures))
            until = utcnow() + timedelta(days=days)
            if pref.cod_blocked_until is None or pref.cod_blocked_until < until:
                pref.cod_blocked_until = until
                ns.notify(db, order.customer_id, "cod_blocked", "Pay on delivery paused",
                          f"{n} of your orders couldn't be delivered, so pay on delivery is paused for {days} days. "
                          "You can still order and pay online.", "/payments.html", background=background)
    if n == 1:
        ns.notify(db, order.customer_id, "no_show_warning", "Order couldn't be delivered",
                  "If an order can't be delivered again, pay on delivery may be paused and your account reviewed. "
                  "Please make sure someone is available at the delivery address.", "/profile.html", background=background)
    elif n == 3:
        ns.notify(db, order.customer_id, "account_flagged", "Account under review",
                  "Several of your orders couldn't be delivered. Our team will review your account. "
                  "You can still order and pay online.", "/profile.html", background=background)
    return n


# ------------------------------------------------------------------ what checkout must know
def collectible(db: Session, user_id, lock: bool = False) -> Decimal:
    """Debt that must be collected with the next online payment. With lock=True the customer's debts are row-locked
    and any standing credit is applied first, so two checkouts can't both charge for the same debt."""
    if lock:
        settle_from_credit(db, user_id)
        _debts(db, user_id, lock=True)
    return balance(db, user_id)


# ------------------------------------------------------------------ paying a debt on its own
def initiate_payment(db: Session, user: User, method: str, idem_key: str, background=None):
    from app.services import payment_service
    amount = collectible(db, user.id, lock=True)
    if amount <= 0:
        raise api_error(409, "no_debt", "You don't owe anything right now.")
    return payment_service.initiate_debt(db, user, amount, method, idem_key, background)


def verify_payment(db: Session, payment_id: int, background=None, user: User | None = None):
    from app.services import payment_service
    p = db.get(Payment, payment_id)
    if p is None or p.purpose != PaymentPurpose.DEBT or (user is not None and p.customer_id != user.id):
        raise api_error(404, "not_found", "Payment not found.")
    return payment_service.verify_debt_payment(db, p, background)


# ------------------------------------------------------------------ admin
def admin_list(db: Session) -> list[dict]:
    rows = db.execute(select(CustomerDebt, User.email).join(User, User.id == CustomerDebt.customer_id)
                      .where(CustomerDebt.kind != "CREDIT").order_by(CustomerDebt.id.desc()).limit(300)).all()
    out = []
    for d, email in rows:
        out.append({"id": d.id, "customer_id": str(d.customer_id), "email": email, "order_id": d.order_id, "order_code": d.order_code,
                    "kind": d.kind, "reason": d.reason, "amount": float(d.amount), "food_amount": float(d.food_amount),
                    "delivery_amount": float(d.delivery_amount), "outstanding": float(outstanding_of(d)),
                    "note": d.note, "created_at": d.created_at.isoformat()})
    return out


def admin_adjust(db: Session, debt_id: int, admin_id, new_amount: Decimal, note: str) -> CustomerDebt:
    """Admin corrects a debt (e.g. the customer did pay, or the report was wrong). Never below what was already paid."""
    d = db.get(CustomerDebt, debt_id)
    if d is None or d.kind == "CREDIT":
        raise api_error(404, "not_found", "Debt not found.")
    paid = sum((r.applied - r.reversed for r in d.repayments), Decimal("0"))
    d.amount = money(max(paid, new_amount))
    d.note = ((d.note or "") + f" | admin {admin_id}: {note}")[:300]
    return d
