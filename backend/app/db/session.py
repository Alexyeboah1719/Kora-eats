from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_s = get_settings()
# pool_pre_ping drops dead connections; pool_recycle refreshes them before a serverless host (Neon) closes them.
engine = create_engine(_s.database_url, pool_pre_ping=True, pool_size=_s.db_pool_size, max_overflow=_s.db_max_overflow, pool_recycle=300)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one DB session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
