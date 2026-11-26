"""Sends transactional email through Resend's REST API (POST https://api.resend.com/emails).

DEVELOPMENT PLACEHOLDER: when RESEND_API_KEY is empty, the email is written to
the server log instead of being sent, so you can copy OTP codes/reset links
while developing. PRODUCTION REQUIRES: a Resend API key and a verified sending
domain set in EMAIL_FROM.
"""
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


def send_email(to: str, subject: str, html: str) -> bool:
    settings = get_settings()
    if not settings.resend_api_key:
        if settings.environment == "production":      # never write OTP codes / reset links to production logs
            logger.error("RESEND_API_KEY is not set: email to %s was NOT sent", to)
            return False
        logger.warning("[DEV EMAIL - not sent] to=%s subject=%s\n%s", to, subject, html)
        return False
    try:
        r = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPError:
        # Never crash a request because email failed; the user can resend.
        logger.exception("Failed to send email to %s", to)
        return False
