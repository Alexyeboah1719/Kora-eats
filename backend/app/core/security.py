"""Cryptographic helpers: Argon2 passwords, JWT access tokens, OTP + opaque tokens."""
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.timeutil import utcnow

_ph = PasswordHasher()  # Argon2id with library-recommended parameters
# Verified against when the email doesn't exist, so login takes similar time
# either way (prevents account enumeration by timing).
DUMMY_HASH = _ph.hash("not-a-real-password")


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(password_hash: str, plain: str) -> bool:
    try:
        return _ph.verify(password_hash, plain)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    s = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("type") == "access" else None


def generate_opaque_token() -> str:
    """Random token for refresh sessions and password-reset links."""
    return secrets.token_urlsafe(48)


def sha256(value: str) -> str:
    """Opaque tokens are high-entropy, so a fast hash is enough. Only the hash is stored."""
    return hashlib.sha256(value.encode()).hexdigest()


def generate_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_otp(code: str, user_id: uuid.UUID) -> str:
    """6-digit codes are guessable, so use HMAC with the server secret (not plain SHA)."""
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()
