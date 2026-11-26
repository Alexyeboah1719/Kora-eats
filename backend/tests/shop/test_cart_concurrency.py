"""A double-tap, a flaky connection that retries, or two open tabs must never produce a server error or a lost/duplicated item."""
from concurrent.futures import ThreadPoolExecutor


def burst(client, n, body):
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(lambda _: client.post("/api/cart/items", json=body), range(n)))


def test_simultaneous_adds_of_the_same_dish_all_succeed_and_add_up(shopper, world):
    results = burst(shopper, 8, {"menu_item_id": world.plain.id, "quantity": 1})
    assert [r.status_code for r in results] == [200] * 8, [r.text[:80] for r in results if r.status_code != 200]
    lines = shopper.get("/api/cart").json()["items"]
    assert len(lines) == 1 and lines[0]["quantity"] == 8                                  # one line, eight portions: nothing lost, nothing duplicated


def test_a_brand_new_customers_first_simultaneous_adds_also_work(world, make_user, login):
    fresh = login(make_user("first-timer@example.com"))                                    # has never had a cart row
    results = burst(fresh, 6, {"menu_item_id": world.spicy.id, "quantity": 1})
    assert [r.status_code for r in results] == [200] * 6
    assert fresh.get("/api/cart").json()["items"][0]["quantity"] == 6


def test_simultaneous_adds_of_different_dishes(shopper, world):
    with ThreadPoolExecutor(max_workers=4) as pool:
        rs = list(pool.map(lambda i: shopper.post("/api/cart/items", json={"menu_item_id": i.id, "quantity": 1}), [world.plain, world.spicy, world.cola, world.plain]))
    assert all(r.status_code == 200 for r in rs)
    q = {i["name"]: i["quantity"] for i in shopper.get("/api/cart").json()["items"]}
    assert q == {"Plain Rice": 2, "Spicy Jollof Chicken": 1, "Sobolo": 1}
