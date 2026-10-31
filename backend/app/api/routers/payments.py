import html
import re

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Header, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.errors import api_error
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.payments import DevProviderSession
from app.models.user import User
from app.models.orders import Order
from app.models.payments import Payment
from app.services import order_service, payment_service
from app.services.payment_providers import METHOD_LABELS, enabled_methods

router = APIRouter(prefix="/payments", tags=["payments"])
KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


@router.post("/orders/{order_id}/initiate", dependencies=[Depends(rate_limit("pay_init", 20, 300))])
def initiate(order_id: int, background: BackgroundTasks, method: str | None = None,
             idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Creates (or returns the existing) payment and its hosted checkout URL. The Idempotency-Key header
    makes retries safe: the same key can never create a second payment."""
    if not idempotency_key or not KEY_RE.match(idempotency_key):
        raise api_error(400, "idempotency_key_required", "Send an Idempotency-Key header (8-64 letters, digits, - or _).")
    p = payment_service.initiate(db, user, order_id, idempotency_key, method or enabled_methods()[0], background)
    return {"payment_id": p.id, "status": p.status.value, "checkout_url": p.checkout_url, "reference": p.reference}


@router.get("/orders/{order_id}/verify", dependencies=[Depends(rate_limit("pay_verify", 60, 300))])
def verify(order_id: int, background: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The payment-result page calls this. The server asks the provider; the redirect itself proves nothing."""
    order = payment_service.verify_order(db, order_id, background, user)
    return order_service.order_out(db, order)


@router.get("/methods")
def methods():
    """Payment methods the customer can choose (Mobile Money first; Card and Bank Transfer when enabled)."""
    return [{"id": m, "label": METHOD_LABELS[m]} for m in enabled_methods()]


@router.get("/by-reference/{reference}/verify", dependencies=[Depends(rate_limit("pay_verify", 60, 300))])
def verify_by_reference(reference: str, background: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Paystack sends the customer back with ?reference=...: resolve it to the customer's own order, then verify."""
    row = db.scalar(select(Payment).join(Order, Order.id == Payment.order_id).where(Payment.reference == reference, Order.customer_id == user.id))
    if row is None:
        raise api_error(404, "not_found", "Payment not found.")
    return order_service.order_out(db, payment_service.verify_order(db, row.order_id, background, user))


@router.get("")
def payment_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return payment_service.history(db, user)


@router.post("/webhook/{provider}")
async def webhook(provider: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.body()
    payment_service.handle_webhook(db, provider, dict(request.headers), body, background)
    return {"received": True}


# ------------------------------------------------------------------ development simulator pages
def _dev_only():
    s = get_settings()
    if s.payment_provider != "dev" or s.environment == "production":
        raise api_error(404, "not_found", "Not found.")


@router.get("/dev/checkout/{reference}", response_class=HTMLResponse, include_in_schema=False)
def dev_checkout(reference: str, db: Session = Depends(get_db)):
    _dev_only()
    s = db.scalar(select(DevProviderSession).where(DevProviderSession.reference == reference))
    if s is None:
        raise api_error(404, "not_found", "Unknown payment.")
    ref, amt = html.escape(reference), f"{s.currency} {s.amount:.2f}"
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payment simulator</title><style>body{{font-family:system-ui;background:#f5f7f4;display:grid;place-items:center;min-height:100vh;margin:0}}
.c{{background:#fff;border:1px solid #dde4df;border-radius:14px;padding:1.5rem;max-width:380px;width:92%}}
.w{{background:#fff4d6;color:#7a5900;border-radius:8px;padding:.5rem .75rem;font-size:.85rem;margin-bottom:1rem}}
button{{width:100%;padding:.8rem;border:0;border-radius:10px;font-weight:700;font-size:1rem;margin-top:.5rem;cursor:pointer}}
.ok{{background:#1b7a46;color:#fff}}.no{{background:#eee}}</style></head><body><div class="c">
<div class="w"><strong>DEVELOPMENT SIMULATOR.</strong> No real money moves. Production uses Paystack.</div>
<h2 style="margin:.2rem 0">Payment</h2><p>Pay <strong>{html.escape(amt)}</strong><br><small>Ref {ref}</small></p>
<form method="post" action="/api/payments/dev/complete/{ref}"><button class="ok" name="outcome" value="success">Approve payment</button>
<button class="no" name="outcome" value="fail">Decline / cancel</button></form></div></body></html>""")


@router.post("/dev/complete/{reference}", include_in_schema=False)
def dev_complete(reference: str, outcome: str = Form(...), db: Session = Depends(get_db)):
    _dev_only()
    s = db.scalar(select(DevProviderSession).where(DevProviderSession.reference == reference))
    if s is None:
        raise api_error(404, "not_found", "Unknown payment.")
    if s.status == "PENDING":
        s.status = "SUCCESS" if outcome == "success" else "FAILED"
        db.commit()
    return RedirectResponse(s.return_url, status_code=303)
