import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.catalog import Category, Vendor
from app.models.orders import Order, OrderStatus as S
from app.models.user import User, UserRole
from app.services import settings_service
from tests.shop.test_notifications import advance_to, titles


@pytest.fixture()
def admin(make_user, login):
    u = make_user("admin@example.com", UserRole.ADMIN, "Site Admin")
    c = login(u); c.user = u
    return c


ADMIN_GETS = ["/api/admin/overview", "/api/admin/users", "/api/admin/orders", "/api/admin/payments", "/api/admin/categories",
              "/api/admin/reviews", "/api/admin/support", "/api/admin/audit", "/api/admin/settings", "/api/admin/reports/sales.csv",
              "/api/admin/coupons", "/api/admin/promotions", "/api/admin/vendors", "/api/admin/riders"]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_every_admin_endpoint_is_locked(path, client, shopper, vendor_user, login, admin):
    assert client.get(path).status_code == 401                         # not logged in
    assert shopper.get(path).status_code == 403                         # customer
    assert login(vendor_user).get(path).status_code == 403              # vendor
    assert admin.get(path).status_code == 200                           # admin


# ---------------------------------------------------------------- dashboard numbers
def test_overview_counts_and_charts(world, shopper, place_order, admin, db):
    paid = place_order(qty=2)                      # 45*2 + 5 = 95
    advance_to(db, paid["id"], S.DELIVERED)
    unpaid = place_order()                          # stays PENDING_PAYMENT: must not count as revenue
    cancelled = place_order(); advance_to(db, cancelled["id"], S.PAID)
    order = db.get(Order, cancelled["id"]); order.status = S.CANCELLED; db.commit()
    d = admin.get("/api/admin/overview").json()
    c = d["cards"]
    assert c["total_revenue"] == 95.0 and c["today_revenue"] == 95.0
    assert c["total_orders"] == 2 and c["cancelled_orders"] == 1 and c["pending_orders"] == 0
    assert c["active_vendors"] == 2 and c["customers"] >= 1 and c["pending_vendor_approvals"] == 0
    ch = d["charts"]
    assert len(ch["revenue"]["labels"]) == len(ch["revenue"]["values"]) == 30 and ch["revenue"]["values"][-1] == 95.0
    assert ch["orders"]["values"][-1] == 2
    assert ch["popular_foods"]["labels"][0] == "Spicy Jollof Chicken" and ch["popular_foods"]["values"][0] == 2
    assert ch["popular_vendors"]["labels"] == ["Near Kitchen"]


def test_vendor_analytics(world, shopper, place_order, vendor_user, login, db):
    for _ in range(2):
        advance_to(db, place_order(qty=1)["id"], S.DELIVERED)
    a = login(vendor_user).get("/api/vendor/analytics").json()
    assert a["food_sales"]["total"] == 90.0 and a["orders"]["total"] == 2 and a["average_order_value"] == 45.0
    assert a["popular_foods"][0] == {"name": "Spicy Jollof Chicken", "quantity": 2, "sales": 90.0}
    assert a["repeat_customer_percent"] == 100 and len(a["revenue_by_day"]["values"]) == 30 and len(a["orders_by_hour"]["values"]) == 24
    assert shopper.get("/api/vendor/analytics").status_code in (403, 404)


# ---------------------------------------------------------------- users
def test_user_management(shopper, admin, make_user, db):
    users = admin.get("/api/admin/users", params={"q": "shopper"}).json()
    assert [u["email"] for u in users] == ["shopper@example.com"]
    assert admin.get("/api/admin/users", params={"role": "ADMIN"}).json()[0]["email"] == "admin@example.com"
    uid = users[0]["id"]
    assert admin.patch(f"/api/admin/users/{uid}/active", params={"is_active": False}).status_code == 200
    assert shopper.get("/api/me/profile").status_code == 401                   # deactivated users are locked out at once
    assert admin.patch(f"/api/admin/users/{uid}/active", params={"is_active": True}).status_code == 200
    assert shopper.get("/api/me/profile").status_code == 200
    me = str(admin.user.id)
    assert admin.patch(f"/api/admin/users/{me}/active", params={"is_active": False}).json()["detail"]["code"] == "self"
    other_admin = make_user("admin2@example.com", UserRole.ADMIN)
    assert admin.patch(f"/api/admin/users/{other_admin.id}/active", params={"is_active": False}).status_code == 409
    assert admin.patch("/api/admin/users/not-a-uuid/active", params={"is_active": False}).status_code == 404


# ---------------------------------------------------------------- orders + refunds
def test_admin_orders_and_override(world, shopper, place_order, admin, db):
    o = place_order(); advance_to(db, o["id"], S.PAID)
    assert [x["code"] for x in admin.get("/api/admin/orders", params={"q": o["code"][3:8]}).json()] == [o["code"]]
    assert admin.get("/api/admin/orders", params={"status": "DELIVERED"}).json() == []
    assert admin.get("/api/admin/orders", params={"status": "NOPE"}).status_code == 422
    assert admin.post(f"/api/admin/orders/{o['id']}/status", json={"status": "DELIVERED"}).status_code == 409   # can't skip steps, even for admin
    r = admin.post(f"/api/admin/orders/{o['id']}/status", json={"status": "REFUNDED", "note": "Refunded via MoMo dashboard"})
    assert r.status_code == 200 and r.json()["status"] == "REFUNDED"
    assert "Refund issued" in titles(db, shopper.user)
    assert admin.get(f"/api/admin/orders/{o['id']}").json()["history"][-1]["note"] == "Refunded via MoMo dashboard"
    assert "admin_order_status" in [a["action"] for a in admin.get("/api/admin/audit").json()]


def test_payments_list(shopper, place_order, admin):
    from tests.shop.test_payments import pay
    o = place_order(); pay(shopper, o["id"])
    p = admin.get("/api/admin/payments").json()
    assert p[0]["order_code"] == o["code"] and p[0]["status"] == "PENDING" and p[0]["amount"] == 95.0
    assert admin.get("/api/admin/payments", params={"status": "SUCCESS"}).json() == []


# ---------------------------------------------------------------- categories
def test_category_management(world, admin, client):
    r = admin.post("/api/admin/categories", json={"name": "Desserts", "icon": "bi-cake"})
    assert r.status_code == 201 and r.json()["slug"] == "desserts"
    assert admin.post("/api/admin/categories", json={"name": "desserts"}).status_code == 409
    assert admin.post("/api/admin/categories", json={"name": "X", "icon": "javascript:alert(1)"}).status_code == 422
    cid = r.json()["id"]
    assert admin.put(f"/api/admin/categories/{cid}", json={"name": "Sweet Treats", "is_active": False}).json()["slug"] == "sweet-treats"
    assert "Sweet Treats" not in [c["name"] for c in client.get("/api/categories").json()]          # hidden when off
    assert admin.delete(f"/api/admin/categories/{world.jollof.id}").json()["detail"]["code"] == "in_use"
    assert admin.delete(f"/api/admin/categories/{cid}").status_code == 200


# ---------------------------------------------------------------- reviews
def test_review_moderation_updates_rating(world, shopper, place_order, admin, db):
    o = place_order(); advance_to(db, o["id"], S.DELIVERED)
    rid = shopper.post("/api/reviews", json={"order_id": o["id"], "food_rating": 1, "vendor_rating": 1, "comment": "rude and awful"}).json()["id"]
    db.refresh(world.near)
    assert world.near.rating_avg == 1.0
    listed = admin.get("/api/admin/reviews", params={"sentiment": "negative"}).json()
    assert listed[0]["vendor"] == "Near Kitchen" and listed[0]["customer_email"] == "shopper@example.com"
    assert admin.post(f"/api/admin/reviews/{rid}/hide", params={"hidden": True}).status_code == 200
    db.expire_all()
    assert db.get(Vendor, world.near.id).rating_count == 0
    assert shopper.get(f"/api/vendors/{world.near.id}/reviews").json() == []


# ---------------------------------------------------------------- support
def test_support_ticket_flow(world, shopper, place_order, admin, make_user, login, db, emails):
    o = place_order()
    assert shopper.post("/api/support/tickets", json={"subject": "Missing item", "message": "My order lacked a drink", "order_id": o["id"]}).status_code == 201
    other = login(make_user("o@example.com"))
    assert other.post("/api/support/tickets", json={"subject": "Hack", "message": "not my order", "order_id": o["id"]}).status_code == 404
    assert shopper.post("/api/support/tickets", json={"subject": "x", "message": "y"}).status_code == 422
    t = admin.get("/api/admin/support", params={"status": "OPEN"}).json()
    assert t[0]["order_code"] == o["code"] and t[0]["email"] == "shopper@example.com"
    assert admin.post(f"/api/admin/support/{t[0]['id']}/reply", json={"reply": "Sorry! We refunded the drink."}).status_code == 200
    mine = shopper.get("/api/support/tickets").json()[0]
    assert mine["status"] == "RESOLVED" and "refunded the drink" in mine["admin_reply"]
    assert "Support replied to you" in titles(db, shopper.user) and emails[-1]["subject"] == "Reply from KORA EATS support"
    assert other.get("/api/support/tickets").json() == []


# ---------------------------------------------------------------- broadcast
def test_broadcast_targets_audience_and_validates_link(world, shopper, vendor_user, admin, db):
    r = admin.post("/api/admin/notifications/broadcast", json={"title": "Weekend deal", "body": "20% off jollof", "url": "/search.html", "audience": "CUSTOMERS"})
    assert r.json()["sent_to"] == 1 and "Weekend deal" in titles(db, shopper.user) and titles(db, vendor_user) == []
    assert admin.post("/api/admin/notifications/broadcast", json={"title": "Hello", "body": "Hello all", "audience": "ALL"}).json()["sent_to"] >= 3
    for bad in ("https://evil.com", "//evil.com", "javascript:alert(1)"):
        assert admin.post("/api/admin/notifications/broadcast", json={"title": "Hello", "body": "Hello all", "url": bad}).status_code == 422
    assert admin.post("/api/admin/notifications/broadcast", json={"title": "Hello", "body": "Hello", "audience": "EVERYONE"}).status_code == 422


# ---------------------------------------------------------------- reports
def test_csv_reports_and_formula_injection_guard(world, shopper, place_order, admin, db):
    world.near.name = "=HYPERLINK(\"http://evil\")"; db.commit()
    advance_to(db, place_order()["id"], S.DELIVERED)
    r = admin.get("/api/admin/reports/sales.csv")
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("Order,Date,Status,Vendor") and "'=HYPERLINK" in lines[1]       # neutralised, not executable
    assert "Spend" not in r.text and admin.get("/api/admin/reports/vendors.csv").status_code == 200
    assert admin.get("/api/admin/reports/payments.csv").status_code == 200
    assert admin.get("/api/admin/reports/secrets.csv").status_code == 404


# ---------------------------------------------------------------- settings
def test_settings_change_delivery_pricing(world, shopper, admin, db, monkeypatch):
    s = get_settings()
    for k in ("delivery_base_fee", "rider_share_percent"):
        monkeypatch.setattr(s, k, getattr(s, k))                                # restore after the test
    keys = {x["key"] for x in admin.get("/api/admin/settings").json()}
    assert {"delivery_base_fee", "rider_share_percent", "order_payment_timeout_minutes"} <= keys
    assert admin.put("/api/admin/settings/delivery_base_fee", json={"value": 8}).status_code == 200
    shopper.post("/api/cart/items", json={"menu_item_id": world.spicy.id})
    assert shopper.get("/api/cart", params={"address_id": shopper.address_id}).json()["delivery_fee"] == 8.0   # takes effect immediately
    assert admin.put("/api/admin/settings/rider_share_percent", json={"value": 150}).json()["detail"]["code"] == "out_of_range"
    assert admin.put("/api/admin/settings/secret_key", json={"value": 1}).json()["detail"]["code"] == "unknown_setting"
    monkeypatch.setattr(s, "delivery_base_fee", 5.0)
    assert settings_service.load_overrides(db) >= 1 and s.delivery_base_fee == 8.0                              # survives restarts
    assert admin.put("/api/admin/settings/delivery_base_fee", json={"value": 5}).status_code == 200
