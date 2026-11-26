"""Performance guard: the number of database queries must NOT grow with the amount of data (the classic N+1 problem)."""
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models.catalog import ApprovalStatus, MenuItem, Vendor
from tests.shop.conftest import OSU

ENDPOINTS = [("GET", "/api/home", None), ("GET", "/api/vendors", None), ("GET", "/api/menu-items", None), ("GET", "/api/ai/feed", None),
             ("POST", "/api/ai/afford", {"budget": "500"}), ("POST", "/api/ai/craving", {"cravings": ["filling"]}), ("POST", "/api/ai/group/split", {"people": 4, "budget": "400"}),
             ("GET", "/api/vendors/{vid}", None)]


def count_queries(engine, fn):
    n = {"q": 0}
    listener = lambda *a, **k: n.__setitem__("q", n["q"] + 1)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        r = fn()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return n["q"], r


def call(c, method, path, body, vid):
    path = path.replace("{vid}", str(vid))
    sep = "&" if "?" in path else "?"
    if method == "GET":
        return c.get(path + sep + f"lat={OSU['lat']}&lng={OSU['lng']}")
    return c.post(path, json={**body, **OSU})


def grow(db, world, vendors=12, items_each=15):
    for i in range(vendors):
        v = Vendor(name=f"Extra {i}", latitude=5.556 + i * 0.001, longitude=-0.183, approval_status=ApprovalStatus.APPROVED, address_text="x", avg_prep_minutes=15,
                   rating_avg=4.0, rating_count=3)
        db.add(v); db.flush()
        db.add_all([MenuItem(vendor_id=v.id, category_id=world.jollof.id, name=f"Dish {i}-{j}", price=Decimal(10 + j), traits=["filling"], meal_types=["lunch"],
                             dietary=["halal"] if j % 3 == 0 else [], prep_minutes=10 + j, order_count=j) for j in range(items_each)])
    db.commit()


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_query_count_does_not_grow_with_data(method, path, body, shopper, world, db, test_engine):
    small, r1 = count_queries(test_engine, lambda: call(shopper, method, path, body, world.near.id))
    assert r1.status_code == 200, (path, r1.text[:200])
    grow(db, world)                                                                    # 12 more vendors, 180 more dishes
    big, r2 = count_queries(test_engine, lambda: call(shopper, method, path, body, world.near.id))
    assert r2.status_code == 200
    assert big <= small + 2, f"{path}: {small} queries with little data, {big} with a lot (N+1?)"
    assert big <= 30, f"{path} uses {big} queries"


def test_a_busy_marketplace_still_answers_quickly(shopper, world, db):
    import time
    grow(db, world, vendors=30, items_each=20)                                          # 600+ dishes
    for method, path, body in ENDPOINTS[:6]:
        t = time.perf_counter()
        assert call(shopper, method, path, body, world.near.id).status_code == 200
        assert time.perf_counter() - t < 2.0, f"{path} took {time.perf_counter() - t:.2f}s"
