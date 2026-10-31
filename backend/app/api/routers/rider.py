"""Rider onboarding + delivery APIs (/api/rider/*)."""
import time
from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import api_error
from app.core.uploads import save_private_document
from app.db.session import get_db
from app.models.catalog import ApprovalStatus
from app.models.riders import DeliveryRider, VehicleType
from app.models.user import User, UserRole
from app.models.wallet import WalletAccount
from app.models.customer import Customer
from app.models.orders import Order, OrderStatus as S
from app.schemas.auth import MessageResponse
from app.schemas.rider import RiderApplyIn, RiderUpdateIn
from pydantic import BaseModel, Field

from app.services import audit_service, rider_service as svc, tracking_service

router = APIRouter(prefix="/rider", tags=["rider"])


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


def rider_out(r: DeliveryRider) -> dict:
    return {"id": r.id, "name": r.user.profile.full_name, "email": r.user.email, "phone": r.phone,
            "vehicle_type": r.vehicle_type.value, "plate_number": r.plate_number,
            "approval_status": r.approval_status.value, "rejection_reason": r.rejection_reason,
            "is_online": r.is_online, "has_document": bool(r.id_document), "document_name": r.id_document_name,
            "last_location_at": r.last_location_at.isoformat() if r.last_location_at else None,
            "created_at": r.created_at.isoformat()}


def owned_rider(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DeliveryRider:
    r = db.scalar(select(DeliveryRider).where(DeliveryRider.user_id == user.id))
    if r is None:
        raise api_error(404, "no_application", "You haven't applied to be a rider yet.")
    return r


def require_rider(user: User = Depends(get_current_user), r: DeliveryRider = Depends(owned_rider)) -> DeliveryRider:
    if user.role != UserRole.RIDER or r.approval_status != ApprovalStatus.APPROVED:
        raise api_error(403, "rider_not_approved", "Your rider account isn't approved yet.")
    return r


def _vehicle(v: str) -> VehicleType:
    try:
        return VehicleType(v)
    except ValueError:
        raise api_error(422, "bad_vehicle", "Choose MOTORBIKE, BICYCLE, CAR or ON_FOOT.")


# ------------------------------------------------------------------ onboarding
@router.post("/apply", status_code=201)
def apply(data: RiderApplyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != UserRole.CUSTOMER:
        raise api_error(403, "wrong_role", "Only customer accounts can apply to become a rider.")
    r = db.scalar(select(DeliveryRider).where(DeliveryRider.user_id == user.id))
    if r is None:
        r = DeliveryRider(user_id=user.id, phone=data.phone, vehicle_type=_vehicle(data.vehicle_type), plate_number=data.plate_number)
        db.add(r)
    elif r.approval_status == ApprovalStatus.REJECTED:
        r.phone, r.vehicle_type, r.plate_number = data.phone, _vehicle(data.vehicle_type), data.plate_number
        r.approval_status, r.rejection_reason = ApprovalStatus.PENDING, None
    else:
        raise api_error(409, "already_applied", "You already have a rider application.")
    audit_service.record(db, "rider_applied", user.id)
    db.commit()
    return rider_out(r)


@router.get("/application")
def application(r: DeliveryRider = Depends(owned_rider)):
    return rider_out(r)


@router.post("/id-document")
def upload_id(file: UploadFile = File(...), r: DeliveryRider = Depends(owned_rider), db: Session = Depends(get_db)):
    r.id_document, r.id_document_name = save_private_document(file)
    db.commit()
    return rider_out(r)


# ------------------------------------------------------------------ profile / status
@router.get("/me")
def me(r: DeliveryRider = Depends(require_rider)):
    return rider_out(r)


@router.put("/me")
def update_me(data: RiderUpdateIn, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    r.phone, r.vehicle_type, r.plate_number = data.phone, _vehicle(data.vehicle_type), data.plate_number
    db.commit()
    return rider_out(r)


@router.post("/online")
def set_online(is_online: bool, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    if not is_online and svc._active_count(db, r):
        raise api_error(409, "active_deliveries", "Finish your active deliveries before going offline.")
    r.is_online = is_online
    db.commit()
    return rider_out(r)


# ------------------------------------------------------------------ deliveries
@router.get("/available")
def available(r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.available(db, r) if r.is_online else []


@router.get("/deliveries")
def deliveries(view: str = "active", r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.my_deliveries(db, r, view)


@router.post("/deliveries/{order_id}/accept")
def accept(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.accept(db, r, order_id, background)


@router.post("/deliveries/{order_id}/pickup")
def pickup(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.pickup(db, r, order_id, background)


@router.post("/deliveries/{order_id}/out-for-delivery")
def out_for_delivery(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.out_for_delivery(db, r, order_id, background)


@router.post("/deliveries/{order_id}/arrived")
def arrived(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.arrived(db, r, order_id, background)


@router.post("/deliveries/{order_id}/delivered")
def delivered(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.deliver(db, r, order_id, background)


@router.post("/deliveries/{order_id}/report-cod-refusal")
def report_cod_refusal(order_id: int, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Report that a customer refused to pay for a COD order. This triggers penalties and blocks the customer from future COD orders."""
    from app.core.config import get_settings
    from app.core.timeutil import utcnow
    from datetime import timedelta
    from decimal import Decimal
    
    order = db.get(Order, order_id)
    if not order:
        raise api_error(404, "not_found", "Order not found.")
    
    if order.payment_mode != "COD":
        raise api_error(422, "not_cod", "This is not a cash-on-delivery order.")
    
    if order.status != S.OUT_FOR_DELIVERY:
        raise api_error(422, "invalid_status", "COD refusal can only be reported when order is out for delivery.")
    
    if order.cod_failed_at:
        raise api_error(409, "already_reported", "COD refusal has already been reported for this order.")
    
    # Mark order as COD failed
    order.cod_failed_at = utcnow()
    
    # Get or create customer record
    customer = db.get(Customer, order.customer_id)
    if not customer:
        customer = Customer(user_id=order.customer_id)
        db.add(customer)
    
    # Increment refusal count
    customer.cod_refusal_count += 1
    
    # Calculate penalty
    s = get_settings()
    order_total = Decimal(order.total)
    penalty_percent = Decimal(str(s.cod_penalty_percent))
    penalty_fixed = Decimal(str(s.cod_penalty_fixed))
    penalty = (order_total * penalty_percent / 100) + penalty_fixed
    
    # Add to customer penalty balance
    customer.cod_penalty_balance += float(penalty)
    
    # Block customer from COD if they've exceeded the limit
    if customer.cod_refusal_count >= s.cod_max_failures:
        block_days = s.cod_block_days
        customer.cod_blocked_until = utcnow() + timedelta(days=block_days)
    
    db.commit()
    
    audit_service.record(db, "cod_refusal_reported", r.user_id, 
                       detail=f"order={order.code} customer={order.customer_id} penalty={penalty} refusals={customer.cod_refusal_count}")
    
    return {
        "message": "COD refusal reported. Customer will be charged a penalty.",
        "penalty_amount": float(penalty),
        "customer_refusal_count": customer.cod_refusal_count,
        "customer_blocked_from_cod": customer.cod_blocked_until is not None,
        "blocked_until": customer.cod_blocked_until.isoformat() if customer.cod_blocked_until else None
    }


@router.post("/deliveries/{order_id}/release", response_model=MessageResponse)
def release(order_id: int, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    svc.release(db, r, order_id, background)
    return MessageResponse(message="Delivery released.")


@router.get("/wallet")
def rider_wallet(r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Get rider's wallet balance including cash on hand from COD."""
    from app.services.wallet_service import rider_balances
    return rider_balances(db, r.id)


@router.post("/topup/initiate")
def initiate_topup(amount: float, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Initiate a top-up payment to clear negative COD balance via Paystack."""
    from app.services import payment_service
    from app.services.wallet_service import rider_balances
    from app.core.money import CURRENCY_CODE
    from decimal import Decimal
    
    if amount <= 0:
        raise api_error(422, "invalid_amount", "Amount must be greater than 0")
    
    # Check current cash balance
    wallet = rider_balances(db, r.id)
    cash_in_hand = wallet.get("cash_in_hand", 0)
    
    # Limit top-up to cover negative balance + buffer
    max_topup = max(0, -cash_in_hand) + 500  # Allow extra 500 buffer
    if amount > max_topup:
        raise api_error(422, "amount_too_high", f"Maximum top-up is {max_topup}")
    
    reference = f"RIDER_TOPUP_{r.id}_{int(time.time())}"
    user = r.user
    
    from app.core.config import get_settings
    s = get_settings()
    base_url = s.cors_origins.split(",")[0] if s.cors_origins else "http://127.0.0.1:8000"
    callback_url = f"{base_url}/api/rider/topup/callback?reference={reference}"
    
    result = payment_service.initiate_standalone(
        db,
        user=user,
        reference=reference,
        amount=Decimal(str(amount)),
        currency=CURRENCY_CODE,
        method="MOBILE_MONEY",
        description=f"Rider top-up to clear COD balance",
        callback_url=callback_url
    )
    
    audit_service.record(db, "rider_topup_initiated", r.user_id, detail=f"amount={amount} reference={reference}")
    
    return {
        "checkout_url": result.checkout_url,
        "reference": result.provider_reference,
        "amount": amount,
    }


@router.post("/topup/verify")
def verify_topup(reference: str, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Verify a completed top-up payment and apply to rider's wallet."""
    from app.services import payment_service
    from app.services.wallet_service import balances, _add
    from app.models.wallet import LedgerKind as K
    from app.core.timeutil import utcnow
    
    # Verify payment with Paystack
    payment = payment_service.verify_payment(db, reference)
    if not payment or not payment.verified:
        raise api_error(404, "payment_not_found", "Payment not found or not verified")
    
    # Check if already processed
    if payment.metadata.get("rider_topup_processed"):
        return {"status": "already_processed", "message": "Top-up already applied"}
    
    # Get top-up amount
    amount = payment.amount
    
    # Add to rider's wallet as COD remittance
    wallet_account = db.scalar(select(WalletAccount).where(
        WalletAccount.owner_id == r.id,
        WalletAccount.owner_type == "RIDER"
    ))
    
    if not wallet_account:
        raise api_error(404, "wallet_not_found", "Rider wallet not found")
    
    # Record as COD remittance to clear negative balance
    meta = {"payment_reference": reference, "topup": True}
    _add(db, wallet_account, None, K.COD_REMITTANCE, amount, utcnow(), "Top-up payment to clear COD balance", meta)
    
    # Mark as processed
    payment.metadata["rider_topup_processed"] = True
    db.commit()
    
    audit_service.record(db, "rider_topup_verified", r.user_id, detail=f"amount={amount} reference={reference}")
    
    return {"status": "success", "amount": amount, "new_balance": balances(db, r.id)}


@router.get("/topup/callback")
def topup_callback(reference: str, r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    """Paystack redirects here after rider top-up payment. Verify and redirect to rider dashboard."""
    from app.services import payment_service
    from app.services.wallet_service import balances, _add
    from app.models.wallet import LedgerKind as K
    from app.core.timeutil import utcnow
    
    # Verify payment
    payment = payment_service.verify_payment(db, reference)
    if not payment or not payment.verified:
        return RedirectResponse(url="/rider/dashboard.html?topup=failed", status_code=303)
    
    # Check if already processed
    if payment.metadata.get("rider_topup_processed"):
        return RedirectResponse(url="/rider/dashboard.html?topup=already_processed", status_code=303)
    
    # Process top-up
    amount = payment.amount
    wallet_account = db.scalar(select(WalletAccount).where(
        WalletAccount.owner_id == r.id,
        WalletAccount.owner_type == "RIDER"
    ))
    
    if wallet_account:
        meta = {"payment_reference": reference, "topup": True}
        _add(db, wallet_account, None, K.COD_REMITTANCE, amount, utcnow(), "Top-up payment to clear COD balance", meta)
        payment.metadata["rider_topup_processed"] = True
        db.commit()
        audit_service.record(db, "rider_topup_verified", r.user_id, detail=f"amount={amount} reference={reference}")
    
    return RedirectResponse(url="/rider/dashboard.html?topup=success", status_code=303)


@router.get("/earnings")
def earnings(r: DeliveryRider = Depends(require_rider), db: Session = Depends(get_db)):
    return svc.earnings(db, r)


@router.post("/location")
def update_location(data: LocationIn, background: BackgroundTasks, r: DeliveryRider = Depends(require_rider),
                    db: Session = Depends(get_db)):
    """Called every few seconds by the rider's phone while online."""
    if not r.is_online:
        raise api_error(409, "offline", "Go online before sharing your location.")
    return {"stored": tracking_service.record_location(db, r, data.lat, data.lng, background)}
