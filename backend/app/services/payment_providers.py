"""Payment provider adapters.

Every provider implements three things: create a hosted checkout, verify a payment by asking the
PROVIDER (server to server), and authenticate a webhook. The rest of the app never talks to a
provider directly, so adding Card / Bank Transfer later, or switching provider, touches only this file.

PaystackProvider      - production provider (Ghana: GHS, Mobile Money first; Card / Bank Transfer switchable).
DevSimulatorProvider  - development only; blocked in production by config validation.

Paystack API facts used here (official docs, https://paystack.com/docs/api/transaction/):
  POST {base}/transaction/initialize  body: email, amount (SUBUNIT: pesewas), currency, reference,
                                      callback_url, channels[], metadata   ->  data.authorization_url, data.access_code
  GET  {base}/transaction/verify/:reference  ->  data.status ("success" means paid), data.amount, data.currency
  Webhooks: header x-paystack-signature = HMAC-SHA512 (hex) of the RAW body, keyed with the secret key.
"""
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.payments import DevProviderSession

logger = logging.getLogger(__name__)

KNOWN_METHODS = ("MOBILE_MONEY", "CARD", "BANK_TRANSFER")
METHOD_LABELS = {"MOBILE_MONEY": "Mobile Money", "CARD": "Card", "BANK_TRANSFER": "Bank transfer"}
PAYSTACK_CHANNELS = {"MOBILE_MONEY": ["mobile_money"], "CARD": ["card"], "BANK_TRANSFER": ["bank_transfer"]}


def enabled_methods() -> list[str]:
    """Methods customers may choose. Mobile Money works first; set ENABLED_PAYMENT_METHODS to add the others."""
    return list(get_settings().enabled_payment_methods)


class ProviderError(Exception):
    """The provider could not be reached or rejected the request."""


class ProviderNotConfigured(ProviderError):
    pass


class ProviderUnreachable(ProviderError):
    """We could not tell what happened (network failure, timeout, 5xx). For money going OUT this is dangerous: the transfer may or may not
    have been sent, so we must never assume it failed. We ask again later instead."""


@dataclass
class TransferResult:
    status: str                      # SUCCESS | PROCESSING | FAILED | REVERSED
    transfer_code: str | None = None
    reason: str | None = None


MOMO_PROVIDERS = {"MTN": "MTN Mobile Money", "VOD": "Telecel Cash", "ATL": "AirtelTigo Money"}      # Paystack's bank_code values for Ghana


@dataclass
class CheckoutSession:
    provider_reference: str | None
    checkout_url: str


@dataclass
class VerifyResult:
    status: str                     # "SUCCESS" | "FAILED" | "PENDING"
    amount: Decimal | None = None
    currency: str | None = None


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, db: Session, *, reference: str, amount: Decimal, currency: str, method: str,
                        customer_email: str, customer_name: str, phone: str, description: str,
                        return_url: str) -> CheckoutSession: ...

    def verify(self, db: Session, reference: str) -> VerifyResult: ...

    def authenticate_webhook(self, headers: dict[str, str], body: bytes) -> str | None:
        """Return the merchant reference the webhook refers to (None = valid but irrelevant event), or raise
        ProviderError if the signature is invalid. The caller NEVER trusts the body's payment status: it re-verifies."""


def to_subunit(amount: Decimal) -> int:
    """GH₵95.00 -> 9500 pesewas. Paystack amounts are integers in the smallest currency unit."""
    return int((Decimal(amount) * 100).to_integral_value(rounding=ROUND_HALF_UP))


class PaystackProvider:
    name = "paystack"

    def __init__(self) -> None:
        s = get_settings()
        self._key, self._base = s.paystack_secret_key, s.paystack_base_url.rstrip("/")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self._key:
            raise ProviderNotConfigured("PAYSTACK_SECRET_KEY is not set")
        try:
            r = httpx.request(method, f"{self._base}{path}", json=body, timeout=15,
                              headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise ProviderUnreachable(f"Paystack unreachable: {type(e).__name__}") from e
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        if r.status_code >= 500:
            raise ProviderUnreachable(f"Paystack error {r.status_code}")
        if r.status_code >= 400 or not payload.get("status"):
            # the key is never logged; Paystack's message is short and safe to keep
            raise ProviderError(f"Paystack error {r.status_code}: {str(payload.get('message', ''))[:120]}")
        return payload.get("data") or {}

    def create_checkout(self, db, *, reference, amount, currency, method, customer_email, customer_name, phone,
                        description, return_url) -> CheckoutSession:
        callback = return_url.split("?")[0]          # Paystack appends ?trxref=&reference= itself; keep our URL query-free
        data = self._call("POST", "/transaction/initialize", {
            "email": customer_email, "amount": str(to_subunit(amount)), "currency": currency, "reference": reference,
            "callback_url": callback, "channels": PAYSTACK_CHANNELS[method],
            "metadata": {"description": description, "customer_name": customer_name, "phone": phone, "cancel_action": callback},
        })
        if not data.get("authorization_url"):
            raise ProviderError("Paystack did not return a checkout URL")
        return CheckoutSession(provider_reference=data.get("access_code"), checkout_url=data["authorization_url"])

    def verify(self, db, reference) -> VerifyResult:
        data = self._call("GET", f"/transaction/verify/{reference}")
        status = str(data.get("status", "")).lower()
        amount = Decimal(int(data["amount"])) / 100 if data.get("amount") is not None else None
        mapped = "SUCCESS" if status == "success" else "FAILED" if status in ("failed", "reversed") else "PENDING"
        return VerifyResult(mapped, amount, (data.get("currency") or "").upper() or None)   # abandoned/pending/ongoing stay PENDING

    # ---------------------------------------------------------------- payouts (Paystack Transfers)
    def create_recipient(self, db, *, kind: str, name: str, account_number: str, provider_code: str) -> str:
        """Register where money should be sent. Mobile Money: type 'mobile_money' with bank_code MTN / VOD / ATL. Banks: type 'ghipss' with the bank's code."""
        data = self._call("POST", "/transferrecipient", {"type": "mobile_money" if kind == "MOBILE_MONEY" else "ghipss", "name": name,
                                                          "account_number": account_number, "bank_code": provider_code, "currency": "GHS"})
        if not data.get("recipient_code"):
            raise ProviderError("Paystack did not return a recipient code")
        return data["recipient_code"]

    def initiate_transfer(self, db, *, reference: str, amount: Decimal, recipient_code: str, reason: str) -> TransferResult:
        """Send money from the Paystack balance. 'amount' is in pesewas. The reference must be unique (16-50 chars, lowercase letters, digits, - and _)."""
        data = self._call("POST", "/transfer", {"source": "balance", "amount": to_subunit(amount), "recipient": recipient_code,
                                                 "reference": reference, "reason": reason[:100], "currency": "GHS"})
        return self._transfer_result(data)

    def verify_transfer(self, db, reference: str) -> TransferResult:
        return self._transfer_result(self._call("GET", f"/transfer/verify/{reference}"))

    def list_banks(self) -> list[dict]:
        """Banks that can receive payouts. (Not exercised against live Paystack yet: confirm with your keys.)"""
        r = self._call_list("/bank?currency=GHS&perPage=100")
        return [{"name": b["name"], "code": b["code"]} for b in r if b.get("code") and str(b.get("type", "")).lower() != "mobile_money"]

    def _call_list(self, path: str) -> list[dict]:
        if not self._key:
            raise ProviderNotConfigured("PAYSTACK_SECRET_KEY is not set")
        try:
            r = httpx.request("GET", f"{self._base}{path}", timeout=15, headers={"Authorization": f"Bearer {self._key}"})
            payload = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderUnreachable("Paystack unreachable") from e
        if r.status_code >= 400 or not payload.get("status"):
            raise ProviderError(f"Paystack error {r.status_code}")
        return payload.get("data") or []

    @staticmethod
    def _transfer_result(data: dict) -> TransferResult:
        status = str(data.get("status", "")).lower()
        code = data.get("transfer_code")
        if status == "success":
            return TransferResult("SUCCESS", code)
        if status in ("failed", "rejected", "blocked"):
            return TransferResult("FAILED", code, str(data.get("reason") or status))
        if status == "reversed":
            return TransferResult("REVERSED", code, "Reversed by the bank or network")
        if status == "otp":
            raise ProviderError("Paystack is asking for an OTP. Turn OFF 'Confirm transfers before sending' in the Paystack dashboard so payouts can run automatically.")
        return TransferResult("PROCESSING", code)                    # pending / received / processing / queued

    def authenticate_webhook(self, headers, body) -> str | None:
        if not self._key:
            raise ProviderNotConfigured("PAYSTACK_SECRET_KEY is not set")
        sent = {k.lower(): v for k, v in headers.items()}.get("x-paystack-signature", "")
        expected = hmac.new(self._key.encode(), body, hashlib.sha512).hexdigest()
        if not sent or not hmac.compare_digest(sent, expected):          # computed over the RAW bytes, constant-time compare
            raise ProviderError("Invalid Paystack webhook signature")
        try:
            event = json.loads(body)
        except ValueError as e:
            raise ProviderError("Webhook body is not JSON") from e
        data = event.get("data") if isinstance(event, dict) else None
        ref = data.get("reference") if isinstance(data, dict) else None
        return ref if isinstance(ref, str) and ref else None


class DevSimulatorProvider:
    name = "dev"

    def create_checkout(self, db, *, reference, amount, currency, method, customer_email, customer_name, phone,
                        description, return_url) -> CheckoutSession:
        db.add(DevProviderSession(reference=reference, amount=amount, currency=currency, return_url=return_url))
        db.flush()
        return CheckoutSession(provider_reference=f"DEV-{reference}", checkout_url=f"/api/payments/dev/checkout/{reference}")

    def verify(self, db, reference) -> VerifyResult:
        s = db.scalar(select(DevProviderSession).where(DevProviderSession.reference == reference))
        if s is None:
            return VerifyResult("FAILED")
        return VerifyResult(s.status, s.amount, s.currency)

    def authenticate_webhook(self, headers, body) -> str | None:
        raise ProviderError("The development simulator has no webhooks.")

    def list_banks(self) -> list[dict]:
        return [{"name": "Example Bank Ghana", "code": "001"}, {"name": "Sample Savings & Loans", "code": "002"}]

    # payouts are simulated: no money moves, every transfer "succeeds" at once
    def create_recipient(self, db, *, kind, name, account_number, provider_code) -> str:
        return "RCP_DEV_" + hashlib.sha1(f"{kind}{account_number}{provider_code}".encode()).hexdigest()[:12]

    def initiate_transfer(self, db, *, reference, amount, recipient_code, reason) -> TransferResult:
        return TransferResult("SUCCESS", "TRF_DEV_" + reference[-8:])

    def verify_transfer(self, db, reference) -> TransferResult:
        return TransferResult("SUCCESS", "TRF_DEV_" + reference[-8:])


_PROVIDERS = {"dev": DevSimulatorProvider, "paystack": PaystackProvider}


def get_provider(name: str | None = None) -> PaymentProvider:
    name = name or get_settings().payment_provider
    if name not in _PROVIDERS:
        raise ProviderNotConfigured(f"Unknown payment provider '{name}'.")
    return _PROVIDERS[name]()
