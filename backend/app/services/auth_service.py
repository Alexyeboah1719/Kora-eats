"""Authentication business logic. Routers call these functions; they never
touch cookies or HTTP responses (that stays in the router layer)."""
import logging
from datetime import timedelta

from fastapi import BackgroundTasks
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import api_error
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    generate_opaque_token,
    hash_password,
    password_needs_rehash,
    sha256,
    verify_password,
)
from app.core.timeutil import utcnow
from app.models.auth import PasswordResetToken, RefreshToken
from app.models.user import User, UserProfile, UserRole
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UserOut,
    VerifyOtpRequest,
)
from app.services import audit_service, email_service, email_templates, otp_service

logger = logging.getLogger(__name__)
Session_ = tuple[User, str, str]  # (user, access_token, refresh_token)


def to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.profile.full_name,
        role=user.role.value,
        is_email_verified=user.is_email_verified,
    )


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _send(background: BackgroundTasks, to: str, template: tuple[str, str]) -> None:
    subject, html = template
    # Looked up at call time via the module so tests can replace send_email.
    background.add_task(email_service.send_email, to, subject, html)


def _queue_otp(db: Session, user: User, background: BackgroundTasks) -> None:
    code = otp_service.issue_otp(db, user)
    _send(background, user.email, email_templates.otp_email(user.profile.full_name, code))


def _create_session(db: Session, user: User, user_agent: str | None) -> Session_:
    s = get_settings()
    refresh = generate_opaque_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=sha256(refresh),
            expires_at=utcnow() + timedelta(days=s.refresh_token_days),
            user_agent=(user_agent or "")[:255] or None,
        )
    )
    db.commit()
    return user, create_access_token(user.id, user.role.value), refresh


def _revoke_all_sessions(db: Session, user_id) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


# ---------------------------------------------------------------- register / OTP
def register(db: Session, data: RegisterRequest, background: BackgroundTasks, ip: str | None) -> None:
    user = get_user_by_email(db, data.email)
    if user:
        if user.is_email_verified:
            raise api_error(409, "email_taken", "An account with this email already exists. Try logging in.")
        # Unverified leftover: let the real owner start over.
        user.password_hash = hash_password(data.password)
        user.profile.full_name = data.full_name
    else:
        user = User(
            email=data.email,
            password_hash=hash_password(data.password),
            role=UserRole.CUSTOMER,  # public sign-up can ONLY create customers
            profile=UserProfile(full_name=data.full_name),
        )
        db.add(user)
    db.flush()
    audit_service.record(db, "register", user.id, ip)
    db.commit()
    _queue_otp(db, user, background)


def verify_otp(db: Session, data: VerifyOtpRequest, background: BackgroundTasks, ip, ua) -> Session_:
    user = get_user_by_email(db, data.email)
    if user is None:
        raise api_error(400, "otp_invalid", "Incorrect or expired code.")
    if user.is_email_verified:
        raise api_error(400, "already_verified", "This email is already verified. Please log in.")
    otp_service.check_otp(db, user, data.code)
    user.is_email_verified = True
    audit_service.record(db, "email_verified", user.id, ip)
    db.commit()
    _send(background, user.email, email_templates.welcome_email(user.profile.full_name))
    return _create_session(db, user, ua)


def resend_otp(db: Session, email: str, background: BackgroundTasks) -> None:
    user = get_user_by_email(db, email)
    if user and not user.is_email_verified:
        _queue_otp(db, user, background)


# ---------------------------------------------------------------- login / sessions
def login(db: Session, data: LoginRequest, background: BackgroundTasks, ip, ua) -> Session_:
    user = get_user_by_email(db, data.email)
    # Always run one Argon2 verify so response time doesn't reveal if the email exists.
    ok = verify_password(user.password_hash if user and user.password_hash else DUMMY_HASH, data.password)
    if not user or not ok:
        audit_service.record(db, "login_failed", user.id if user else None, ip)
        db.commit()
        raise api_error(401, "invalid_credentials", "Incorrect email or password.")
    if not user.is_active:
        raise api_error(403, "account_disabled", "This account has been disabled. Contact support.")
    if not user.is_email_verified:
        try:
            _queue_otp(db, user, background)
        except Exception:  # cooldown/limit: the earlier code is still usable
            pass
        raise api_error(403, "email_not_verified", "Please verify your email. We've sent you a code.")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)
    audit_service.record(db, "login", user.id, ip)
    return _create_session(db, user, ua)


def refresh(db: Session, raw_token: str | None, ua) -> Session_:
    if not raw_token:
        raise api_error(401, "not_authenticated", "Please log in.")
    rec = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256(raw_token)))
    if rec is None:
        raise api_error(401, "not_authenticated", "Please log in.")
    if rec.revoked_at is not None:
        # A rotated-out token was presented again: possible theft. Kill all sessions.
        _revoke_all_sessions(db, rec.user_id)
        audit_service.record(db, "refresh_reuse_detected", rec.user_id)
        db.commit()
        raise api_error(401, "not_authenticated", "Session expired. Please log in again.")
    user = db.get(User, rec.user_id)
    if rec.expires_at < utcnow() or user is None or not user.is_active:
        raise api_error(401, "not_authenticated", "Session expired. Please log in again.")
    rec.revoked_at = utcnow()  # rotation: each refresh token works once
    return _create_session(db, user, ua)


def logout(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == sha256(raw_token), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    db.commit()


# ---------------------------------------------------------------- Google
def verify_google_token(credential: str) -> dict:
    """Verifies signature, audience and expiry with Google's public keys."""
    return google_id_token.verify_oauth2_token(
        credential, google_requests.Request(), get_settings().google_client_id
    )


def google_login(db: Session, credential: str, ip, ua) -> Session_:
    if not get_settings().google_client_id:
        raise api_error(503, "google_not_configured", "Google sign-in is not configured.")
    try:
        claims = verify_google_token(credential)
    except ValueError:
        raise api_error(401, "invalid_google_token", "Google sign-in failed. Please try again.")
    if not claims.get("email_verified"):
        raise api_error(401, "google_email_unverified", "Your Google email is not verified.")

    sub, email = claims["sub"], claims["email"].lower()
    name = claims.get("name") or email.split("@")[0]
    user = db.scalar(select(User).where(User.google_sub == sub)) or get_user_by_email(db, email)
    if user is None:
        user = User(
            email=email, google_sub=sub, is_email_verified=True, is_active=True, role=UserRole.CUSTOMER,
            profile=UserProfile(full_name=name[:100]),
        )
        db.add(user)
    else:
        user.google_sub = user.google_sub or sub
        user.is_email_verified = True  # Google has verified this address
    if not user.is_active:
        raise api_error(403, "account_disabled", "This account has been disabled. Contact support.")
    db.flush()
    audit_service.record(db, "login_google", user.id, ip)
    return _create_session(db, user, ua)


# ---------------------------------------------------------------- password reset
def forgot_password(db: Session, data: ForgotPasswordRequest, background: BackgroundTasks, ip) -> None:
    """Silent when the email is unknown, so attackers can't discover accounts."""
    user = get_user_by_email(db, data.email)
    if user is None or not user.is_active:
        return
    now = utcnow()
    recent = db.scalar(
        select(PasswordResetToken.id).where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.created_at > now - timedelta(seconds=60)
        )
    )
    if recent:
        return
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = generate_opaque_token()
    s = get_settings()
    db.add(
        PasswordResetToken(
            user_id=user.id, token_hash=sha256(token), expires_at=now + timedelta(minutes=s.reset_token_minutes)
        )
    )
    audit_service.record(db, "password_reset_requested", user.id, ip)
    db.commit()
    link = f"{s.frontend_base_url}/auth/reset-password.html?token={token}"
    _send(background, user.email, email_templates.password_reset_email(user.profile.full_name, link))


def reset_password(db: Session, data: ResetPasswordRequest, ip) -> None:
    rec = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == sha256(data.token)))
    if rec is None or rec.used_at is not None or rec.expires_at < utcnow():
        raise api_error(400, "invalid_reset_token", "This reset link is invalid or has expired.")
    user = db.get(User, rec.user_id)
    user.password_hash = hash_password(data.password)
    user.is_email_verified = True  # they proved ownership of the inbox
    rec.used_at = utcnow()
    _revoke_all_sessions(db, user.id)  # log out every device
    audit_service.record(db, "password_reset", user.id, ip)
    db.commit()
