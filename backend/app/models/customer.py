import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class Favorite(Base):
    """A customer's saved vendor OR menu item (exactly one of the two is set)."""
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "vendor_id"), UniqueConstraint("user_id", "menu_item_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"))
    menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(40))
    address_line: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(80), default="Accra")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    region: Mapped[str | None] = mapped_column(String(40))
    area: Mapped[str | None] = mapped_column(String(80))
    digital_address: Mapped[str | None] = mapped_column(String(14))
    delivery_notes: Mapped[str | None] = mapped_column(String(255))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Customer(Base):
    __tablename__ = "customers"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    cod_refusal_count: Mapped[int] = mapped_column(default=0)  # Number of times customer refused to pay COD
    cod_blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # When COD block expires
    cod_penalty_balance: Mapped[float] = mapped_column(default=0.0)  # Outstanding penalty balance
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
