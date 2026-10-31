"""Fixtures for auth tests. They use a SEPARATE database (foodapp_test), so your
development data is never touched, and they don't alter the Phase 1 fixtures."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services import email_service

from app import models as _models  # noqa: F401  (registers tables on Base)


def _test_url() -> str:
    base = get_settings().database_url.rsplit("/", 1)[0]
    return f"{base}/foodapp_test"


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(_test_url())
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db(test_engine):
    Session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        yield session
    session_tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with test_engine.begin() as conn:  # wipe EVERY table between tests
        conn.execute(text(f"TRUNCATE {session_tables} RESTART IDENTITY CASCADE"))


@pytest.fixture()
def emails(monkeypatch):
    """Captures outgoing emails instead of sending them."""
    sent: list[dict] = []
    monkeypatch.setattr(email_service, "send_email",
                        lambda to, subject, html: sent.append({"to": to, "subject": subject, "html": html}) or True)
    return sent


@pytest.fixture()
def client(db, test_engine, emails, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "otp_resend_cooldown_seconds", 0)  # tests can resend instantly
    limiter.reset()

    Session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override():  # a fresh session per request, exactly like production
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def otp_from(emails: list[dict]) -> str:
    return re.search(r">(\d{6})<", emails[-1]["html"]).group(1)


def token_from(emails: list[dict]) -> str:
    return re.search(r"token=([\w\-]+)", emails[-1]["html"]).group(1)


PASSWORD = "Jollof2024"


@pytest.fixture()
def register(client, emails):
    def _register(email="ama@example.com", name="Ama Mensah", password=PASSWORD):
        return client.post("/api/auth/register", json={
            "full_name": name, "email": email, "password": password, "confirm_password": password})
    return _register


@pytest.fixture()
def verified_user(client, emails, register):
    """Registers + verifies a customer. Leaves the client logged in."""
    register()
    r = client.post("/api/auth/verify-otp", json={"email": "ama@example.com", "code": otp_from(emails)})
    assert r.status_code == 200
    return r.json()["user"]
