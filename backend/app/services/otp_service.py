"""Email OTP rules: expiry, attempt limit, resend cooldown, hourly cap."""
import hmac
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import api_error
from app.core.security import generate_otp, hash_otp
from app.core.timeutil import utcnow
from app.models.auth import EmailVerification
from app.models.user import User


def issue_otp(db: Session, user: User) -> str:
    s = get_settings()
    now = utcnow()

    last = db.scalar(
        select(EmailVerification)
        .where(EmailVerification.user_id == user.id)
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if last:
        wait = s.otp_resend_cooldown_seconds - (now - last.created_at).total_seconds()
        if wait > 0:
            raise api_error(429, "otp_cooldown", f"Please wait {int(wait) + 1}s before requesting another code.")

    sent_last_hour = db.scalar(
        select(func.count())
        .select_from(EmailVerification)
        .where(EmailVerification.user_id == user.id, EmailVerification.created_at > now - timedelta(hours=1))
    )
    if sent_last_hour >= s.otp_max_per_hour:
        raise api_error(429, "otp_limit", "Too many codes requested. Try again in an hour.")

    # Only the newest code is valid.
    db.execute(
        update(EmailVerification)
        .where(EmailVerification.user_id == user.id, EmailVerification.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = generate_otp()
    db.add(
        EmailVerification(
            user_id=user.id,
            code_hash=hash_otp(code, user.id),
            expires_at=now + timedelta(minutes=s.otp_expire_minutes),
        )
    )
    db.commit()
    return code


def check_otp(db: Session, user: User, code: str) -> None:
    s = get_settings()
    now = utcnow()
    rec = db.scalar(
        select(EmailVerification)
        .where(EmailVerification.user_id == user.id, EmailVerification.consumed_at.is_(None))
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if rec is None or rec.expires_at < now:
        raise api_error(400, "otp_expired", "This code has expired. Request a new one.")
    if rec.attempts >= s.otp_max_attempts:
        raise api_error(429, "otp_locked", "Too many wrong attempts. Request a new code.")
    if not hmac.compare_digest(rec.code_hash, hash_otp(code, user.id)):
        rec.attempts += 1
        db.commit()
        left = s.otp_max_attempts - rec.attempts
        raise api_error(400, "otp_invalid", f"Incorrect code. {left} attempt(s) left.")
    rec.consumed_at = now
    db.flush()
