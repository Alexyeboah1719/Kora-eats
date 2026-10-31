import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base
from app.models.catalog import MenuItem, Vendor


class GroupStatus(str, enum.Enum):
    OPEN = "OPEN"
    CHECKED_OUT = "CHECKED_OUT"
    CLOSED = "CLOSED"


class GroupOrder(Base):
    __tablename__ = "group_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)      # share link: /group/ABC123
    organizer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[GroupStatus] = mapped_column(Enum(GroupStatus, name="group_status"), default=GroupStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    vendor: Mapped[Vendor] = relationship(lazy="joined")
    members: Mapped[list["GroupOrderMember"]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by="GroupOrderMember.id")
    items: Mapped[list["GroupOrderItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by="GroupOrderItem.id")
    preferences: Mapped[list["GroupOrderPreference"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class GroupOrderMember(Base):
    __tablename__ = "group_order_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("group_orders.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(100))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupOrderItem(Base):
    __tablename__ = "group_order_items"
    __table_args__ = (UniqueConstraint("group_id", "user_id", "menu_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("group_orders.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    special_instructions: Mapped[str | None] = mapped_column(String(200))

    item: Mapped[MenuItem] = relationship(lazy="joined")


class GroupOrderPreference(Base):
    """What one member wants from the group meal. Only the member can read their own; the AI uses all of them to find a plan."""
    __tablename__ = "group_order_preferences"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("group_orders.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    cravings: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    terms: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")            # food words, e.g. ["chicken", "jollof"]
    max_price: Mapped[float | None] = mapped_column(Numeric(10, 2))                           # most they want to pay for their own dish
    dietary: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    avoid_allergens: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    no_spice: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
