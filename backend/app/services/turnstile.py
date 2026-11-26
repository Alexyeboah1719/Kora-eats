"""Cloudflare Turnstile bot check on the auth endpoints.

Disabled (a no-op) until TURNSTILE_SECRET_KEY is set, so dev environments don't need it.
The widget's site key is public and served via /api/auth/config; the secret never leaves the server."""
import logging

import httpx

from app.core.config import get_settings
from app.core.errors import api_error

logger = logging.getLogger(__name__)
_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def require(token: str | None, ip: str | None = None) -> None:
    """Raise unless Cloudflare confirms the token. No-op when Turnstile isn't configured."""
    secret = get_settings().turnstile_secret_key
    if not secret:
        return
    if not token:
        raise api_error(403, "human_check_failed", "Please complete the security check and try again.")
    try:
        r = httpx.post(_VERIFY_URL, data={"secret": secret, "response": token, "remoteip": ip or ""}, timeout=10)
        ok = bool(r.json().get("success"))
    except Exception:
        logger.exception("turnstile siteverify failed")
        raise api_error(503, "human_check_unavailable", "The security check is unavailable. Please try again in a moment.")
    if not ok:
        raise api_error(403, "human_check_failed", "Security check failed. Please try again.")
