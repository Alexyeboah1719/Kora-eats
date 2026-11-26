"""Customer debts: money a customer owes KORA EATS for orders that were never delivered.

Customers have no wallet. A debt is created when a rider brings food back because the customer refused
cash on delivery or was not there. The system has already paid the vendor and the rider, so the debt is
owed to the PLATFORM only. Outstanding balance is always a SUM over these rows (like the vendor/rider
ledger), never a stored number that could drift."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base


class CustomerDebt(Base):
    __tablename__ = "customer_debts"
    __table_args__ = (UniqueConstraint("order_id", "kind", name="uq_debt_once_per_order"),)   # one debt per order per kind, ever

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    order_code: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(20))          # COD_REFUSED | PREPAID_NO_SHOW | ADJUSTMENT
    reason: Mapped[str] = mapped_column(String(40), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))          # total still conceptually owed when created
    food_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)      # the food part (what the customer ordered)
    delivery_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # the delivery-fee part
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    repayments: Mapped[list["DebtRepayment"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                            order_by="DebtRepayment.id")


class DebtRepayment(Base):
    """Money the customer paid towards a debt. `applied` counts towards the balance; `reversed` gives it back
    (a refund of that payment). A payment is APPLIED only once the provider has confirmed it."""
    __tablename__ = "debt_repayments"

    id: Mapped[int] = mapped_column(primary_key=True)
    debt_id: Mapped[int] = mapped_column(ForeignKey("customer_debts.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    applied: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    reversed: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
