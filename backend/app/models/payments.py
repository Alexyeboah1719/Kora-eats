import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PaymentPurpose(str, enum.Enum):
    ORDER = "ORDER"        # pays an order (order_id set); may also carry a debt_amount portion
    DEBT = "DEBT"          # pays customer debts only (order_id NULL)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)   # set for every payment (debt payments have no order)
    purpose: Mapped[PaymentPurpose] = mapped_column(Enum(PaymentPurpose, name="payment_purpose"), default=PaymentPurpose.ORDER,
                                                     server_default="ORDER")
    debt_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")   # part of `amount` that repays customer debt
    provider: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str] = mapped_column(String(60), unique=True, index=True)    # OUR reference, sent to the provider
    provider_reference: Mapped[str | None] = mapped_column(String(120))            # the provider's own id, if different
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)         # "<customer_id>:<client key>"
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="GHS")
    method: Mapped[str] = mapped_column(String(30), default="MOBILE_MONEY")
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.PENDING, index=True)
    checkout_url: Mapped[str | None] = mapped_column(String(500))
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # Soft delete for data retention


class DevProviderSession(Base):
    """DEVELOPMENT ONLY. Stands in for the payment provider's own records so that backend
    verification can be exercised end to end without a real provider. Never used in production."""
    __tablename__ = "dev_provider_sessions"

    reference: Mapped[str] = mapped_column(String(60), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10), default="PENDING")    # PENDING | SUCCESS | FAILED
    return_url: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
