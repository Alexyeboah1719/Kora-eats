import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base
from app.models.catalog import ApprovalStatus
from app.models.user import User


class VehicleType(str, enum.Enum):
    MOTORBIKE = "MOTORBIKE"
    BICYCLE = "BICYCLE"
    CAR = "CAR"
    ON_FOOT = "ON_FOOT"


class DeliveryRider(Base):
    __tablename__ = "delivery_riders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    phone: Mapped[str] = mapped_column(String(20))
    vehicle_type: Mapped[VehicleType] = mapped_column(Enum(VehicleType, name="vehicle_type"))
    plate_number: Mapped[str | None] = mapped_column(String(20))
    id_document: Mapped[str | None] = mapped_column(String(80))       # private file name (ID card / licence)
    id_document_name: Mapped[str | None] = mapped_column(String(255))
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status", create_type=False), default=ApprovalStatus.PENDING)
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    current_lat: Mapped[float | None] = mapped_column(Float)
    current_lng: Mapped[float | None] = mapped_column(Float)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


class Delivery(Base):
    """One row per order that a rider has taken. Exactly one rider per order (unique order_id)."""
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("delivery_riders.id"), index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    out_for_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nearby_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rider_earning: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)

    rider: Mapped[DeliveryRider] = relationship(lazy="joined")


class DeliveryTracking(Base):
    """A breadcrumb of the rider's position during a delivery (for the live map and disputes)."""
    __tablename__ = "delivery_tracking"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id", ondelete="CASCADE"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
