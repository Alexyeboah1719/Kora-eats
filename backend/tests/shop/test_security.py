import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.rate_limit import limiter
from app.main import app
from app.services import catalog_service
from tests.auth.conftest import PASSWORD, otp_from


# ---------------------------------------------------------------- headers
def test_security_headers_on_pages_and_api(client):
    page = client.get("/")
    assert page.headers["x-content-type-options"] == "nosniff" and page.headers["x-frame-options"] == "DENY"
    assert page.headers["referrer-policy"] and "geolocation=(self)" in page.headers["permissions-policy"]
    csp = page.headers["content-security-policy-report-only"]            # report-only by default: see DEPLOYMENT.md before enforcing
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "script-src 'self' https://cdn.jsdelivr.net https://accounts.google.com" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    assert page.headers["cache-control"] == "no-cache" and page.headers["x-request-id"]
    api = client.get("/api/categories")
    assert api.headers["cache-control"] == "no-store" and "content-security-policy-report-only" not in api.headers
    assert client.get("/icons/icon-192.png").headers["cache-control"].startswith("public, max-age=")


def test_hsts_only_in_production_and_csp_modes(client, monkeypatch):
    s = get_settings()
    assert "strict-transport-security" not in client.get("/").headers
    monkeypatch.setattr(s, "environment", "production")
    assert "max-age=31536000" in client.get("/").headers["strict-transport-security"]
    monkeypatch.setattr(s, "csp_mode", "enforce")
    h = client.get("/").headers
    assert "content-security-policy" in h and "content-security-policy-report-only" not in h
    monkeypatch.setattr(s, "csp_mode", "off")
    assert "content-security-policy" not in client.get("/").headers


def test_no_page_needs_inline_script_or_handlers():
    from pathlib import Path
    for p in (Path(__file__).resolve().parents[3] / "frontend").rglob("*.html"):
        text = p.read_text()
        assert "<script>" not in text and " onclick=" not in text and "javascript:" not in text, p.name   # required by the CSP


def test_csp_violation_reports_are_accepted_and_bounded(client, caplog):
    with caplog.at_level("WARNING"):
        r = client.post("/api/csp-report", content=b'{"csp-report":{"blocked-uri":"https://evil.com"}}' + b"x" * 100000,
                        headers={"content-type": "application/csp-report"})
    assert r.status_code == 204 and "CSP violation" in caplog.text and len(caplog.text) < 6000


# ---------------------------------------------------------------- CSRF origin check
def test_cross_site_writes_are_blocked(shopper, world):
    body = {"menu_item_id": world.spicy.id}
    evil = shopper.post("/api/cart/items", json=body, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["detail"]["code"] == "bad_origin"
    assert shopper.post("/api/cart/items", json=body, headers={"Origin": "null"}).status_code == 403
    assert shopper.post("/api/cart/items", json=body, headers={"Origin": "http://testserver"}).status_code == 200   # our own site
    assert shopper.post("/api/cart/items", json=body).status_code == 200                                             # no Origin (curl, server-to-server)
    assert shopper.get("/api/cart", headers={"Origin": "https://evil.example"}).status_code == 200                   # reads aren't state-changing
    assert shopper.post("/api/payments/webhook/paystack", content=b"{}").status_code == 400                         # webhooks send no Origin: reach the handler


def test_allow_listed_origin_is_accepted(shopper, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "cors_origins", ["https://app.foodapp.example"])
    assert shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id}, headers={"Origin": "https://app.foodapp.example"}).status_code == 200


# ---------------------------------------------------------------- rate limiting
def test_global_api_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_rate_limit_per_minute", 5)
    limiter.reset()
    codes = [client.get("/api/categories").status_code for _ in range(7)]
    assert codes == [200] * 5 + [429, 429]
    r = client.get("/api/categories")
    assert r.json()["detail"]["code"] == "rate_limited" and r.headers["retry-after"] == "60"
    assert client.get("/api/health").status_code == 200                       # health checks are never throttled
    assert client.get("/").status_code == 200                                 # pages aren't API traffic


def test_forged_forwarded_header_cannot_dodge_limits_unless_proxy_is_trusted(client, monkeypatch, verified_user):
    client.post("/api/auth/logout")
    attempt = lambda ip: client.post("/api/auth/login", json={"email": "ama@example.com", "password": "Wrong12345"}, headers={"X-Forwarded-For": ip}).status_code
    assert [attempt(f"9.9.9.{i}") for i in range(11)][-1] == 429                       # hops=0: header ignored, same bucket
    limiter.reset()
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    assert [attempt(f"9.9.9.{i}") for i in range(11)][-1] == 401                       # hops=1: each real client IP has its own bucket
    assert [attempt("6.6.6.6") for _ in range(11)][-1] == 429                          # ...and is still limited


# ---------------------------------------------------------------- errors never leak internals
def test_unhandled_errors_are_generic(world, monkeypatch, caplog):
    def boom(db):
        raise RuntimeError("secret database detail postgres://user:pw@host")
    monkeypatch.setattr(catalog_service, "list_categories", boom)
    c = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level("ERROR"):
        r = c.get("/api/categories")
    assert r.status_code == 500 and r.json()["detail"]["code"] == "server_error"
    assert "secret" not in r.text and "postgres" not in r.text and "Traceback" not in r.text
    assert "secret database detail" in caplog.text                                     # but the real error IS logged for us


# ---------------------------------------------------------------- production configuration guards
BASE = dict(database_url="postgresql+psycopg://x/y", environment="production", debug=False, cors_origins=[], secret_key="k" * 40, payment_provider="paystack", paystack_secret_key="sk_test_dummy_key_for_tests",
            frontend_base_url="https://foodapp.example")


@pytest.mark.parametrize("override,message", [
    ({"secret_key": "dev-only-insecure-secret-change-me"}, "SECRET_KEY"), ({"secret_key": "short"}, "SECRET_KEY"),
    ({"debug": True}, "DEBUG"), ({"frontend_base_url": "http://foodapp.example"}, "https"),
    ({"cors_origins": ["*"]}, "CORS_ORIGINS"), ({"payment_provider": "dev"}, "simulator"), ({"paystack_secret_key": ""}, "PAYSTACK_SECRET_KEY"),
    ({"payment_provider": "seevplus"}, "PAYMENT_PROVIDER")])
def test_production_refuses_unsafe_config(override, message):
    with pytest.raises(ValueError, match=message):
        Settings(**{**BASE, **override})


def test_production_accepts_a_safe_config():
    assert Settings(**BASE).cookie_secure is True


def test_email_bodies_are_not_logged_in_production(monkeypatch, caplog):
    from app.services import email_service
    s = get_settings()
    monkeypatch.setattr(s, "resend_api_key", ""); monkeypatch.setattr(s, "environment", "production")
    with caplog.at_level("WARNING"):
        assert email_service.send_email("a@b.com", "Code", "<p>123456</p>") is False
    assert "123456" not in caplog.text and "was NOT sent" in caplog.text


# ---------------------------------------------------------------- authorisation sweeps
PRIVATE_GET = ["/api/cart", "/api/orders", "/api/me/profile", "/api/me/addresses", "/api/me/favorites", "/api/notifications",
               "/api/notifications/unread-count", "/api/payments", "/api/support/tickets", "/api/ai/preferences", "/api/auth/me",
               "/api/vendor/me", "/api/vendor/menu", "/api/vendor/orders", "/api/vendor/analytics", "/api/vendor/coupons",
               "/api/rider/me", "/api/rider/available", "/api/rider/earnings", "/api/groups/ABC123"]
PRIVATE_POST = ["/api/cart/items", "/api/orders", "/api/reviews", "/api/me/addresses", "/api/vendor/apply", "/api/rider/apply",
                "/api/support/tickets", "/api/groups", "/api/payments/orders/1/initiate", "/api/rider/location"]


@pytest.mark.parametrize("path", PRIVATE_GET)
def test_private_reads_require_login(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", PRIVATE_POST)
def test_private_writes_require_login(client, path):
    assert client.post(path, json={}).status_code == 401


def test_cannot_grant_yourself_a_role(client, emails, db):
    from sqlalchemy import select
    from app.models.user import User, UserRole
    r = client.post("/api/auth/register", json={"full_name": "Mallory", "email": "m@example.com", "password": PASSWORD,
                                                "confirm_password": PASSWORD, "role": "ADMIN", "is_active": True, "is_email_verified": True})
    assert r.status_code == 201
    u = db.scalar(select(User).where(User.email == "m@example.com"))
    assert u.role == UserRole.CUSTOMER and u.is_email_verified is False              # extra fields were ignored
    client.post("/api/auth/verify-otp", json={"email": "m@example.com", "code": otp_from(emails)})
    client.put("/api/me/profile", json={"full_name": "Mallory", "role": "ADMIN"})
    db.expire_all()
    assert db.scalar(select(User).where(User.email == "m@example.com")).role == UserRole.CUSTOMER
    assert client.get("/api/admin/overview").status_code == 403


def test_no_secrets_committed():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    bad = ("BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE", "sk_live_", "re_live_", "AKIA")
    for p in list(root.rglob("*.py")) + list(root.rglob("*.js")) + list(root.rglob("*.html")) + list(root.rglob("*.md")):
        if not p.is_file() or {"tests", "venv", ".venv", "node_modules"} & set(p.parts) or p.name.startswith("test_"):
            continue                                      # only OUR source files: not tests, not downloaded third-party packages
        text = p.read_text(errors="ignore")
        assert not any(b in text for b in bad), f"possible secret in {p}"
    assert ".env" in (root / ".gitignore").read_text().split()


def test_the_camera_is_allowed_for_our_own_pages_only(client):
    """Camera capture and QR scanning need the camera. The policy must allow it for this site, but never for embedded third parties."""
    pp = client.get("/api/health").headers["permissions-policy"]
    assert "camera=(self)" in pp and "geolocation=(self)" in pp
    assert "microphone=()" in pp and "payment=()" in pp                                   # everything we don't use stays off
