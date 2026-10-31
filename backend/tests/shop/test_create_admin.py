import sys

import pytest
from sqlalchemy import select

from app.models.user import User, UserRole
from scripts import create_admin as ca


def run(monkeypatch, db, args, *passwords):
    answers = iter(passwords)
    monkeypatch.setattr(sys, "argv", ["create_admin", *args])
    monkeypatch.setattr(ca.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(ca, "SessionLocal", lambda: __import__("contextlib").nullcontext(db))      # use the test database
    ca.main()


def test_creates_an_admin_who_can_log_in(monkeypatch, db, client):
    run(monkeypatch, db, ["owner@kora.example", "Ama Owner"], "Correct horse battery 7", "Correct horse battery 7")
    u = db.scalar(select(User).where(User.email == "owner@kora.example"))
    assert u.role == UserRole.ADMIN and u.is_email_verified and u.is_active
    r = client.post("/api/auth/login", json={"email": "owner@kora.example", "password": "Correct horse battery 7"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "ADMIN" and client.get("/api/admin/overview").status_code == 200


@pytest.mark.parametrize("p1,p2,message", [("Shortish12", "Shortish12", "at least 12"), ("password-only-letters", "password-only-letters", "letter and one number"),
                                           ("Correct horse battery 7", "Correct horse battery 8", "do not match")])
def test_weak_or_mismatched_passwords_are_refused(monkeypatch, db, p1, p2, message):
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, db, ["owner@kora.example", "Ama Owner"], p1, p2)
    assert message in str(e.value) and db.scalar(select(User.id).where(User.email == "owner@kora.example")) is None


def test_rerunning_resets_the_password_and_promotes_an_existing_user(monkeypatch, db, client, make_user):
    make_user("staff@kora.example", name="Staff Member")                                                 # a normal customer account
    run(monkeypatch, db, ["staff@kora.example", "Staff Member"], "First long passphrase 1", "First long passphrase 1")
    run(monkeypatch, db, ["staff@kora.example", "Staff Member"], "Second long passphrase 2", "Second long passphrase 2")   # recovery
    assert client.post("/api/auth/login", json={"email": "staff@kora.example", "password": "First long passphrase 1"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "staff@kora.example", "password": "Second long passphrase 2"}).json()["user"]["role"] == "ADMIN"


def test_the_website_can_never_create_an_admin(client, db):
    body = {"email": "sneaky@example.com", "password": "Str0ng!Pass-2026", "confirm_password": "Str0ng!Pass-2026", "full_name": "Sneaky Person"}
    for extra in ({}, {"role": "ADMIN"}, {"is_admin": True}):
        client.post("/api/auth/register", json={**body, **extra})
        u = db.scalar(select(User).where(User.email == "sneaky@example.com"))
        assert u.role == UserRole.CUSTOMER
        db.delete(u); db.commit()
