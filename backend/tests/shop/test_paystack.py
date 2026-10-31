"""Paystack integration, tested against a fake of Paystack's documented API
(POST /transaction/initialize, GET /transaction/verify/:reference, HMAC-SHA512 webhooks)."""
import hashlib
import hmac
import json
import re
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models.orders import Order, OrderStatus as S
from app.models.payments import Payment, PaymentStatus
from app.services import payment_providers
from app.services.payment_providers import PaystackProvider, to_subunit

KEY = "sk_test_unit_test_key_123"


class FakePaystack:
    """Behaves like api.paystack.co for the two endpoints we use."""

    def __init__(self):
        self.tx: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.down = False
        self.reject_with: str | None = None

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers or {}})
        if self.down:
            raise httpx.ConnectError("boom")
        if self.reject_with:
            return httpx.Response(400, json={"status": False, "message": self.reject_with})
        if url.endswith("/transaction/initialize") and method == "POST":
            ref = json["reference"]
            self.tx[ref] = {"status": "abandoned", "amount": int(json["amount"]), "currency": json["currency"]}
            return httpx.Response(200, json={"status": True, "message": "Authorization URL created",
                                             "data": {"authorization_url": f"https://checkout.paystack.com/{ref[-8:]}", "access_code": "ac_" + ref[-6:], "reference": ref}})
        m = re.search(r"/transaction/verify/([^/]+)$", url)
        if m and method == "GET":
            t = self.tx.get(m.group(1))
            if t is None:
                return httpx.Response(404, json={"status": False, "message": "Transaction reference not found"})
            return httpx.Response(200, json={"status": True, "message": "Verification successful", "data": {**t, "reference": m.group(1)}})
        return httpx.Response(404, json={"status": False, "message": "unknown"})

    def set(self, ref, status, amount=None, currency=None):
        self.tx[ref]["status"] = status
        if amount is not None:
            self.tx[ref]["amount"] = amount
        if currency:
            self.tx[ref]["currency"] = currency


@pytest.fixture()
def ps(monkeypatch):
    fake = FakePaystack()
    s = get_settings()
    monkeypatch.setattr(s, "payment_provider", "paystack")
    monkeypatch.setattr(s, "paystack_secret_key", KEY)
    monkeypatch.setattr(payment_providers.httpx, "request", fake.request)
    return fake


def pay(c, order_id, key="key-12345678", **kw):
    return c.post(f"/api/payments/orders/{order_id}/initiate", headers={"Idempotency-Key": key}, **kw)


def verify(c, order_id):
    return c.get(f"/api/payments/orders/{order_id}/verify").json()


def signed(body: bytes, key=KEY):
    return {"x-paystack-signature": hmac.new(key.encode(), body, hashlib.sha512).hexdigest(), "content-type": "application/json"}


def event(ref, name="charge.success"):
    return json.dumps({"event": name, "data": {"reference": ref, "status": "success"}}, ensure_ascii=False).encode()


# ---------------------------------------------------------------- amounts
@pytest.mark.parametrize("amount,pesewas", [("95.00", 9500), ("0.50", 50), ("1250.50", 125050), ("7", 700), ("19.995", 2000)])
def test_cedis_are_sent_to_paystack_as_pesewas(amount, pesewas):
    assert to_subunit(Decimal(amount)) == pesewas


# ---------------------------------------------------------------- initialize
def test_initialize_request_matches_the_documented_api(shopper, place_order, ps, db):
    o = place_order()                                                   # total GH₵95.00
    r = pay(shopper, o["id"])
    assert r.status_code == 200 and r.json()["checkout_url"].startswith("https://checkout.paystack.com/")
    call = ps.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://api.paystack.co/transaction/initialize"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    body = call["json"]
    assert body["amount"] == "9500" and body["currency"] == "GHS" and body["email"] == "shopper@example.com"
    assert body["channels"] == ["mobile_money"]
    assert re.fullmatch(r"KEP-[A-Za-z0-9.=-]+", body["reference"])                   # only characters Paystack allows
    assert body["callback_url"].endswith("/payment-result.html") and "?" not in body["callback_url"]
    assert body["metadata"]["phone"] == "+233244000111"
    p = db.scalar(select(Payment))
    assert p.provider == "paystack" and p.reference == body["reference"] and p.provider_reference.startswith("ac_")
    assert KEY not in json.dumps(r.json())                                            # the secret never reaches the browser


def test_the_frontend_never_sees_the_secret_key(shopper, place_order, ps, client):
    o = place_order(); pay(shopper, o["id"])
    for path in ("/api/payments", f"/api/orders/{o['id']}", "/api/payments/methods", "/api/home"):
        assert KEY not in shopper.get(path).text
    assert all(KEY not in f.read_text(errors="ignore") for f in __import__("pathlib").Path(__file__).resolve().parents[3].joinpath("frontend").rglob("*.js"))


def test_same_idempotency_key_never_creates_a_second_paystack_transaction(shopper, place_order, ps):
    o = place_order()
    a, b = pay(shopper, o["id"]).json(), pay(shopper, o["id"]).json()
    assert a["payment_id"] == b["payment_id"] and len([c for c in ps.calls if c["method"] == "POST"]) == 1


# ---------------------------------------------------------------- verification decides, not the redirect
def test_paid_only_after_server_side_verification(shopper, place_order, ps, db):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]
    assert verify(shopper, o["id"])["status"] == "PENDING_PAYMENT"                    # "abandoned" at Paystack: still waiting
    ps.set(ref, "success")
    v = verify(shopper, o["id"])
    assert v["status"] == "PAID" and v["payment"]["status"] == "SUCCESS"
    assert any(c["url"].endswith(f"/transaction/verify/{ref}") and c["method"] == "GET" for c in ps.calls)
    assert verify(shopper, o["id"])["history"][-1]["status"] == "PAID"                # idempotent


def test_return_url_with_reference_verifies_the_customers_own_order(shopper, place_order, ps, make_user, login):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]
    ps.set(ref, "success")
    assert login(make_user("thief@example.com")).get(f"/api/payments/by-reference/{ref}/verify").status_code == 404
    got = shopper.get(f"/api/payments/by-reference/{ref}/verify").json()
    assert got["status"] == "PAID" and got["id"] == o["id"]
    assert shopper.get("/api/payments/by-reference/nope/verify").status_code == 404


@pytest.mark.parametrize("amount,currency", [(100, None), (9499, None), (9500, "USD")])
def test_wrong_amount_or_currency_is_never_accepted(shopper, place_order, ps, db, amount, currency):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]
    ps.set(ref, "success", amount=amount, currency=currency)
    v = verify(shopper, o["id"])
    assert v["status"] == "PENDING_PAYMENT" and v["payment"]["status"] == "FAILED"


@pytest.mark.parametrize("paystack_status,expected_order", [("failed", "PAYMENT_FAILED"), ("reversed", "PAYMENT_FAILED"),
                                                              ("abandoned", "PENDING_PAYMENT"), ("pending", "PENDING_PAYMENT"), ("ongoing", "PENDING_PAYMENT")])
def test_paystack_statuses_map_correctly(shopper, place_order, ps, paystack_status, expected_order):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]
    ps.set(ref, paystack_status)
    assert verify(shopper, o["id"])["status"] == expected_order


def test_failed_then_retry_then_paid(shopper, place_order, ps):
    o = place_order(); ref1 = pay(shopper, o["id"]).json()["reference"]
    ps.set(ref1, "failed"); assert verify(shopper, o["id"])["status"] == "PAYMENT_FAILED"
    ref2 = pay(shopper, o["id"], key="second-try-0001").json()["reference"]
    assert ref2 != ref1
    ps.set(ref2, "success"); assert verify(shopper, o["id"])["status"] == "PAID"


# ---------------------------------------------------------------- provider failures are safe
@pytest.mark.parametrize("mode", ["down", "rejected"])
def test_paystack_outage_or_rejection_is_a_clean_503(shopper, place_order, ps, db, caplog, mode):
    if mode == "down": ps.down = True
    else: ps.reject_with = "Invalid key"
    o = place_order()
    with caplog.at_level("ERROR"):
        r = pay(shopper, o["id"])
    assert r.status_code == 503 and r.json()["detail"]["code"] == "payment_unavailable"
    assert KEY not in r.text and KEY not in caplog.text                                # never leaked, even in logs
    assert db.scalar(select(Payment.status)) == PaymentStatus.FAILED
    assert db.get(Order, o["id"]).status == S.PENDING_PAYMENT


def test_verify_outage_keeps_the_payment_pending(shopper, place_order, ps):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]
    ps.set(ref, "success"); ps.down = True
    assert verify(shopper, o["id"])["status"] == "PENDING_PAYMENT"                     # can't confirm -> don't fulfil, try again later
    ps.down = False
    assert verify(shopper, o["id"])["status"] == "PAID"


# ---------------------------------------------------------------- webhooks
def post_hook(c, body: bytes, headers):
    return c.post("/api/payments/webhook/paystack", content=body, headers=headers)


def test_valid_webhook_triggers_verification_and_marks_paid(shopper, place_order, ps, client, db):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]
    ps.set(ref, "success")
    body = event(ref)
    assert post_hook(client, body, signed(body)).status_code == 200                  # no login needed: authenticated by signature
    db.expire_all()
    assert db.get(Order, o["id"]).status == S.PAID


def test_webhook_cannot_mark_an_unpaid_order_paid(shopper, place_order, ps, client, db):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]               # Paystack still says "abandoned"
    body = event(ref)                                                                  # ...but the webhook claims success
    assert post_hook(client, body, signed(body)).status_code == 200
    db.expire_all()
    assert db.get(Order, o["id"]).status == S.PENDING_PAYMENT                         # we asked Paystack; the body is never trusted


def test_forged_or_tampered_webhooks_are_rejected(shopper, place_order, ps, client, db):
    o = place_order(); ref = pay(shopper, o["id"]).json()["reference"]; ps.set(ref, "success")
    body = event(ref)
    assert post_hook(client, body, {"content-type": "application/json"}).status_code == 400                  # no signature
    assert post_hook(client, body, signed(body, key="sk_test_someone_elses_key")).status_code == 400          # wrong key
    assert post_hook(client, body, signed(event(ref, "charge.failed"))).status_code == 400                    # signature for a different body
    assert post_hook(client, body + b" ", signed(body)).status_code == 400                                    # one byte changed
    assert post_hook(client, b"not json", signed(b"not json")).status_code == 400
    db.expire_all()
    assert db.get(Order, o["id"]).status == S.PENDING_PAYMENT


def test_webhook_signature_covers_the_raw_bytes_including_unicode(client, ps):
    body = json.dumps({"event": "charge.success", "data": {"reference": "KEP-x", "customer": "Kofi \u20b5 \u00c9"}}, separators=(", ", ": "), ensure_ascii=False).encode()
    assert post_hook(client, body, signed(body)).status_code == 200


def test_unrelated_or_unknown_events_are_acknowledged_and_ignored(client, ps, shopper, place_order, db):
    o = place_order()
    for body in (json.dumps({"event": "transfer.success", "data": {"amount": 5}}).encode(), event("KEP-does-not-exist")):
        assert post_hook(client, body, signed(body)).status_code == 200               # 200 so Paystack doesn't keep retrying
    assert db.get(Order, o["id"]).status == S.PENDING_PAYMENT


# ---------------------------------------------------------------- Card / Bank Transfer are switchable
def test_methods_default_to_mobile_money_and_can_be_extended(shopper, place_order, ps, monkeypatch):
    assert shopper.get("/api/payments/methods").json() == [{"id": "MOBILE_MONEY", "label": "Mobile Money"}]
    o = place_order()
    assert pay(shopper, o["id"], params={"method": "CARD"}).json()["detail"]["code"] == "method_unavailable"
    monkeypatch.setattr(get_settings(), "enabled_payment_methods", ["MOBILE_MONEY", "CARD", "BANK_TRANSFER"])
    assert [m["id"] for m in shopper.get("/api/payments/methods").json()] == ["MOBILE_MONEY", "CARD", "BANK_TRANSFER"]
    assert pay(shopper, o["id"], key="card-attempt-001", params={"method": "CARD"}).status_code == 200
    assert ps.calls[-1]["json"]["channels"] == ["card"]


def test_enabled_methods_setting_is_validated():
    base = dict(database_url="postgresql+psycopg://x/y")
    assert Settings(**base, enabled_payment_methods="mobile_money, card").enabled_payment_methods == ["MOBILE_MONEY", "CARD"]
    for bad in ("", "BITCOIN", "MOBILE_MONEY,CHEQUE"):
        with pytest.raises(ValueError):
            Settings(**base, enabled_payment_methods=bad)


def test_provider_reports_unconfigured_without_a_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "paystack_secret_key", "")
    with pytest.raises(payment_providers.ProviderNotConfigured):
        PaystackProvider().verify(None, "KEP-x")
