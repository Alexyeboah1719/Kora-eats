"""Simple in-memory sliding-window rate limiter, keyed by client IP.

DEVELOPMENT/SMALL-DEPLOY NOTE: state lives in one process. With several
workers or servers, move this to Redis (Phase 14). Behind a reverse proxy,
configure it to pass the real client IP.
"""
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request

from app.core.config import get_settings

from app.core.errors import api_error


def client_ip(request: Request) -> str:
    """The caller's IP. X-Forwarded-For is trusted ONLY when TRUSTED_PROXY_HOPS says a proxy of ours sets it
    (otherwise anyone could forge the header and dodge rate limits)."""
    hops = get_settings().trusted_proxy_hops
    xff = request.headers.get("x-forwarded-for")
    if hops > 0 and xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(name: str, limit: int, window_seconds: int):
    """FastAPI dependency factory: Depends(rate_limit("login", 10, 300))."""

    def dependency(request: Request) -> None:
        if not limiter.allow(f"{name}:{client_ip(request)}", limit, window_seconds):
            raise api_error(429, "rate_limited", "Too many attempts. Please wait a few minutes and try again.")

    return dependency
