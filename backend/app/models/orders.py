import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base
from app.models.catalog import MenuItem, Vendor


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PLACED = "PLACED"                 # pay-on-delivery order: placed and waiting for the vendor (nothing paid yet)
    PAID = "PAID"
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    RIDER_ASSIGNED = "RIDER_ASSIGNED"
    PICKED_UP = "PICKED_UP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    COLLECTED = "COLLECTED"          # pickup orders: the customer collected their food
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class FulfillmentType(str, enum.Enum):
    DELIVERY = "DELIVERY"
    PICKUP = "PICKUP"


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    vendor: Mapped[Vendor | None] = relationship(lazy="joined")
    items: Mapped[list["CartItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by="CartItem.id")


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "menu_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    special_instructions: Mapped[str | None] = mapped_column(String(200))

    item: Mapped[MenuItem] = relationship(lazy="joined")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)   # human friendly, e.g. KE-7K3M9Q
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING_PAYMENT, index=True)
    fulfillment_type: Mapped[FulfillmentType] = mapped_column(Enum(FulfillmentType, name="fulfillment_type"),
                                                              default=FulfillmentType.DELIVERY, server_default="DELIVERY")
    payment_mode: Mapped[str] = mapped_column(String(10), default="ONLINE", server_default="ONLINE")   # ONLINE (Paystack) | COD (cash on delivery)
    coupon_funded_by: Mapped[str | None] = mapped_column(String(10))       # PLATFORM or VENDOR: who pays for the coupon discount (set when the order is placed)
    discount_delivery_part: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")   # part of the discount that waived the delivery fee
    cod_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # the rider confirmed collecting the cash
    cod_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))      # the customer refused to pay at the door
    earnings_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)) # vendor/rider/platform earnings were written to the ledger
    pickup_code: Mapped[str | None] = mapped_column(String(8))     # the customer shows this when collecting (PICKUP orders)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    delivery_address: Mapped[str] = mapped_column(String(300))
    delivery_lat: Mapped[float] = mapped_column(Float)
    delivery_lng: Mapped[float] = mapped_column(Float)
    delivery_notes: Mapped[str | None] = mapped_column(String(255))
    customer_phone: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(String(300))
    cancelled_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)   # 'deliver later'
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))                  # when the vendor gets to see it

    vendor: Mapped[Vendor] = relationship(lazy="joined")
    items: Mapped[list["OrderItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan")
    history: Mapped[list["OrderStatusHistory"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="OrderStatusHistory.id")


class OrderItem(Base):
    """Snapshot of what was bought: later menu edits never change past orders."""
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_items.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    special_instructions: Mapped[str | None] = mapped_column(String(200))


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[OrderStatus | None] = mapped_column(Enum(OrderStatus, name="order_status"))
    to_status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus, name="order_status"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_role: Mapped[str] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
