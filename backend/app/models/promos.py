import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class CouponType(str, enum.Enum):
    PERCENT = "PERCENT"
    FIXED = "FIXED"
    FREE_DELIVERY = "FREE_DELIVERY"


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)       # stored UPPERCASE
    description: Mapped[str] = mapped_column(String(200), default="")
    type: Mapped[CouponType] = mapped_column(Enum(CouponType, name="coupon_type"))
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)            # % for PERCENT, GHS for FIXED
    min_order: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    max_discount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_limit: Mapped[int | None] = mapped_column(Integer)                     # total redemptions, None = unlimited
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1)
    first_order_only: Mapped[bool] = mapped_column(Boolean, default=False)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)  # None = platform-wide
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CouponUsage(Base):
    __tablename__ = "coupon_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupons.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
