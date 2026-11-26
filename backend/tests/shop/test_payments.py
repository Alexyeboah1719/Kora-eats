import pytest
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.models.audit import AuditLog
from app.models.orders import Order, OrderStatus as S
from app.models.payments import DevProviderSession, Payment, PaymentStatus


def pay(c, order_id, key="key-12345678", **kw):
    return c.post(f"/api/payments/orders/{order_id}/initiate", headers={"Idempotency-Key": key}, **kw)


def simulate(c, reference, outcome="success"):
    """What the customer does on the provider's page (the dev simulator)."""
    return c.post(f"/api/payments/dev/complete/{reference}", data={"outcome": outcome}, follow_redirects=False)


def status(db, order_id):
    db.expire_all()
    return db.get(Order, order_id).status


def test_initiate_needs_idempotency_key(shopper, place_order):
    o = place_order()
    assert shopper.post(f"/api/payments/orders/{o['id']}/initiate").status_code == 400
    assert pay(shopper, o["id"], key="short").status_code == 400
    r = pay(shopper, o["id"])
    assert r.status_code == 200 and r.json()["checkout_url"].startswith("/api/payments/dev/checkout/")


def test_same_key_never_creates_second_payment(shopper, place_order, db):
    o = place_order()
    ids = {pay(shopper, o["id"]).json()["payment_id"] for _ in range(3)}
    assert len(ids) == 1
    assert pay(shopper, o["id"], key="another-key-9999").json()["payment_id"] in ids   # fresh pending attempt is reused
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    o2 = place_order()
    assert pay(shopper, o2["id"]).status_code == 409                                    # key already used for order 1


def test_redirect_alone_is_not_proof_of_payment(shopper, place_order, db):
    o = place_order()
    ref = pay(shopper, o["id"]).json()["reference"]
    simulate(shopper, ref)                                    # customer "paid" on the provider page and was redirected back
    assert status(db, o["id"]) == S.PENDING_PAYMENT           # ...but WE haven't confirmed anything yet
    v = shopper.get(f"/api/payments/orders/{o['id']}/verify").json()      # backend asks the provider
    assert v["status"] == "PAID" and v["payment"]["status"] == "SUCCESS"
    assert [h["status"] for h in v["history"]] == ["PENDING_PAYMENT", "PAID"]
    again = shopper.get(f"/api/payments/orders/{o['id']}/verify").json()  # idempotent
    assert again["status"] == "PAID" and len(again["history"]) == 2


def test_verify_before_payment_stays_pending(shopper, place_order):
    o = place_order()
    pay(shopper, o["id"])
    assert shopper.get(f"/api/payments/orders/{o['id']}/verify").json()["status"] == "PENDING_PAYMENT"


def test_declined_then_retry_then_success(shopper, place_order, db):
    o = place_order()
    simulate(shopper, pay(shopper, o["id"]).json()["reference"], "fail")
    assert shopper.get(f"/api/payments/orders/{o['id']}/verify").json()["status"] == "PAYMENT_FAILED"
    retry = pay(shopper, o["id"], key="retry-key-0001").json()
    assert status(db, o["id"]) == S.PENDING_PAYMENT and retry["status"] == "PENDING"
    simulate(shopper, retry["reference"])
    assert shopper.get(f"/api/payments/orders/{o['id']}/verify").json()["status"] == "PAID"
    assert pay(shopper, o["id"], key="retry-key-0002").status_code == 409                  # already paid


def test_amount_mismatch_never_marks_paid(shopper, place_order, db):
    o = place_order()
    ref = pay(shopper, o["id"]).json()["reference"]
    sess = db.get(DevProviderSession, ref)
    sess.amount = sess.amount - 10                              # provider reports less money than we asked for
    db.commit()
    simulate(shopper, ref)
    v = shopper.get(f"/api/payments/orders/{o['id']}/verify").json()
    assert v["status"] == "PENDING_PAYMENT" and v["payment"]["status"] == "FAILED"
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "payment_amount_mismatch")) is not None


def test_late_payment_on_cancelled_order_is_flagged_not_lost(shopper, place_order, db):
    o = place_order()
    ref = pay(shopper, o["id"]).json()["reference"]
    shopper.post(f"/api/orders/{o['id']}/cancel", json={})
    simulate(shopper, ref)
    v = shopper.get(f"/api/payments/orders/{o['id']}/verify").json()
    assert v["status"] == "CANCELLED" and v["payment"]["status"] == "SUCCESS"
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "payment_for_closed_order")) is not None


def test_only_the_owner_can_pay_or_verify(shopper, place_order, make_user, login):
    o = place_order()
    other = login(make_user("thief@example.com"))
    assert pay(other, o["id"]).status_code == 404
    assert other.get(f"/api/payments/orders/{o['id']}/verify").status_code == 404
    assert other.get("/api/payments").json() == []


def test_unknown_and_disabled_methods(shopper, place_order):
    o = place_order()
    assert pay(shopper, o["id"], params={"method": "CARD"}).json()["detail"]["code"] == "method_unavailable"
    assert pay(shopper, o["id"], params={"method": "BITCOIN"}).status_code == 422


def test_paystack_without_a_key_fails_safely(shopper, place_order, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "payment_provider", "paystack")
    monkeypatch.setattr(get_settings(), "paystack_secret_key", "")
    o = place_order()
    r = pay(shopper, o["id"])
    assert r.status_code == 503 and r.json()["detail"]["code"] == "payment_unavailable"
    assert db.scalar(select(Payment.status)) == PaymentStatus.FAILED
    assert status(db, o["id"]) == S.PENDING_PAYMENT
    assert shopper.post("/api/payments/webhook/paystack", content=b"{}").status_code == 400   # can't authenticate -> rejected
    assert shopper.get("/api/payments/dev/checkout/anything").status_code == 404              # simulator is off


def test_dev_provider_refused_in_production():
    with pytest.raises(ValueError, match="simulator"):
        Settings(database_url="postgresql+psycopg://x/y", environment="production", secret_key="k" * 40,
                 payment_provider="dev")


def test_payment_history(shopper, place_order):
    o = place_order()
    simulate(shopper, pay(shopper, o["id"]).json()["reference"])
    shopper.get(f"/api/payments/orders/{o['id']}/verify")
    h = shopper.get("/api/payments").json()
    assert len(h) == 1 and h[0]["order_code"] == o["code"] and h[0]["status"] == "SUCCESS" and h[0]["amount"] == 95.0
