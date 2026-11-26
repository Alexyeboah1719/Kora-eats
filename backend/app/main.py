import asyncio
import contextlib
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routers import share
from app.services import event_handlers  # noqa: F401  (registers order-event subscribers)
from app.core.config import get_settings
from app.core import middleware
from app.core.logging import setup_logging
from app.core.uploads import public_dir

settings = get_settings()
mimetypes.add_type("application/manifest+json", ".webmanifest")   # some systems (e.g. Windows) don't know this type
setup_logging(settings.debug)

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    """Starts the background job loop (scheduled orders, payment timeouts, reminders) with the server."""
    tasks = []
    if settings.background_jobs:
        from app.services import scheduler, settings_service
        tasks = [asyncio.create_task(scheduler.loop()), asyncio.create_task(settings_service.refresh_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(api_router)
app.include_router(share.page_router)       # /v/<vendor>, /f/<dish>, /sitemap.xml, /robots.txt (shareable pages)
middleware.install(app)                                    # headers, CSRF origin check, rate limit, request log, error handler
app.add_middleware(GZipMiddleware, minimum_size=1024)   # compress JSON/JS/CSS responses

# Share links like /group/ABC123 serve the group-order page; the page reads the code from the URL.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@app.get("/group/{code}", include_in_schema=False)
def group_page(code: str):
    return FileResponse(FRONTEND_DIR / "group.html")


# Public images (logos, covers, food photos). Private documents are NOT served from here.
app.mount("/uploads", StaticFiles(directory=public_dir()), name="uploads")

# Serve the frontend from the same origin (mounted last so /api/* wins).
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
