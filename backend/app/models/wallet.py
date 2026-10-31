"""Wallets: what vendors and riders have earned, what they may withdraw, and where it was sent.

The LEDGER is the single source of truth: every cedi that moves is one signed row. A balance is always a SUM over rows, never a stored number
that could drift. Rows are never edited; mistakes are fixed with a reversing row."""
import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class WalletOwner(str, enum.Enum):
    VENDOR = "VENDOR"
    RIDER = "RIDER"
    PLATFORM = "PLATFORM"          # the owners' revenue


class LedgerKind(str, enum.Enum):
    ORDER_SALE = "ORDER_SALE"                    # vendor: food sales (+)
    ORDER_COMMISSION = "ORDER_COMMISSION"        # vendor: platform commission (-)   |  platform: commission earned (+)
    DISCOUNT_FUNDING = "DISCOUNT_FUNDING"        # vendor: a coupon discount the vendor agreed to fund (-)
    DELIVERY_PAY = "DELIVERY_PAY"                # rider: the delivery fee (+)
    DELIVERY_COMMISSION = "DELIVERY_COMMISSION"  # rider: platform deduction (-)     |  platform: earned (+)
    PLATFORM_DISCOUNT = "PLATFORM_DISCOUNT"      # platform: cost of platform-funded coupons (-)
    COD_CASH_COLLECTED = "COD_CASH_COLLECTED"    # rider: cash taken from a customer, owed to the platform (-)
    COD_REMITTANCE = "COD_REMITTANCE"            # rider: cash handed over to the platform (+)
    COD_REFUSED_LOSS = "COD_REFUSED_LOSS"        # platform: paid vendor and rider although the customer refused to pay (-)
    WITHDRAWAL = "WITHDRAWAL"                    # money sent to a MoMo number or bank (-)
    WITHDRAWAL_FEE = "WITHDRAWAL_FEE"            # platform: fee charged for a withdrawal (+)
    WITHDRAWAL_REVERSAL = "WITHDRAWAL_REVERSAL"  # a failed withdrawal returns to the wallet (+)
    REFUND_REVERSAL = "REFUND_REVERSAL"          # an order was refunded: its earnings are taken back
    ADJUSTMENT = "ADJUSTMENT"                    # admin correction (always with a reason)


class WalletAccount(Base):
    __tablename__ = "wallet_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[WalletOwner] = mapped_column(Enum(WalletOwner, name="wallet_owner"))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), unique=True)
    rider_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_riders.id", ondelete="CASCADE"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (UniqueConstraint("account_id", "order_id", "kind", name="uq_ledger_once_per_order"),)   # an order can never be paid out twice

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("wallet_accounts.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    kind: Mapped[LedgerKind] = mapped_column(Enum(LedgerKind, name="ledger_kind"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))                       # signed: + adds to the wallet, - takes away
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)   # before this it is PENDING (held)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    withdrawal_id: Mapped[int | None] = mapped_column(ForeignKey("withdrawals.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(String(200), default="")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # the rates and base used, so history never changes when rates do


class PayoutAccountType(str, enum.Enum):
    MOBILE_MONEY = "MOBILE_MONEY"
    BANK = "BANK"


class PayoutAccount(Base):
    """Where a vendor or rider wants their money sent."""
    __tablename__ = "payout_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("wallet_accounts.id", ondelete="CASCADE"), index=True)
    type: Mapped[PayoutAccountType] = mapped_column(Enum(PayoutAccountType, name="payout_account_type"))
    provider_code: Mapped[str] = mapped_column(String(20))        # MTN | VOD | ATL for Mobile Money, or Paystack's bank code
    provider_name: Mapped[str] = mapped_column(String(80), default="")
    account_number: Mapped[str] = mapped_column(String(32))
    account_name: Mapped[str] = mapped_column(String(100))
    recipient_code: Mapped[str | None] = mapped_column(String(40))   # Paystack's transfer recipient (RCP_...)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WithdrawalStatus(str, enum.Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"   # large: waiting for an admin
    PROCESSING = "PROCESSING"               # sent to Paystack, waiting for the result
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"                       # money returned to the wallet
    REVERSED = "REVERSED"                   # sent, then bounced back: returned to the wallet
    REJECTED = "REJECTED"                   # an admin said no: returned to the wallet


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("wallet_accounts.id", ondelete="CASCADE"), index=True)
    payout_account_id: Mapped[int] = mapped_column(ForeignKey("payout_accounts.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))        # what leaves the wallet
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)  # of which this much is our fee; the transfer is amount - fee
    status: Mapped[WithdrawalStatus] = mapped_column(Enum(WithdrawalStatus, name="withdrawal_status"), index=True)
    reference: Mapped[str] = mapped_column(String(60), unique=True)   # our reference, also Paystack's transfer reference
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    transfer_code: Mapped[str | None] = mapped_column(String(40))
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WalletPayment(Base):
    """A rider paying the platform the cash they collected on pay-on-delivery orders."""
    __tablename__ = "wallet_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("delivery_riders.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reference: Mapped[str] = mapped_column(String(60), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")     # PENDING | SUCCESS | FAILED
    checkout_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
