from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_health_reports_db_failure():
    class BrokenSession:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def close(self):
            pass

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        r = TestClient(app).get("/api/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json()["database"] == "unreachable"


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "KORA EATS" in r.text


def test_unknown_api_route_404(client):
    assert client.get("/api/nope").status_code == 404
