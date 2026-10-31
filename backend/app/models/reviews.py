import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base
from app.models.user import User


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)   # one review per order
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    food_rating: Mapped[int] = mapped_column(Integer)
    vendor_rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(255))
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)      # moderation
    sentiment: Mapped[str | None] = mapped_column(String(10))            # POSITIVE | NEUTRAL | NEGATIVE (Phase 10)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    aspects: Mapped[dict | None] = mapped_column(JSON)                   # {"delivery": "NEGATIVE", ...} (Phase 10)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    customer: Mapped[User] = relationship(lazy="joined")
