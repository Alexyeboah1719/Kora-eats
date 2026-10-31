import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.catalog import Vendor
from scripts import seed_dev_data as sd


def test_demo_accounts_work_and_have_the_right_powers(client, db):
    sd.seed(db)
    made = sd.seed_demo_users(db)
    assert len(made) == 4 and sd.seed_demo_users(db) == []                                         # idempotent
    for email, _, role in sd.DEMO_USERS:
        r = client.post("/api/auth/login", json={"email": email, "password": sd.DEMO_PASSWORD})
        assert r.status_code == 200 and r.json()["user"]["role"] == role.value, (email, r.text)
    vendor = client.post("/api/auth/login", json={"email": "vendor@demo.kora", "password": sd.DEMO_PASSWORD})
    assert vendor.status_code == 200 and client.get("/api/vendor/me").json()["name"] == "Auntie Ama's Kitchen"
    client.post("/api/auth/login", json={"email": "rider@demo.kora", "password": sd.DEMO_PASSWORD})
    me = client.get("/api/rider/me").json()
    assert me["approval_status"] == "APPROVED" and me["is_online"] is True
    client.post("/api/auth/login", json={"email": "admin@demo.kora", "password": sd.DEMO_PASSWORD})
    assert client.get("/api/admin/ai/overview").status_code == 200
    assert db.scalar(select(Vendor.owner_user_id).where(Vendor.name == "Auntie Ama's Kitchen")) is not None


def test_demo_data_refuses_to_run_in_production(monkeypatch, db, capsys):
    monkeypatch.setattr(get_settings(), "environment", "production")
    with pytest.raises(SystemExit) as e:
        sd.main(["--demo-users"])
    assert "Refusing" in str(e.value)
    from app.models.user import User
    assert db.scalar(select(User.id).where(User.email == "admin@demo.kora")) is None                 # nothing was created
