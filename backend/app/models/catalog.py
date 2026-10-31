import enum
import uuid
from datetime import date as date_type
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    icon: Mapped[str] = mapped_column(String(40), default="bi-egg-fried")  # Bootstrap Icons class
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BusinessType(str, enum.Enum):
    """KORA EATS is open to everyone who sells food, not just restaurants."""
    RESTAURANT = "RESTAURANT"
    CHOP_BAR = "CHOP_BAR"
    HOME_COOK = "HOME_COOK"
    BAKER = "BAKER"
    STREET_FOOD = "STREET_FOOD"
    SMALL_BUSINESS = "SMALL_BUSINESS"
    CATERER = "CATERER"


BUSINESS_LABELS = {"RESTAURANT": "Restaurant", "CHOP_BAR": "Chop bar", "HOME_COOK": "Home cook", "BAKER": "Baker",
                   "STREET_FOOD": "Street food", "SMALL_BUSINESS": "Small food business", "CATERER": "Caterer"}


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    phone: Mapped[str | None] = mapped_column(String(20))
    address_text: Mapped[str] = mapped_column(String(255), default="")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    logo_url: Mapped[str | None] = mapped_column(String(255))
    cover_url: Mapped[str | None] = mapped_column(String(255))
    rating_avg: Mapped[float] = mapped_column(Float, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_prep_minutes: Mapped[int] = mapped_column(Integer, default=20)
    delivery_radius_km: Mapped[float | None] = mapped_column(Float)   # this vendor's delivery zone; None = platform default
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)  # manual "accepting orders" switch
    cod_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # vendor opt-in for cash on delivery
    owner_name: Mapped[str | None] = mapped_column(String(100))
    business_type: Mapped[BusinessType] = mapped_column(Enum(BusinessType, name="business_type"), default=BusinessType.RESTAURANT, server_default="RESTAURANT")
    region: Mapped[str | None] = mapped_column(String(40))
    area: Mapped[str | None] = mapped_column(String(80))            # neighbourhood, e.g. East Legon
    digital_address: Mapped[str | None] = mapped_column(String(14))   # Ghana Post GPS, e.g. GA-543-4531
    contact_email: Mapped[str | None] = mapped_column(String(255))
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status"), default=ApprovalStatus.PENDING, server_default="PENDING")
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    category: Mapped[Category | None] = relationship(lazy="joined")
    items: Mapped[list["MenuItem"]] = relationship(back_populates="vendor", cascade="all, delete-orphan")
    hours: Mapped[list["VendorHour"]] = relationship(lazy="selectin", cascade="all, delete-orphan")
    documents: Mapped[list["VendorDocument"]] = relationship(cascade="all, delete-orphan")
    promotions: Mapped[list["Promotion"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class VendorHour(Base):
    """One row per weekday (0 = Monday ... 6 = Sunday). Closing before opening means 'after midnight'."""
    __tablename__ = "vendor_hours"
    __table_args__ = (UniqueConstraint("vendor_id", "weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    opens_at: Mapped[time] = mapped_column(Time, default=time(8, 0))
    closes_at: Mapped[time] = mapped_column(Time, default=time(20, 0))
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)


class VendorDocument(Base):
    """Business documents for admin review. Files are stored privately (never publicly served)."""
    __tablename__ = "vendor_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    image_url: Mapped[str | None] = mapped_column(String(255))
    prep_minutes: Mapped[int] = mapped_column(Integer, default=15)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_spicy: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[str] = mapped_column(String(200), default="")  # comma-separated: "filling,chicken,rice"
    order_count: Mapped[int] = mapped_column(Integer, default=0)  # popularity, bumped on delivery
    # Structured attributes chosen from app.services.food_taxonomy (vendors pick from lists; nothing is guessed)
    traits: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")          # filling, light, sweet, comfort, high_protein
    meal_types: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")      # breakfast, lunch, dinner, snack, drink, dessert
    dietary: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")         # vegetarian, vegan, halal, ...
    allergens: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")       # peanuts, dairy, ... (only meaningful when declared)
    dietary_declared: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # vendor confirmed the allergen info is complete
    spice_level: Mapped[int] = mapped_column(Integer, default=0, server_default="0")        # 0 none .. 3 hot
    daily_prep_qty: Mapped[int | None] = mapped_column(Integer)                              # portions the vendor plans to make per day (optional)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    vendor: Mapped[Vendor] = relationship(back_populates="items", lazy="joined")
    category: Mapped[Category | None] = relationship(lazy="joined")

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]


class Promotion(Base):
    """A time-limited percentage discount on one food item, or on the vendor's whole menu (menu_item_id NULL)."""
    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="")
    discount_percent: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WasteLog(Base):
    """A vendor's end-of-day record: how many portions were prepared and how many were left over. Powers waste-reduction advice."""
    __tablename__ = "waste_logs"
    __table_args__ = (UniqueConstraint("menu_item_id", "log_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    log_date: Mapped[date_type] = mapped_column(Date)
    prepared_qty: Mapped[int] = mapped_column(Integer)
    leftover_qty: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
