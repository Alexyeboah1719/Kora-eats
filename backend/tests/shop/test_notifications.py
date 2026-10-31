from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.models.notifications import Notification, PushSubscription
from app.models.orders import Order, OrderStatus as S
from app.models.reviews import Review
from app.services import notification_service as ns, order_service as svc, review_service


def advance_to(db, order_id, target):
    """Walk an order through the real state machine up to `target`."""
    path = [(S.PAID, "system"), (S.CONFIRMED, "vendor"), (S.PREPARING, "vendor"), (S.READY_FOR_PICKUP, "vendor"),
            (S.RIDER_ASSIGNED, "rider"), (S.PICKED_UP, "rider"), (S.OUT_FOR_DELIVERY, "rider"), (S.DELIVERED, "rider")]
    o = svc.lock_order(db, order_id)
    for status, role in path:
        svc.change_status(db, o, status, role)
        if status == target:
            break
    db.commit()
    return o


def titles(db, user):
    return [n.title for n in db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id))]


# ---------------------------------------------------------------- notifications follow the order
def test_order_lifecycle_notifies_the_right_people(world, shopper, vendor_user, make_rider, place_order, db, emails):
    rider_user, _ = make_rider()
    o = place_order()
    assert titles(db, shopper.user) == ["Order placed"]
    advance_to(db, o["id"], S.PAID)
    assert "Payment successful" in titles(db, shopper.user) and titles(db, vendor_user) == ["New order!"]
    assert any(e["subject"] == "Payment received" and e["to"] == "shopper@example.com" for e in emails)   # payment confirmation email
    advance_to_next = lambda: None
    o2 = svc.lock_order(db, o["id"])
    for status, role in [(S.CONFIRMED, "vendor"), (S.PREPARING, "vendor"), (S.READY_FOR_PICKUP, "vendor")]:
        svc.change_status(db, o2, status, role)
    db.commit()
    assert "New delivery available" in titles(db, rider_user)                                    # online riders are alerted
    for status, role in [(S.RIDER_ASSIGNED, "rider"), (S.PICKED_UP, "rider"), (S.OUT_FOR_DELIVERY, "rider"), (S.DELIVERED, "rider")]:
        svc.change_status(db, o2, status, role)
    db.commit()
    assert titles(db, shopper.user) == ["Order placed", "Payment successful", "Order accepted", "Your food is being prepared",
                                        "Your food is ready", "Rider assigned", "Order picked up", "Out for delivery", "Order delivered"]
    subjects = [e["subject"] for e in emails]
    assert "Order confirmed" in subjects and "Your order was delivered" in subjects


def test_cancel_and_failure_notifications(world, shopper, place_order, db):
    o = place_order()
    shopper.post(f"/api/orders/{o['id']}/cancel", json={"reason": "Changed my mind"})
    assert "Order cancelled" in titles(db, shopper.user)


# ---------------------------------------------------------------- notifications API
def test_notification_api(shopper, place_order, db, make_user, login):
    place_order()
    n = shopper.get("/api/notifications").json()
    assert len(n) == 1 and n[0]["is_read"] is False and n[0]["url"].startswith("/order.html")
    assert shopper.get("/api/notifications/unread-count").json() == {"count": 1}
    other = login(make_user("other@example.com"))
    assert other.post(f"/api/notifications/{n[0]['id']}/read").status_code == 404            # can't touch others'
    assert other.get("/api/notifications").json() == []
    assert shopper.post(f"/api/notifications/{n[0]['id']}/read").status_code == 200
    assert shopper.get("/api/notifications/unread-count").json() == {"count": 0}
    place_order()
    assert shopper.post("/api/notifications/read-all").status_code == 200
    assert shopper.get("/api/notifications", params={"unread_only": True}).json() == []


# ---------------------------------------------------------------- web push
SUB = {"endpoint": "https://push.example.com/abc123456", "keys": {"p256dh": "k" * 40, "auth": "a" * 20}}


def test_push_subscription_api(client, shopper, db):
    assert client.get("/api/notifications/push/public-key").json() == {"public_key": None}      # not configured
    assert shopper.post("/api/notifications/push/subscribe", json=SUB).status_code == 200
    assert shopper.post("/api/notifications/push/subscribe", json=SUB).status_code == 200         # idempotent
    assert len(db.scalars(select(PushSubscription)).all()) == 1
    assert shopper.post("/api/notifications/push/subscribe", json={**SUB, "endpoint": "http://insecure"}).status_code == 422
    assert client.post("/api/notifications/push/subscribe", json=SUB).status_code == 401
    shopper.post("/api/notifications/push/unsubscribe", params={"endpoint": SUB["endpoint"]})
    assert db.scalars(select(PushSubscription)).all() == []


def test_push_is_logged_without_vapid_and_sent_with_it(shopper, db, monkeypatch, caplog):
    shopper.post("/api/notifications/push/subscribe", json=SUB)
    subs = [{"endpoint": SUB["endpoint"], "p256dh": "k" * 40, "auth": "a" * 20}]
    with caplog.at_level("WARNING"):
        ns._send_push(subs, {"title": "Hi"})
    assert "DEV PUSH - not sent" in caplog.text
    sent = []
    import pywebpush
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: sent.append(kw["data"]))
    monkeypatch.setattr(get_settings(), "vapid_private_key", "x" * 43)
    ns._send_push(subs, {"title": "Hi"})
    assert sent and "Hi" in sent[0]


def test_expired_push_subscription_is_removed(shopper, db, test_engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import pywebpush

    class Gone(pywebpush.WebPushException):
        def __init__(self):
            self.response = type("R", (), {"status_code": 410})()

    def boom(**kw):
        raise Gone()

    shopper.post("/api/notifications/push/subscribe", json=SUB)
    monkeypatch.setattr(pywebpush, "webpush", boom)
    monkeypatch.setattr(get_settings(), "vapid_private_key", "x" * 43)
    monkeypatch.setattr(ns, "SessionLocal", sessionmaker(bind=test_engine))
    ns._send_push([{"endpoint": SUB["endpoint"], "p256dh": "k" * 40, "auth": "a" * 20}], {"title": "x"})
    db.expire_all()
    assert db.scalars(select(PushSubscription)).all() == []


# ---------------------------------------------------------------- reviews
def delivered(db, place_order):
    o = place_order()
    advance_to(db, o["id"], S.DELIVERED)
    return o


def test_review_rules(world, shopper, place_order, db, make_user, login):
    o = place_order()
    body = {"order_id": o["id"], "food_rating": 5, "vendor_rating": 4, "comment": "Great jollof"}
    assert shopper.post("/api/reviews", json=body).json()["detail"]["code"] == "not_delivered"
    advance_to(db, o["id"], S.DELIVERED)
    assert shopper.get(f"/api/orders/{o['id']}").json()["can_review"] is True
    assert shopper.post("/api/reviews", json={**body, "food_rating": 6}).status_code == 422
    assert login(make_user("x@example.com")).post("/api/reviews", json=body).status_code == 404   # not their order
    r = shopper.post("/api/reviews", json=body)
    assert r.status_code == 201
    assert shopper.post("/api/reviews", json=body).json()["detail"]["code"] == "already_reviewed"
    got = shopper.get(f"/api/orders/{o['id']}").json()
    assert got["can_review"] is False and got["review"]["comment"] == "Great jollof"


def test_reviews_update_vendor_rating_and_hide(world, shopper, place_order, db, make_user, login):
    o1, o2 = delivered(db, place_order), delivered(db, place_order)
    shopper.post("/api/reviews", json={"order_id": o1["id"], "food_rating": 5, "vendor_rating": 5})
    shopper.post("/api/reviews", json={"order_id": o2["id"], "food_rating": 3, "vendor_rating": 2, "comment": "Cold food"})
    db.refresh(world.near)
    assert world.near.rating_count == 2 and world.near.rating_avg == 3.5
    pub = shopper.get(f"/api/vendors/{world.near.id}/reviews").json()
    assert len(pub) == 2 and pub[0]["customer"] == "Yaw S."                                  # privacy: first name + initial
    r = db.scalar(select(Review).where(Review.comment == "Cold food")); r.is_hidden = True
    review_service.recompute_vendor_rating(db, world.near.id); db.commit()
    assert len(shopper.get(f"/api/vendors/{world.near.id}/reviews").json()) == 1
    db.refresh(world.near)
    assert world.near.rating_avg == 5.0


def test_vendor_sees_reviews_and_review_photo(world, shopper, place_order, db, vendor_user, login, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    o = delivered(db, place_order)
    rid = shopper.post("/api/reviews", json={"order_id": o["id"], "food_rating": 4, "vendor_rating": 4}).json()["id"]
    from tests.images import PNG as png
    assert shopper.post(f"/api/reviews/{rid}/image", files={"file": ("a.png", png, "image/png")}).json()["image_url"].startswith("/uploads/")
    assert shopper.post(f"/api/reviews/{rid}/image", files={"file": ("a.png", b"junk", "image/png")}).status_code == 415
    mine = login(vendor_user).get("/api/vendor/reviews").json()
    assert mine["count"] == 1 and mine["distribution"]["4"] == 1 and mine["reviews"][0]["image_url"]
    assert shopper.get("/api/vendor/reviews").status_code in (403, 404)


def test_review_window_closes(world, shopper, place_order, db):
    o = delivered(db, place_order)
    order = db.get(Order, o["id"]); order.delivered_at = utcnow() - timedelta(days=20); db.commit()
    r = shopper.post("/api/reviews", json={"order_id": o["id"], "food_rating": 5, "vendor_rating": 5})
    assert r.json()["detail"]["code"] == "review_window_closed"


def test_review_reminders_sent_once_and_skip_reviewed(world, shopper, place_order, db, emails):
    o1, o2 = delivered(db, place_order), delivered(db, place_order)
    assert review_service.send_due_reminders(db) == 0                                       # too soon
    for oid in (o1["id"], o2["id"]):
        order = db.get(Order, oid); order.delivered_at = utcnow() - timedelta(hours=2)
    db.commit()
    shopper.post("/api/reviews", json={"order_id": o2["id"], "food_rating": 5, "vendor_rating": 5})
    assert review_service.send_due_reminders(db) == 1                                       # only the unreviewed one
    assert review_service.send_due_reminders(db) == 0                                       # and only once
    assert "How was Near Kitchen?" in titles(db, shopper.user) and emails[-1]["subject"] == "How was Near Kitchen?"


def test_approval_creates_in_app_notification(make_user, login, db):
    from app.models.user import UserRole
    admin = login(make_user("admin@example.com", UserRole.ADMIN))
    u = make_user("rid@example.com"); c = login(u)
    rid = c.post("/api/rider/apply", json={"phone": "+233201112233", "vehicle_type": "BICYCLE"}).json()["id"]
    from app.models.riders import DeliveryRider
    db.get(DeliveryRider, rid).id_document = "x.png"; db.commit()
    admin.post(f"/api/admin/riders/{rid}/approve")
    assert titles(db, u) == ["You're approved!"]
