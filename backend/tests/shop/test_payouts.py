import hashlib
import hmac
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.audit import AuditLog
from app.models.user import UserRole
from app.models.wallet import LedgerEntry, LedgerKind as K, PayoutAccount, Withdrawal, WithdrawalStatus as W
from app.services import payment_providers, payout_service as ps, wallet_service as ws
from tests.shop.test_notifications import titles
from tests.shop.test_wallet import deliver

D = Decimal
KEY = "sk_test_payout_unit_test_key"


class FakePaystack:
    """Behaves like api.paystack.co for recipients and transfers."""

    def __init__(self):
        self.calls, self.transfers, self.recipients = [], {}, {}
        self.transfer_status, self.reject, self.down, self.verify_down, self.otp = "pending", None, False, False, False

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers or {}})
        path = url.split("api.paystack.co")[1]
        if method == "POST" and path == "/transferrecipient":
            code = f"RCP_{len(self.recipients) + 1:04d}"
            self.recipients[code] = json
            return httpx.Response(200, json={"status": True, "data": {"recipient_code": code}})
        if method == "POST" and path == "/transfer":
            if self.down:
                raise httpx.ConnectError("network down")
            if self.reject:
                return httpx.Response(400, json={"status": False, "message": self.reject})
            self.transfers[json["reference"]] = {"status": self.transfer_status, "amount": json["amount"], "recipient": json["recipient"]}
            st = "otp" if self.otp else self.transfer_status
            return httpx.Response(200, json={"status": True, "data": {"status": st, "transfer_code": "TRF_" + json["reference"][-6:], "reference": json["reference"]}})
        if method == "GET" and path.startswith("/transfer/verify/"):
            if self.verify_down:
                raise httpx.ConnectError("network down")
            t = self.transfers.get(path.rsplit("/", 1)[1])
            if t is None:
                return httpx.Response(404, json={"status": False, "message": "Transfer not found"})
            return httpx.Response(200, json={"status": True, "data": {"status": t["status"], "transfer_code": "TRF_x"}})
        if method == "GET" and path.startswith("/bank"):
            return httpx.Response(200, json={"status": True, "data": [{"name": "GCB Bank", "code": "040", "type": "ghipss"}, {"name": "MTN", "code": "MTN", "type": "mobile_money"}]})
        return httpx.Response(404, json={"status": False, "message": "unknown"})


@pytest.fixture()
def ps_(monkeypatch):
    fake = FakePaystack()
    s = get_settings()
    monkeypatch.setattr(s, "payment_provider", "paystack")
    monkeypatch.setattr(s, "paystack_secret_key", KEY)
    monkeypatch.setattr(s, "payout_account_cooldown_hours", 0)
    monkeypatch.setattr(payment_providers.httpx, "request", fake.request)
    return fake


@pytest.fixture()
def me(world, vendor_user, login, db):
    c = login(vendor_user)
    acct = ws.vendor_account(db, world.near.id)
    db.commit()                                   # commit, or the API (a separate session) would wait forever for this uncommitted row
    return SimpleNamespace(c=c, acct=acct, user=vendor_user, w=world)


def credit(db, acct, amount, hours_ago=48):
    """hours_ago > 0: already withdrawable.  hours_ago < 0: still held, becomes available that many hours from now."""
    db.add(LedgerEntry(account_id=acct.id, kind=K.ADJUSTMENT, amount=D(str(amount)), available_at=utcnow() - timedelta(hours=hours_ago), note="test credit")); db.commit()


MOMO = {"type": "MOBILE_MONEY", "provider_code": "MTN", "account_number": "024 123 4567", "account_name": "Efua  Owusu"}


def setup_payout(me, **kw):
    r = me.c.put("/api/vendor/wallet/payout-account", json={**MOMO, **kw})
    assert r.status_code == 200, r.text
    return r.json()


_k = {"n": 0}


def withdraw(c, amount, key=None, prefix="/api/vendor"):
    _k["n"] += 1
    return c.post(f"{prefix}/wallet/withdrawals", json={"amount": str(amount)}, headers={"Idempotency-Key": key or f"auto-key-{_k['n']:06d}"})


def signed(body: bytes):
    return {"x-paystack-signature": hmac.new(KEY.encode(), body, hashlib.sha512).hexdigest(), "content-type": "application/json"}


def hook(client, event, reference):
    body = json.dumps({"event": event, "data": {"reference": reference, "status": "success"}}).encode()
    return client.post("/api/payments/webhook/paystack", content=body, headers=signed(body))


# ---------------------------------------------------------------- payout accounts
def test_a_mobile_money_account_is_registered_with_paystack_and_masked(me, ps_, db):
    out = setup_payout(me)
    assert out["account_number_masked"] == "•••• 4567" and out["provider_name"] == "MTN Mobile Money" and "0241234567" not in json.dumps(out)
    call = next(c for c in ps_.calls if c["url"].endswith("/transferrecipient"))
    assert call["json"] == {"type": "mobile_money", "name": "Efua Owusu", "account_number": "0241234567", "bank_code": "MTN", "currency": "GHS"}
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert "Payout account changed" in titles(db, me.user)
    summary = me.c.get("/api/vendor/wallet").json()
    assert summary["payout_account"]["account_name"] == "Efua Owusu" and "0241234567" not in json.dumps(summary)


def test_bank_accounts_and_changing_accounts(me, ps_, db):
    banks = me.c.get("/api/vendor/wallet/banks").json()
    assert {m["code"] for m in banks["mobile_money"]} == {"MTN", "VOD", "ATL"} and banks["banks"] == [{"name": "GCB Bank", "code": "040"}]
    setup_payout(me)
    setup_payout(me, type="BANK", provider_code="040", account_number="1234567890123", account_name="Efua Owusu")
    assert next(c for c in ps_.calls if c["json"] and c["json"].get("type") == "ghipss")["json"]["bank_code"] == "040"
    accts = db.scalars(select(PayoutAccount).where(PayoutAccount.account_id == me.acct.id)).all()
    assert sorted(a.is_active for a in accts) == [False, True]                                       # the old one is retired, not deleted


@pytest.mark.parametrize("change", [{"provider_code": "XYZ"}, {"account_number": "12345"}, {"account_number": "+14155550123"}, {"account_name": "E"},
                                    {"type": "BANK", "provider_code": "040", "account_number": "12"}, {"type": "CRYPTO"}])
def test_bad_payout_details_are_refused(me, ps_, change):
    assert me.c.put("/api/vendor/wallet/payout-account", json={**MOMO, **change}).status_code == 422


def test_paystack_refusing_the_recipient_is_a_clean_error(me, ps_, monkeypatch):
    monkeypatch.setattr(payment_providers.httpx, "request", lambda *a, **k: httpx.Response(400, json={"status": False, "message": "Invalid account"}))
    r = me.c.put("/api/vendor/wallet/payout-account", json=MOMO)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "payout_setup_failed" and KEY not in r.text


# ---------------------------------------------------------------- the rules for withdrawing
def test_the_wallet_says_exactly_what_blocks_a_withdrawal(me, ps_, db, monkeypatch):
    e = me.c.get("/api/vendor/wallet").json()["eligibility"]
    assert e["can_withdraw"] is False and "Add a Mobile Money number or bank account first." in e["reasons"]
    setup_payout(me)
    e = me.c.get("/api/vendor/wallet").json()["eligibility"]
    assert e["reasons"] == ["The minimum withdrawal is GH₵20.00."] and e["max_now"] == 0
    credit(db, me.acct, 180, hours_ago=-20)                                                                 # earned recently: held for 20 more hours
    credit(db, me.acct, 10, hours_ago=100)
    e = me.c.get("/api/vendor/wallet").json()["eligibility"]
    assert e["max_now"] == 10 and any("minimum" in r for r in e["reasons"])
    monkeypatch.setattr(get_settings(), "payout_account_cooldown_hours", 24)
    setup_payout(me, account_name="Efua Owusu")                                                            # a changed account starts a cooling-off period
    assert any("changed your payout account recently" in r for r in me.c.get("/api/vendor/wallet").json()["eligibility"]["reasons"])
    assert withdraw(me.c, 25).status_code == 409


def test_pending_money_cannot_be_withdrawn_until_the_hold_ends(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100, hours_ago=-22)                                              # earned 2 hours ago: 22 more hours of hold
    b = me.c.get("/api/vendor/wallet").json()
    assert b["balance"]["pending"] == 100 and b["balance"]["withdrawable"] == 0 and any("held for 24 hours" in i for i in b["eligibility"]["info"])
    assert withdraw(me.c, 50).status_code in (409, 422)


def test_a_successful_withdrawal_end_to_end(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100)
    r = withdraw(me.c, 60, key="happy-path-0001")
    assert r.status_code == 201 and r.json()["status"] == "PROCESSING" and r.json()["you_receive"] == 60.0
    t = next(c for c in ps_.calls if c["url"].endswith("/transfer") and c["method"] == "POST")["json"]
    assert t["source"] == "balance" and t["amount"] == 6000 and t["currency"] == "GHS" and t["recipient"] == "RCP_0001" and re.fullmatch(r"[a-z0-9_-]{16,50}", t["reference"])
    assert ws.balances(db, me.acct.id)["available"] == D("40.00")                                          # the money left the wallet at once
    ref = r.json()["reference"]
    ps_.transfers[ref]["status"] = "success"
    assert hook(me.c, "transfer.success", ref).status_code == 200
    assert me.c.get("/api/vendor/wallet/withdrawals").json()[0]["status"] == "SUCCESS" and ws.balances(db, me.acct.id)["available"] == D("40.00")
    assert "Money sent" in titles(db, me.user)


def test_the_withdrawal_fee_is_deducted_and_goes_to_the_platform(me, ps_, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "withdrawal_fee", 2.0)
    ps_.transfer_status = "success"                                                                        # settles at once, so nothing is left in flight
    setup_payout(me); credit(db, me.acct, 100)
    r = withdraw(me.c, 50).json()
    assert r["fee"] == 2.0 and r["you_receive"] == 48.0
    assert next(c for c in ps_.calls if c["url"].endswith("/transfer"))["json"]["amount"] == 4800
    assert ws.balances(db, me.acct.id)["available"] == D("50.00") and ws.balances(db, ws.platform_account(db).id)["total"] == D("2.00")
    assert withdraw(me.c, 2).status_code == 422                                                              # must be more than the fee


def test_the_same_tap_twice_never_sends_money_twice(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100)
    a, b = withdraw(me.c, 30, key="tap-twice-0001"), withdraw(me.c, 30, key="tap-twice-0001")
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert len([c for c in ps_.calls if c["url"].endswith("/transfer") and c["method"] == "POST"]) == 1
    assert ws.balances(db, me.acct.id)["available"] == D("70.00")
    assert me.c.post("/api/vendor/wallet/withdrawals", json={"amount": "30"}).status_code == 400            # a key is required


def test_simultaneous_withdrawals_can_never_overspend(me, ps_, db):
    ps_.transfer_status = "success"                                                                        # settles at once, so only the balance limits us
    setup_payout(me); credit(db, me.acct, 100)
    with ThreadPoolExecutor(max_workers=6) as pool:
        rs = list(pool.map(lambda i: withdraw(me.c, 40, key=f"race-key-{i:04d}"), range(6)))
    ok = [r for r in rs if r.status_code == 201]
    assert len(ok) == 2, [r.status_code for r in rs]                                                       # 40 + 40 fits in 100, a third does not
    db.expire_all()
    assert ws.balances(db, me.acct.id)["available"] == D("20.00")
    assert db.scalar(select(func.count()).select_from(Withdrawal)) == 2 and len([c for c in ps_.calls if c["url"].endswith("/transfer") and c["method"] == "POST"]) == 2


def test_only_one_withdrawal_may_be_in_flight(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 200)
    assert withdraw(me.c, 30).status_code == 201                                                           # stays PROCESSING (Paystack says pending)
    r = withdraw(me.c, 30)
    assert r.status_code == 409 and "already have a withdrawal in progress" in r.json()["detail"]["message"]


def test_the_daily_limit(me, ps_, db, monkeypatch):
    ps_.transfer_status = "success"
    monkeypatch.setattr(get_settings(), "max_withdrawal_per_day", 100.0)
    setup_payout(me); credit(db, me.acct, 500)
    assert withdraw(me.c, 60).status_code == 201
    r = withdraw(me.c, 60)
    assert r.status_code == 422 and "up to GH₵40.00" in r.json()["detail"]["message"]
    assert withdraw(me.c, 40).status_code == 201 and withdraw(me.c, 20).status_code == 409                   # limit reached


def test_someone_who_owes_the_platform_cannot_withdraw(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100); credit(db, me.acct, -150)
    e = me.c.get("/api/vendor/wallet").json()["eligibility"]
    assert any("You owe KORA EATS GH₵50.00" in r for r in e["reasons"]) and withdraw(me.c, 30).status_code == 409


def test_an_unapproved_vendor_cannot_withdraw(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100)
    from app.models.catalog import ApprovalStatus
    me.w.near.approval_status = ApprovalStatus.SUSPENDED if hasattr(ApprovalStatus, "SUSPENDED") else ApprovalStatus.REJECTED; db.commit()
    assert any("isn't approved" in r for r in ps.eligibility(db, me.acct)["reasons"])


# ---------------------------------------------------------------- when things go wrong
def test_paystack_refusing_a_transfer_returns_the_money(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100); ps_.reject = "Insufficient balance"
    r = withdraw(me.c, 50)
    assert r.status_code == 201 and r.json()["status"] == "FAILED" and "Insufficient balance" in r.json()["failure_reason"]
    assert ws.balances(db, me.acct.id)["available"] == D("100.00")                                           # nothing was lost
    assert "Withdrawal didn't go through" in titles(db, me.user)


def test_an_otp_prompt_is_explained_to_the_admin(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100); ps_.otp = True
    r = withdraw(me.c, 50).json()
    assert r["status"] == "FAILED" and "OTP" in r["failure_reason"] and ws.balances(db, me.acct.id)["available"] == D("100.00")


def test_when_the_outcome_is_unknown_money_is_NOT_returned_until_we_know(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100); ps_.down = True
    r = withdraw(me.c, 50).json()
    assert r["status"] == "PROCESSING"                                                                       # it may have been sent: never assume it failed
    assert ws.balances(db, me.acct.id)["available"] == D("50.00")
    w = db.scalar(select(Withdrawal)); w.requested_at = utcnow() - timedelta(minutes=10); db.commit()
    ps_.verify_down = True
    assert ps.reconcile_processing(db) == 0 and db.scalar(select(Withdrawal.status)) == W.PROCESSING          # still can't tell: still waiting
    ps_.verify_down = False
    ps_.transfers[w.reference] = {"status": "success", "amount": 5000, "recipient": "x"}                       # Paystack did send it after all
    assert ps.reconcile_processing(db) == 1 and db.scalar(select(Withdrawal.status)) == W.SUCCESS
    assert ws.balances(db, me.acct.id)["available"] == D("50.00")                                            # and it was NOT double-credited


def test_an_unconfirmed_transfer_that_turns_out_to_have_failed_is_returned_once(me, ps_, db):
    setup_payout(me); credit(db, me.acct, 100); ps_.down = True
    w_id = withdraw(me.c, 50).json()["id"]
    w = db.get(Withdrawal, w_id); w.requested_at = utcnow() - timedelta(minutes=10); db.commit()
    ps_.transfers[w.reference] = {"status": "failed", "amount": 5000, "recipient": "x"}
    assert ps.reconcile_processing(db) == 1
    assert ps.reconcile_processing(db) == 0
    assert db.get(Withdrawal, w_id).status == W.FAILED and ws.balances(db, me.acct.id)["available"] == D("100.00")


def test_a_failed_webhook_returns_the_money_exactly_once(me, ps_, db, client):
    setup_payout(me); credit(db, me.acct, 100)
    ref = withdraw(me.c, 50).json()["reference"]
    ps_.transfers[ref]["status"] = "failed"
    assert hook(client, "transfer.failed", ref).status_code == 200 and hook(client, "transfer.failed", ref).status_code == 200      # Paystack retries webhooks
    db.expire_all()
    assert db.scalar(select(Withdrawal.status)) == W.FAILED and ws.balances(db, me.acct.id)["available"] == D("100.00")
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == K.WITHDRAWAL_REVERSAL)) == 1


def test_a_transfer_that_bounces_back_after_success_is_returned(me, ps_, db, client):
    setup_payout(me); credit(db, me.acct, 100); ps_.transfer_status = "success"
    ref = withdraw(me.c, 50).json()["reference"]
    assert db.scalar(select(Withdrawal.status)) == W.SUCCESS
    ps_.transfers[ref]["status"] = "reversed"
    hook(client, "transfer.reversed", ref); hook(client, "transfer.reversed", ref)
    db.expire_all()
    assert db.scalar(select(Withdrawal.status)) == W.REVERSED and ws.balances(db, me.acct.id)["available"] == D("100.00")


def test_a_forged_webhook_cannot_mark_a_withdrawal_paid_or_failed(me, ps_, db, client):
    setup_payout(me); credit(db, me.acct, 100)
    ref = withdraw(me.c, 50).json()["reference"]                                                              # Paystack says "pending"
    body = json.dumps({"event": "transfer.success", "data": {"reference": ref}}).encode()
    assert client.post("/api/payments/webhook/paystack", content=body, headers={"content-type": "application/json"}).status_code == 400          # no signature
    assert client.post("/api/payments/webhook/paystack", content=body, headers={"x-paystack-signature": "0" * 128}).status_code == 400            # wrong signature
    hook(client, "transfer.success", ref)                                                                       # signed correctly, but Paystack still says pending
    db.expire_all()
    assert db.scalar(select(Withdrawal.status)) == W.PROCESSING                                                 # we asked Paystack; the body is never believed
    hook(client, "transfer.success", "kora-wd-doesnotexist00")                                                  # unknown reference: acknowledged, ignored


# ---------------------------------------------------------------- admin approval for large amounts
@pytest.fixture()
def admin(make_user, login):
    return login(make_user("payout-admin@example.com", UserRole.ADMIN, "Pay Admin"))


def test_large_withdrawals_wait_for_an_admin(me, ps_, db, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "withdrawal_approval_threshold", 100.0)
    setup_payout(me); credit(db, me.acct, 400)
    r = withdraw(me.c, 150).json()
    assert r["status"] == "PENDING_APPROVAL" and ws.balances(db, me.acct.id)["available"] == D("250.00")      # reserved, not yet sent
    assert not any(c["url"].endswith("/transfer") for c in ps_.calls)
    listed = admin.get("/api/admin/wallets/withdrawals", params={"status": "PENDING_APPROVAL"}).json()
    assert listed[0]["sends_to"] == "MTN Mobile Money •••• 4567 (Efua Owusu)" and listed[0]["owner_type"] == "VENDOR"
    assert me.c.post(f"/api/admin/wallets/withdrawals/{r['id']}/approve").status_code == 403                  # only admins
    ps_.transfer_status = "success"
    ok = admin.post(f"/api/admin/wallets/withdrawals/{r['id']}/approve").json()
    assert ok["status"] == "SUCCESS" and any(c["url"].endswith("/transfer") for c in ps_.calls)
    assert admin.post(f"/api/admin/wallets/withdrawals/{r['id']}/approve").status_code == 409                  # not pending any more
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "withdrawal_approved"))


def test_an_admin_can_decline_and_the_money_comes_back(me, ps_, db, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "withdrawal_approval_threshold", 100.0)
    setup_payout(me); credit(db, me.acct, 400)
    wid = withdraw(me.c, 150).json()["id"]
    assert admin.post(f"/api/admin/wallets/withdrawals/{wid}/reject", json={"reason": "x"}).status_code == 422
    r = admin.post(f"/api/admin/wallets/withdrawals/{wid}/reject", json={"reason": "Please verify your ID first"}).json()
    assert r["status"] == "REJECTED" and ws.balances(db, me.acct.id)["available"] == D("400.00")
    assert "Withdrawal declined" in titles(db, me.user) and not any(c["url"].endswith("/transfer") for c in ps_.calls)


def test_admin_adjustments_need_a_reason_and_are_audited(me, ps_, db, admin):
    url = f"/api/admin/wallets/accounts/{me.acct.id}/adjust"
    assert admin.post(url, json={"amount": "25", "reason": "ok"}).status_code == 422
    assert admin.post(url, json={"amount": "0", "reason": "no change at all"}).status_code == 404
    r = admin.post(url, json={"amount": "-12.50", "reason": "Customer refund paid by cash"}).json()
    assert r["balance"]["available"] == -12.5 and db.scalar(select(AuditLog.id).where(AuditLog.action == "wallet_adjusted"))
    assert admin.post(f"/api/admin/wallets/accounts/{ws.platform_account(db).id}/adjust", json={"amount": "5", "reason": "tamper with revenue"}).status_code == 404
    assert me.c.post(url, json={"amount": "5", "reason": "give myself money"}).status_code == 403


# ---------------------------------------------------------------- the owners' view and the admin's view
def test_the_wallet_screens_after_a_real_delivery(world, vendor_user, make_rider, login, shopper, place_order, db, admin):
    ru, rider = make_rider("pay-rider@example.com", name="Kwesi Rider")
    crew = SimpleNamespace(w=world, vendor=login(vendor_user), rider_user=ru, rider=rider, shopper=shopper, login=login, vendor_user=vendor_user)
    deliver(crew, place_order())
    v = crew.vendor.get("/api/vendor/wallet").json()
    assert v["balance"]["pending"] == 81.0 and v["balance"]["commission_paid"] == 9.0 and v["rates"] == {"commission_percent": 10.0}
    assert "10% KORA EATS commission" in v["explain"]
    row = crew.vendor.get("/api/vendor/wallet/statement").json()["orders"][0]
    assert row["you_earn"] == 81.0 and {p["kind"]: p["amount"] for p in row["parts"]} == {"ORDER_SALE": 90.0, "ORDER_COMMISSION": -9.0}
    rw = login(ru).get("/api/rider/wallet").json()
    assert rw["balance"]["pending"] == 4.0 and rw["rates"] == {"deduction_percent": 20.0} and "20% KORA EATS deduction" in rw["explain"]
    assert login(ru).get("/api/vendor/wallet").status_code in (403, 404) and crew.vendor.get("/api/rider/wallet").status_code in (403, 404)    # separate worlds
    o = admin.get("/api/admin/wallets/overview").json()
    assert o["revenue"]["vendor_commission"] == 9.0 and o["revenue"]["rider_deduction"] == 1.0 and o["revenue"]["net_revenue"] == 10.0
    assert o["owed"]["vendors"]["pending"] == 81.0 and o["owed"]["riders"]["pending"] == 4.0 and o["recent_orders"][0]["platform_net"] == 10.0
    assert o["reconciliation"]["ledger_balance"] == 95.0                                                       # exactly what the customer paid
    assert shopper.get("/api/admin/wallets/overview").status_code == 403


def test_a_second_vendor_never_sees_the_first_vendors_money(world, vendor_user, login, shopper, place_order, make_rider, make_user, db):
    ru, rider = make_rider("iso-rider@example.com")
    crew = SimpleNamespace(w=world, vendor=login(vendor_user), rider_user=ru, rider=rider, shopper=shopper, login=login, vendor_user=vendor_user)
    deliver(crew, place_order())
    rival = make_user("rival@example.com", UserRole.VENDOR); world.far.owner_user_id = rival.id; db.commit()
    rc = login(rival)
    assert rc.get("/api/vendor/wallet").json()["balance"]["lifetime_earnings"] == 0 and rc.get("/api/vendor/wallet/statement").json()["orders"] == []
