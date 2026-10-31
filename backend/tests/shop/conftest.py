"""Shared helpers for all Phase 3+ tests: users, logged-in clients, sample world."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.catalog import ApprovalStatus, Category, MenuItem, Vendor
from app.models.user import User, UserProfile, UserRole
from tests.auth.conftest import client, db, emails, register, test_engine, verified_user  # noqa: F401  (reuse DB fixtures)


@pytest.fixture()
def make_user(db):
    def _make(email="cust@example.com", role=UserRole.CUSTOMER, name="Kofi Mensah"):
        u = User(email=email, password_hash=hash_password("Jollof2024"), role=role, is_email_verified=True,
                 is_active=True, profile=UserProfile(full_name=name))
        db.add(u)
        db.commit()
        return u
    return _make


@pytest.fixture()
def login(client):
    """login(user) -> a TestClient authenticated as that user (cookie set directly, no OTP dance)."""
    def _login(user):
        c = TestClient(app)
        c.cookies.set("access_token", create_access_token(user.id, user.role.value))
        return c
    return _login


@pytest.fixture()
def world(db):
    """Two vendors in Accra with a few items. Returns a simple namespace."""
    jollof = Category(name="Jollof & Rice", slug="jollof", icon="bi-egg-fried")
    drinks = Category(name="Drinks", slug="drinks", icon="bi-cup-straw")
    db.add_all([jollof, drinks])
    db.flush()
    near = Vendor(name="Near Kitchen", latitude=5.5560, longitude=-0.1830, category_id=jollof.id, rating_avg=4.6,
                  rating_count=10, approval_status=ApprovalStatus.APPROVED, address_text="Osu", avg_prep_minutes=20)
    far = Vendor(name="Far Grill", latitude=5.6700, longitude=-0.0100, category_id=jollof.id, rating_avg=4.0,
                 rating_count=5, approval_status=ApprovalStatus.APPROVED, address_text="Tema", avg_prep_minutes=30)
    db.add_all([near, far])
    db.flush()
    spicy = MenuItem(vendor_id=near.id, category_id=jollof.id, name="Spicy Jollof Chicken", price=Decimal("45.00"),
                     is_spicy=True, tags="filling,chicken,rice,spicy", prep_minutes=20, order_count=9)
    plain = MenuItem(vendor_id=near.id, category_id=jollof.id, name="Plain Rice", price=Decimal("20.00"),
                     tags="rice,budget", prep_minutes=10, order_count=2)
    cola = MenuItem(vendor_id=near.id, category_id=drinks.id, name="Sobolo", price=Decimal("10.00"), tags="drink",
                    prep_minutes=2)
    gone = MenuItem(vendor_id=near.id, category_id=jollof.id, name="Sold Out Special", price=Decimal("30.00"),
                    is_available=False, prep_minutes=10)
    farfood = MenuItem(vendor_id=far.id, category_id=jollof.id, name="Far Chicken Rice", price=Decimal("40.00"),
                       tags="filling,chicken,rice", prep_minutes=25)
    db.add_all([spicy, plain, cola, gone, farfood])
    db.commit()
    class W: pass
    w = W()
    w.near, w.far, w.spicy, w.plain, w.cola, w.gone, w.farfood = near, far, spicy, plain, cola, gone, farfood
    w.jollof, w.drinks = jollof, drinks
    return w

OSU = {"lat": 5.5570, "lng": -0.1820}  # a point ~150m from "Near Kitchen"


@pytest.fixture()
def vendor_user(db, world, make_user):
    """An approved VENDOR who owns world.near."""
    u = make_user("vendor@example.com", UserRole.VENDOR, "Ama Vendor")
    world.near.owner_user_id = u.id
    db.commit()
    return u


@pytest.fixture()
def shopper(make_user, login):
    """A customer client with a saved address near Osu and a phone number."""
    u = make_user("shopper@example.com", name="Yaw Shopper")
    c = login(u)
    c.put("/api/me/profile", json={"full_name": "Yaw Shopper", "phone": "+233244000111"})
    a = c.post("/api/me/addresses", json={"label": "Home", "address_line": "5 Osu Ave", "latitude": 5.557, "longitude": -0.182}).json()
    c.address_id = a["id"]
    c.user = u
    return c


@pytest.fixture()
def place_order(world, shopper):
    """place_order(item=..., qty=...) -> order JSON (status PENDING_PAYMENT)."""
    def _place(item=None, qty=2):
        shopper.post("/api/cart/items", json={"menu_item_id": (item or world.spicy).id, "quantity": qty})
        r = shopper.post("/api/orders", json={"address_id": shopper.address_id})
        assert r.status_code == 201, r.text
        return r.json()
    return _place


@pytest.fixture()
def make_rider(db, make_user):
    """make_rider(email) -> (user, DeliveryRider) already approved and online."""
    from app.models.catalog import ApprovalStatus as A
    from app.models.riders import DeliveryRider, VehicleType

    def _make(email="rider@example.com", online=True, name="Kwesi Rider"):
        u = make_user(email, UserRole.RIDER, name)
        r = DeliveryRider(user_id=u.id, phone="+233201112233", vehicle_type=VehicleType.MOTORBIKE, plate_number="GR-123-24",
                          id_document="x.png", id_document_name="id.png", approval_status=A.APPROVED, is_online=online)
        db.add(r)
        db.commit()
        return u, r
    return _make


@pytest.fixture()
def ready_order(db, place_order):
    """An order that is READY_FOR_PICKUP (paid, confirmed, prepared)."""
    from app.models.orders import Order, OrderStatus as S
    from app.services import order_service as svc

    def _ready(**kw):
        o = place_order(**kw)
        order = svc.lock_order(db, o["id"])
        for status, role in [(S.PAID, "system"), (S.CONFIRMED, "vendor"), (S.PREPARING, "vendor"), (S.READY_FOR_PICKUP, "vendor")]:
            svc.change_status(db, order, status, role)
        db.commit()
        return o
    return _ready
