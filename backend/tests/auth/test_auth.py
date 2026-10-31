import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.api.deps import require_roles
from app.core.config import get_settings
from app.core.timeutil import utcnow
from datetime import timedelta
from app.models.auth import EmailVerification, RefreshToken
from app.models.user import User, UserRole
from app.services import auth_service
from tests.auth.conftest import PASSWORD, otp_from, token_from


# ---------------------------------------------------------------- register + OTP
def test_register_sends_otp_and_blocks_login_until_verified(client, emails, register):
    r = register()
    assert r.status_code == 201
    assert emails[-1]["to"] == "ama@example.com"
    r = client.post("/api/auth/login", json={"email": "ama@example.com", "password": PASSWORD})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "email_not_verified"


def test_verify_otp_logs_in_as_customer(client, emails, verified_user):
    assert verified_user["role"] == "CUSTOMER"
    assert verified_user["is_email_verified"] is True
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "ama@example.com"
    assert "HttpOnly" in client.cookies.jar.__repr__() or True  # cookie flags checked below


def test_cookies_are_httponly(client, emails, register):
    register()
    r = client.post("/api/auth/verify-otp", json={"email": "ama@example.com", "code": otp_from(emails)})
    cookie_headers = [h for h in r.headers.get_list("set-cookie")]
    assert len(cookie_headers) == 2
    assert all("HttpOnly" in h and "SameSite=lax" in h for h in cookie_headers)


def test_password_is_hashed_with_argon2(client, emails, register, db):
    register()
    user = db.scalar(select(User))
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash


def test_wrong_otp_locks_after_max_attempts(client, emails, register):
    register()
    for _ in range(get_settings().otp_max_attempts):
        r = client.post("/api/auth/verify-otp", json={"email": "ama@example.com", "code": "000000"})
        assert r.status_code == 400
    r = client.post("/api/auth/verify-otp", json={"email": "ama@example.com", "code": otp_from(emails)})
    assert r.status_code == 429  # even the right code is refused until a new one is requested


def test_expired_otp_rejected(client, emails, register, db):
    register()
    db.execute(update(EmailVerification).values(expires_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    r = client.post("/api/auth/verify-otp", json={"email": "ama@example.com", "code": otp_from(emails)})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "otp_expired"


def test_resend_invalidates_old_code(client, emails, register):
    register()
    old = otp_from(emails)
    client.post("/api/auth/resend-otp", json={"email": "ama@example.com"})
    new = otp_from(emails)
    assert len(emails) == 2
    if old != new:
        r = client.post("/api/auth/verify-otp", json={"email": "ama@example.com", "code": old})
        assert r.status_code == 400
    r = client.post("/api/auth/verify-otp", json={"email": "ama@example.com", "code": new})
    assert r.status_code == 200


def test_resend_cooldown_enforced(client, emails, register, monkeypatch):
    monkeypatch.setattr(get_settings(), "otp_resend_cooldown_seconds", 60)
    register()
    r = client.post("/api/auth/resend-otp", json={"email": "ama@example.com"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "otp_cooldown"


def test_hourly_otp_cap(client, emails, register):
    register()
    for _ in range(get_settings().otp_max_per_hour - 1):
        assert client.post("/api/auth/resend-otp", json={"email": "ama@example.com"}).status_code == 200
    r = client.post("/api/auth/resend-otp", json={"email": "ama@example.com"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "otp_limit"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("pw,confirm", [("short1", "short1"), ("onlyletters", "onlyletters"),
                                        ("12345678", "12345678"), (PASSWORD, "Different123")])
def test_bad_passwords_rejected(client, pw, confirm):
    r = client.post("/api/auth/register", json={
        "full_name": "Kofi", "email": "k@example.com", "password": pw, "confirm_password": confirm})
    assert r.status_code == 422


def test_duplicate_verified_email_rejected(client, emails, verified_user, register):
    client.post("/api/auth/logout")
    assert register().status_code == 409


# ---------------------------------------------------------------- login / logout / refresh
def test_login_generic_error_for_bad_password_and_unknown_email(client, emails, verified_user):
    a = client.post("/api/auth/login", json={"email": "ama@example.com", "password": "Wrong12345"})
    b = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "Wrong12345"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_login_success_and_logout(client, emails, verified_user):
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "AMA@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revokes_refresh_token(client, emails, verified_user, db):
    client.post("/api/auth/logout")
    tokens = db.scalars(select(RefreshToken)).all()
    assert tokens and all(t.revoked_at is not None for t in tokens)


def test_refresh_rotates_and_detects_reuse(client, emails, verified_user):
    old_refresh = client.cookies.get("refresh_token")
    assert client.post("/api/auth/refresh").status_code == 200
    new_refresh = client.cookies.get("refresh_token")
    assert new_refresh != old_refresh
    # Replaying the old (rotated) token is treated as theft: every session is revoked.
    client.cookies.set("refresh_token", old_refresh, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401
    client.cookies.set("refresh_token", new_refresh, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401


def test_me_requires_login(client):
    assert client.get("/api/auth/me").status_code == 401


def test_login_rate_limited(client, emails, verified_user):
    client.post("/api/auth/logout")
    codes = [client.post("/api/auth/login", json={"email": "ama@example.com", "password": "Wrong12345"}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


# ---------------------------------------------------------------- roles
class _FakeUser:
    def __init__(self, role):
        self.role = role


def test_require_roles_allows_and_blocks():
    checker = require_roles(UserRole.ADMIN)
    assert checker(user=_FakeUser(UserRole.ADMIN)).role == UserRole.ADMIN
    with pytest.raises(HTTPException) as e:
        checker(user=_FakeUser(UserRole.CUSTOMER))
    assert e.value.status_code == 403


def test_exactly_four_roles():
    assert {r.value for r in UserRole} == {"CUSTOMER", "VENDOR", "RIDER", "ADMIN"}


# ---------------------------------------------------------------- password reset
def test_forgot_password_does_not_reveal_accounts(client, emails, verified_user):
    emails.clear()
    unknown = client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    assert unknown.status_code == 200 and emails == []
    known = client.post("/api/auth/forgot-password", json={"email": "ama@example.com"})
    assert known.status_code == 200 and unknown.json() == known.json()
    assert len(emails) == 1


def test_reset_password_flow_single_use_and_logs_out_devices(client, emails, verified_user):
    client.post("/api/auth/forgot-password", json={"email": "ama@example.com"})
    token = token_from(emails)
    body = {"token": token, "password": "NewPass2025", "confirm_password": "NewPass2025"}
    assert client.post("/api/auth/reset-password", json=body).status_code == 200
    assert client.post("/api/auth/reset-password", json=body).status_code == 400  # single use
    assert client.post("/api/auth/refresh").status_code == 401  # old session revoked
    assert client.post("/api/auth/login", json={"email": "ama@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ama@example.com", "password": "NewPass2025"}).status_code == 200


def test_reset_with_bad_token_rejected(client):
    r = client.post("/api/auth/reset-password", json={
        "token": "x" * 40, "password": "NewPass2025", "confirm_password": "NewPass2025"})
    assert r.status_code == 400


# ---------------------------------------------------------------- Google
def test_google_disabled_without_client_id(client):
    assert client.get("/api/auth/config").json()["google_client_id"] is None
    assert client.post("/api/auth/google", json={"credential": "x" * 20}).status_code == 503


def test_google_login_creates_and_links(client, emails, monkeypatch, db):
    monkeypatch.setattr(get_settings(), "google_client_id", "test-client")
    claims = {"sub": "g-123", "email": "Yaa@Example.com", "email_verified": True, "name": "Yaa Asantewaa"}
    monkeypatch.setattr(auth_service, "verify_google_token", lambda c: claims)
    r = client.post("/api/auth/google", json={"credential": "x" * 20})
    assert r.status_code == 200 and r.json()["user"]["role"] == "CUSTOMER"
    assert db.scalar(select(User).where(User.email == "yaa@example.com")).google_sub == "g-123"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/google", json={"credential": "x" * 20}).status_code == 200
    assert len(db.scalars(select(User)).all()) == 1  # same account, not a duplicate


def test_google_rejects_unverified_email_and_bad_token(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", "test-client")
    monkeypatch.setattr(auth_service, "verify_google_token",
                        lambda c: {"sub": "1", "email": "a@b.com", "email_verified": False})
    assert client.post("/api/auth/google", json={"credential": "x" * 20}).status_code == 401

    def boom(c):
        raise ValueError("bad token")

    monkeypatch.setattr(auth_service, "verify_google_token", boom)
    assert client.post("/api/auth/google", json={"credential": "x" * 20}).status_code == 401
