"""Admin: how well is the AI doing? Aggregated numbers only. Nothing here identifies a customer or shows anyone's order history."""
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.timeutil import utcnow
from app.db.session import get_db
from app.models.ai import CustomerPreference, RecommendationEvent
from app.models.catalog import ApprovalStatus, MenuItem, Vendor
from app.models.orders import Order
from app.models.riders import DeliveryRider
from app.models.user import UserRole
from app.services import dispatch_service as dsp, forecast_service as fc
from app.services.analytics_service import COUNTED
from app.services.recommend.scoring import weights

router = APIRouter(prefix="/admin/ai", tags=["admin"], dependencies=[Depends(require_roles(UserRole.ADMIN))])
SURFACES = ["feed", "afford", "craving", "assistant", "group"]


def _rate(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


@router.get("/overview")
def overview(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    now = utcnow()
    since = now - timedelta(days=days)

    # --- recommendation performance by surface (the learning loop)
    counts = {(s, k): n for s, k, n in db.execute(select(RecommendationEvent.surface, RecommendationEvent.kind, func.count())
                                                   .where(RecommendationEvent.created_at >= since).group_by(RecommendationEvent.surface, RecommendationEvent.kind))}
    surfaces = []
    for s in SURFACES:
        imp, clk, add, order = (counts.get((s, k), 0) for k in ("impression", "click", "add_to_cart", "order"))
        surfaces.append({"surface": s, "impressions": imp, "clicks": clk, "add_to_cart": add, "orders": order,
                         "click_rate": _rate(clk, imp), "add_rate": _rate(add, imp), "conversion_rate": _rate(order, imp)})
    total_imp = sum(x["impressions"] for x in surfaces)
    total_orders = sum(x["orders"] for x in surfaces)

    top = db.execute(select(MenuItem.name, Vendor.name,
                            func.count(case((RecommendationEvent.kind == "impression", 1))), func.count(case((RecommendationEvent.kind == "order", 1))))
                     .join(MenuItem, MenuItem.id == RecommendationEvent.menu_item_id).join(Vendor, Vendor.id == MenuItem.vendor_id)
                     .where(RecommendationEvent.created_at >= since).group_by(MenuItem.id, MenuItem.name, Vendor.name)
                     .order_by(func.count(case((RecommendationEvent.kind == "order", 1))).desc(), func.count(case((RecommendationEvent.kind == "impression", 1))).desc())
                     .limit(10)).all()

    # --- customer controls (are people using the privacy and safety features?)
    prefs_total = db.scalar(select(func.count()).select_from(CustomerPreference)) or 0
    privacy = {"customers_with_a_food_profile": prefs_total,
               "personalisation_switched_off": db.scalar(select(func.count()).select_from(CustomerPreference).where(CustomerPreference.personalization_enabled.is_(False))) or 0,
               "with_dietary_needs": db.scalar(select(func.count()).select_from(CustomerPreference).where(func.jsonb_array_length(CustomerPreference.dietary) > 0)) or 0,
               "avoiding_allergens": db.scalar(select(func.count()).select_from(CustomerPreference).where(func.jsonb_array_length(CustomerPreference.avoid_allergens) > 0)) or 0,
               "who_forgot_their_history": db.scalar(select(func.count()).select_from(CustomerPreference).where(CustomerPreference.memory_reset_at.is_not(None))) or 0}

    # --- forecast coverage: how many vendors have enough history for a real forecast?
    today = now.date()
    per_vendor = db.execute(select(Vendor.id, func.count(Order.id), func.min(Order.paid_at)).join(Order, (Order.vendor_id == Vendor.id) & Order.paid_at.is_not(None)
                                                                                                 & Order.status.in_(COUNTED) & (Order.paid_at >= now - timedelta(days=63)), isouter=True)
                            .where(Vendor.approval_status == ApprovalStatus.APPROVED).group_by(Vendor.id)).all()
    coverage = {"insufficient": 0, "low": 0, "medium": 0, "high": 0}
    for _, n, first in per_vendor:
        h = fc.History(today=today, first_date=first.date() if first else None, orders=n or 0)
        coverage[fc.confidence(h)["level"]] += 1

    # --- dispatch snapshot (right now)
    waiting = dsp.waiting_orders(db)
    pri = [dsp.priority(o, now) for o in waiting]
    riders = dsp.eligible_riders(db)
    dispatch = {"orders_waiting_for_a_rider": len(waiting), "urgent": sum(1 for p in pri if p["urgent"]),
                "average_wait_minutes": round(sum(p["waiting_minutes"] for p in pri) / len(pri), 1) if pri else 0,
                "riders_online": len(riders), "riders_online_with_location": sum(1 for r in riders if r.current_lat is not None),
                "riders_approved": db.scalar(select(func.count()).select_from(DeliveryRider).where(DeliveryRider.approval_status == ApprovalStatus.APPROVED)) or 0}

    return {"days": days, "totals": {"impressions": total_imp, "orders_from_recommendations": total_orders, "conversion_rate": _rate(total_orders, total_imp)},
            "surfaces": surfaces, "top_dishes": [{"dish": d, "vendor": v, "impressions": i, "orders": o} for d, v, i, o in top], "customer_controls": privacy,
            "forecast_coverage": {"approved_vendors": len(per_vendor), **coverage}, "dispatch": dispatch, "ranking_weights": weights(),
            "note": "Aggregated and anonymous. Ranking weights can be changed in Settings."}
