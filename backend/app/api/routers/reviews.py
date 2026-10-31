from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routers.vendor import require_vendor
from app.core.errors import api_error
from app.core.uploads import save_public_image
from app.db.session import get_db
from app.models.catalog import Vendor
from app.models.reviews import Review
from app.models.user import User
from app.services import catalog_service, review_service as svc

router = APIRouter(tags=["reviews"])


class ReviewIn(BaseModel):
    order_id: int
    food_rating: int = Field(ge=1, le=5)
    vendor_rating: int = Field(ge=1, le=5)
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


@router.post("/reviews", status_code=201)
def create_review(data: ReviewIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.review_out(svc.create(db, user, data.order_id, data.food_rating, data.vendor_rating, data.comment))


@router.post("/reviews/{review_id}/image")
def review_image(review_id: int, file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.scalar(select(Review).where(Review.id == review_id, Review.customer_id == user.id))
    if r is None:
        raise api_error(404, "not_found", "Review not found.")
    r.image_url = save_public_image(file)
    db.commit()
    return svc.review_out(r)


@router.get("/vendors/{vendor_id}/reviews")
def vendor_reviews(vendor_id: int, db: Session = Depends(get_db)):
    if db.scalar(catalog_service.public_vendors_query().where(Vendor.id == vendor_id)) is None:
        raise api_error(404, "not_found", "Vendor not found.")
    rows = db.scalars(select(Review).where(Review.vendor_id == vendor_id, Review.is_hidden.is_(False))
                      .order_by(Review.id.desc()).limit(50)).unique()
    return [svc.review_out(r) for r in rows]


@router.get("/vendor/reviews")
def my_reviews(v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    rows = db.scalars(select(Review).where(Review.vendor_id == v.id, Review.is_hidden.is_(False))
                      .order_by(Review.id.desc()).limit(100)).unique().all()
    dist = dict(db.execute(select(Review.vendor_rating, func.count()).where(Review.vendor_id == v.id, Review.is_hidden.is_(False))
                           .group_by(Review.vendor_rating)).all())
    return {"average": round(v.rating_avg, 2), "count": v.rating_count,
            "distribution": {str(n): int(dist.get(n, 0)) for n in range(1, 6)}, "reviews": [svc.review_out(r) for r in rows]}


@router.get("/vendor/reviews/insights")
def insights(v: Vendor = Depends(require_vendor), db: Session = Depends(get_db)):
    """What customers praise or complain about, from the rule-based review analysis."""
    rows = db.scalars(select(Review).where(Review.vendor_id == v.id, Review.is_hidden.is_(False))).unique().all()
    sentiment = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
    aspects: dict[str, dict[str, int]] = {}
    for r in rows:
        if r.sentiment:
            sentiment[r.sentiment] += 1
        for a, pol in (r.aspects or {}).items():
            aspects.setdefault(a, {"POSITIVE": 0, "NEGATIVE": 0, "MIXED": 0})[pol] += 1
    issues = sorted(((a, c["NEGATIVE"]) for a, c in aspects.items() if c["NEGATIVE"]), key=lambda x: -x[1])
    return {"method": "rule-based keyword analysis", "reviews_analysed": len(rows), "sentiment": sentiment,
            "aspects": aspects, "top_issues": [{"aspect": a, "mentions": n} for a, n in issues]}
