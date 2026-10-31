import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class AiConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiMessage(Base):
    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))               # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    item_ids: Mapped[list | None] = mapped_column(JSON)         # menu items the assistant showed (all from the database)
    filters: Mapped[list | None] = mapped_column(JSON)          # what the parser understood
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CustomerPreference(Base):
    """What we've learned from a customer's own behaviour. Used only to personalise recommendations."""
    __tablename__ = "customer_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    spicy_preference: Mapped[bool | None] = mapped_column(Boolean)
    avg_budget: Mapped[float | None] = mapped_column(Float)
    budget_samples: Mapped[int] = mapped_column(Integer, default=0)
    top_categories: Mapped[list | None] = mapped_column(JSON)
    # What the customer TOLD us (explicit food profile)
    dietary: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")           # needs, e.g. vegetarian, halal
    avoid_allergens: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")   # e.g. peanuts
    spice_preference: Mapped[str | None] = mapped_column(String(10))                          # none | mild | medium | hot
    # Privacy controls for what we LEARN from their orders
    cod_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")           # times this customer refused to pay on delivery
    cod_blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    personalization_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    memory_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))        # orders before this are forgotten
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RecommendationEvent(Base):
    """The learning loop: what we recommended, and what the customer did with it. Used to measure and improve the engine.
    Only stored for signed-in customers who haven't switched personalisation off; deleted with 'forget my history'."""
    __tablename__ = "recommendation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    surface: Mapped[str] = mapped_column(String(20), index=True)          # feed | afford | craving | assistant | group
    kind: Mapped[str] = mapped_column(String(12), index=True)             # impression | click | add_to_cart | order
    menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_items.id", ondelete="SET NULL"), index=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
