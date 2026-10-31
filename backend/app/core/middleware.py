"""Cross-cutting HTTP protections, installed once in main.py:
request IDs + access log, global rate limit, CSRF origin check, security headers (incl. CSP), cache rules."""
import logging
import time
import uuid
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.rate_limit import client_ip, limiter

logger = logging.getLogger("app.access")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def csp_policy() -> str:
    """Only our own files plus the CDNs/services this app really uses."""
    return "; ".join([
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net https://accounts.google.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://accounts.google.com",
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net",
        "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://*.googleusercontent.com",
        "connect-src 'self' https://accounts.google.com",
        "frame-src https://accounts.google.com",
        "worker-src 'self'", "manifest-src 'self'", "object-src 'none'",
        "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
        "report-uri /api/csp-report",
    ])


def origin_allowed(request: Request) -> bool:
    """CSRF defence in depth (cookies are already SameSite=Lax): a browser-made write request must come
    from our own site or an allow-listed origin. Server-to-server calls (webhooks) send no Origin header."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    if origin == "null":
        return False
    return urlparse(origin).netloc == request.headers.get("host") or origin in get_settings().cors_origins


def install(app: FastAPI) -> None:
    settings = get_settings()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):          # innermost
        response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = "geolocation=(self), camera=(self), microphone=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"   # keeps Google sign-in popups working
        if settings.environment == "production":
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        mode = settings.csp_mode
        if mode in ("report-only", "enforce") and not request.url.path.startswith("/api/"):
            h["Content-Security-Policy-Report-Only" if mode == "report-only" else "Content-Security-Policy"] = csp_policy()
        if "cache-control" not in h:
            p = request.url.path
            h["Cache-Control"] = ("no-store" if p.startswith("/api/") else
                                  "public, max-age=31536000, immutable" if p.startswith("/uploads/") else
                                  "public, max-age=604800" if p.startswith("/icons/") else
                                  "no-cache")        # pages/js/css/sw.js: always revalidate (cheap 304) so deploys take effect at once
        return response

    @app.middleware("http")
    async def csrf_origin_check(request: Request, call_next):
        if request.method in UNSAFE and request.url.path.startswith("/api/") and not origin_allowed(request):
            return JSONResponse({"detail": {"code": "bad_origin", "message": "Request blocked (cross-site)."}}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def global_rate_limit(request: Request, call_next):
        p = request.url.path
        if p.startswith("/api/") and p != "/api/health" and not limiter.allow(f"api:{client_ip(request)}", settings.api_rate_limit_per_minute, 60):
            return JSONResponse({"detail": {"code": "rate_limited", "message": "Too many requests. Please slow down."}},
                                status_code=429, headers={"Retry-After": "60"})
        return await call_next(request)

    @app.middleware("http")
    async def request_log(request: Request, call_next):                # outermost
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            logger.info("%s %s %s %.0fms rid=%s ip=%s", request.method, request.url.path, response.status_code,
                        (time.perf_counter() - start) * 1000, rid, client_ip(request))
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        """Log the real error with the request id; show the user nothing internal."""
        logger.exception("Unhandled error rid=%s %s %s", getattr(request.state, "request_id", "-"), request.method, request.url.path)
        return JSONResponse({"detail": {"code": "server_error", "message": "Something went wrong on our side. Please try again."}}, status_code=500)
