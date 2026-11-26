"""SAMPLE DATA for development only. Every vendor, price and dish below is fictional.

    python -m scripts.seed_dev_data                  # adds categories + sample vendors (skips ones that already exist)
    python -m scripts.seed_dev_data --demo-users     # ...and four demo logins (customer, vendor, rider, admin) for trying every screen

DEVELOPMENT ONLY: both refuse to run when ENVIRONMENT=production.
"""
import sys
from datetime import datetime, timezone
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.core.security import hash_password
from app.models.catalog import ApprovalStatus, BusinessType, Category, MenuItem, Vendor
from app.models.riders import DeliveryRider, VehicleType
from app.models.user import User, UserProfile, UserRole

CATEGORIES = [
    ("Jollof & Rice", "bi-egg-fried"), ("Waakye", "bi-bowl-hot"), ("Fufu & Soups", "bi-cup-hot"), ("Grills & Chicken", "bi-fire"),
    ("Burgers & Pizza", "bi-pie-chart"), ("Snacks & Sides", "bi-basket"), ("Breakfast", "bi-sunrise"), ("Drinks", "bi-cup-straw"),
    ("Bakery & Sweets", "bi-cake2"), ("Light & Healthy", "bi-flower1"),
]


def item(name, price, cat, prep, spice=0, traits=(), meals=("lunch",), dietary=(), allergens=(), declared=False, tags=""):
    return dict(name=name, price=price, cat=cat, prep=prep, spice=spice, traits=list(traits), meals=list(meals), dietary=list(dietary),
                allergens=list(allergens), declared=declared, tags=tags)


# name, description, business type, category, lat, lng, region, area, address, prep, rating, items
VENDORS = [
    ("Auntie Ama's Kitchen", "Home-style jollof, fried rice and grilled chicken.", "CHOP_BAR", "Jollof & Rice", 5.5560, -0.1830, "Greater Accra", "Osu", "Oxford St, Osu", 25, 4.6, [
        item("Jollof Rice with Chicken", 45, "Jollof & Rice", 20, 2, ["filling", "high_protein"], ["lunch", "dinner"], ["halal"], ["eggs"], True, "chicken,rice"),
        item("Fried Rice with Chicken", 48, "Jollof & Rice", 20, 0, ["filling"], ["lunch", "dinner"], [], ["eggs", "soy"], True, "chicken,rice"),
        item("Plain Jollof", 22, "Jollof & Rice", 10, 2, [], ["lunch"], ["vegetarian"], [], True, "rice,budget"),
        item("Grilled Tilapia & Banku", 60, "Fufu & Soups", 30, 2, ["filling", "high_protein"], ["lunch", "dinner"], ["pescatarian"], ["fish"], True, "fish,banku"),
        item("Sobolo (500ml)", 10, "Drinks", 2, 0, ["light", "sweet"], ["drink"], ["vegan", "gluten_free"], [], True, "hibiscus")]),
    ("Waakye Junction", "Waakye with all the trimmings, served hot.", "STREET_FOOD", "Waakye", 5.5650, -0.1740, "Greater Accra", "Labone", "Labone Junction", 15, 4.4, [
        item("Waakye Special", 40, "Waakye", 12, 2, ["filling", "comfort"], ["breakfast", "lunch"], [], ["eggs"], True, "rice,beans,egg"),
        item("Waakye with Fish", 35, "Waakye", 12, 2, ["filling", "high_protein"], ["lunch"], ["pescatarian"], ["fish", "eggs"], True, "rice,beans,fish"),
        item("Waakye Plain", 20, "Waakye", 8, 1, ["filling"], ["breakfast", "lunch"], ["vegetarian"], [], True, "rice,beans,budget"),
        item("Boiled Egg (2)", 8, "Snacks & Sides", 3, 0, ["high_protein", "light"], ["breakfast", "snack"], ["vegetarian"], ["eggs"], True, "egg"),
        item("Fresh Pineapple Juice", 12, "Drinks", 2, 0, ["light", "sweet"], ["drink"], ["vegan"], [], True, "pineapple")]),
    ("Fufu Palace", "Fufu and banku with rich soups.", "RESTAURANT", "Fufu & Soups", 5.6350, -0.1560, "Greater Accra", "East Legon", "Lagos Ave, East Legon", 30, 4.7, [
        item("Fufu & Light Soup (Goat)", 55, "Fufu & Soups", 25, 2, ["filling", "comfort", "high_protein"], ["lunch", "dinner"], ["halal"], [], False, "goat,fufu,soup"),
        item("Fufu & Groundnut Soup", 50, "Fufu & Soups", 25, 1, ["filling", "comfort"], ["lunch", "dinner"], [], ["peanuts"], True, "chicken,fufu,soup"),
        item("Banku & Okro Stew", 45, "Fufu & Soups", 25, 2, ["filling", "comfort"], ["lunch", "dinner"], ["pescatarian"], ["fish"], True, "banku,okro"),
        item("Kenkey & Fried Fish", 38, "Fufu & Soups", 20, 2, ["filling", "high_protein"], ["lunch"], ["pescatarian"], ["fish"], True, "kenkey,fish")]),
    ("Grill House Osu", "Charcoal grilled chicken, kebab and ribs.", "RESTAURANT", "Grills & Chicken", 5.5580, -0.1790, "Greater Accra", "Osu", "Cantonments Rd, Osu", 30, 4.5, [
        item("Quarter Grilled Chicken & Chips", 55, "Grills & Chicken", 25, 2, ["filling", "high_protein"], ["lunch", "dinner"], ["halal"], [], False, "chicken,chips"),
        item("Chicken Kebab (3 sticks)", 35, "Grills & Chicken", 15, 3, ["high_protein"], ["dinner", "snack"], ["halal"], [], False, "chicken,kebab"),
        item("Grilled Chicken & Jollof", 58, "Grills & Chicken", 25, 2, ["filling", "high_protein"], ["lunch", "dinner"], [], [], False, "chicken,rice"),
        item("Beef Suya", 30, "Snacks & Sides", 12, 3, ["high_protein"], ["snack", "dinner"], ["halal"], ["peanuts"], True, "beef,suya")]),
    ("Slice & Co", "Burgers and pizza made fresh.", "SMALL_BUSINESS", "Burgers & Pizza", 5.6690, -0.1670, "Greater Accra", "Madina", "Madina Estate", 25, 4.2, [
        item("Chicken Burger & Chips", 50, "Burgers & Pizza", 18, 0, ["filling"], ["lunch", "dinner"], [], ["gluten", "eggs", "dairy"], True, "chicken,burger"),
        item("Pepperoni Pizza (Medium)", 85, "Burgers & Pizza", 25, 1, ["filling"], ["dinner"], [], ["gluten", "dairy", "pork"], True, "pizza"),
        item("Veggie Wrap", 28, "Burgers & Pizza", 12, 0, ["light"], ["lunch"], ["vegetarian"], ["gluten"], True, "wrap,budget"),
        item("Milkshake", 25, "Drinks", 5, 0, ["sweet"], ["dessert", "drink"], ["vegetarian"], ["dairy"], True, "dessert")]),
    ("Morning Glory", "Breakfast all day: koko, bread and eggs.", "CHOP_BAR", "Breakfast", 5.5600, -0.2100, "Greater Accra", "Adabraka", "Adabraka", 15, 4.3, [
        item("Koko & Koose", 15, "Breakfast", 8, 1, ["filling", "comfort"], ["breakfast"], ["vegan"], [], True, "koko,budget"),
        item("Egg Sandwich", 18, "Breakfast", 8, 0, ["high_protein"], ["breakfast"], ["vegetarian"], ["eggs", "gluten"], True, "egg"),
        item("Full Ghana Breakfast", 40, "Breakfast", 15, 0, ["filling", "high_protein"], ["breakfast"], [], ["eggs"], True, "egg,beans"),
        item("Tea & Bread", 12, "Breakfast", 5, 0, ["light"], ["breakfast"], ["vegetarian"], ["gluten", "dairy"], True, "budget")]),
    ("Mama Efua's Home Kitchen", "Cooked fresh at home, small batches.", "HOME_COOK", "Jollof & Rice", 5.6700, -0.1650, "Greater Accra", "Madina", "Near Madina Market", 35, 4.8, [
        item("Home-style Jollof & Gizzard", 38, "Jollof & Rice", 30, 2, ["filling", "comfort"], ["lunch"], [], [], False, "rice,gizzard"),
        item("Red Red & Plantain", 32, "Light & Healthy", 25, 1, ["filling", "comfort"], ["lunch", "dinner"], ["vegan"], [], True, "beans,plantain"),
        item("Kelewele (portion)", 15, "Snacks & Sides", 12, 3, ["sweet"], ["snack"], ["vegan"], [], True, "plantain,budget")]),
    ("Sweet Oven Bakery", "Fresh bread, pastries and cakes baked daily.", "BAKER", "Bakery & Sweets", 5.5450, -0.2500, "Greater Accra", "Dansoman", "Dansoman High Street", 20, 4.5, [
        item("Butter Bread (loaf)", 18, "Bakery & Sweets", 10, 0, ["filling"], ["breakfast", "snack"], ["vegetarian"], ["gluten", "dairy", "eggs"], True, "bread"),
        item("Meat Pie", 12, "Bakery & Sweets", 8, 1, ["filling", "high_protein"], ["snack", "lunch"], [], ["gluten", "eggs"], True, "pie,budget"),
        item("Slice of Chocolate Cake", 20, "Bakery & Sweets", 5, 0, ["sweet"], ["dessert", "snack"], ["vegetarian"], ["gluten", "dairy", "eggs", "soy"], True, "cake,dessert")]),
    ("Gold Coast Caterers", "Party jollof, trays and event catering.", "CATERER", "Jollof & Rice", 5.6100, -0.1750, "Greater Accra", "Airport Residential", "Airport Residential Area", 60, 4.6, [
        item("Party Jollof Tray (serves 5)", 220, "Jollof & Rice", 50, 2, ["filling"], ["lunch", "dinner"], [], ["eggs"], True, "rice,party,tray"),
        item("Chicken Tray (20 pieces)", 260, "Grills & Chicken", 55, 1, ["filling", "high_protein"], ["lunch", "dinner"], ["halal"], [], False, "chicken,tray")]),
    ("Maame Adwoa's Chop Bar", "Kumasi favourites: fufu, light soup and kontomire stew.", "CHOP_BAR", "Fufu & Soups", 6.6885, -1.6244, "Ashanti", "Adum", "Adum, Kumasi", 25, 4.6, [
        item("Fufu & Light Soup (Chicken)", 45, "Fufu & Soups", 25, 2, ["filling", "comfort"], ["lunch", "dinner"], [], [], False, "chicken,fufu,soup"),
        item("Kontomire & Yam", 30, "Light & Healthy", 20, 1, ["filling", "light"], ["lunch"], ["pescatarian"], ["fish"], True, "yam,kontomire")]),
    ("Takoradi Seaside Grill", "Fresh fish and chips by the sea.", "RESTAURANT", "Grills & Chicken", 4.8845, -1.7554, "Western", "Market Circle", "Market Circle, Takoradi", 30, 4.4, [
        item("Grilled Tilapia & Chips", 58, "Grills & Chicken", 25, 2, ["filling", "high_protein"], ["lunch", "dinner"], ["pescatarian"], ["fish"], True, "fish,chips"),
        item("Fish Stew & Rice", 40, "Jollof & Rice", 20, 2, ["filling"], ["lunch"], ["pescatarian"], ["fish"], True, "fish,rice")]),
]


def seed(db) -> tuple[int, int]:
    cats = {}
    for n, (name, icon) in enumerate(CATEGORIES):
        c = db.scalar(select(Category).where(Category.name == name))
        if c is None:
            c = Category(name=name, slug=name.lower().replace(" & ", "-").replace(" ", "-"), icon=icon, sort_order=n)
            db.add(c)
            db.flush()
        cats[name] = c
    added = 0
    for name, desc, btype, cat, lat, lng, region, area, addr, prep, rating, items in VENDORS:
        if db.scalar(select(Vendor).where(Vendor.name == name)):
            continue
        v = Vendor(name=name, description=desc, business_type=BusinessType(btype), category_id=cats[cat].id, latitude=lat, longitude=lng, region=region,
                   area=area, address_text=addr, avg_prep_minutes=prep, rating_avg=rating, rating_count=20, phone="+233200000000",
                   approval_status=ApprovalStatus.APPROVED)
        db.add(v)
        db.flush()
        for it in items:
            db.add(MenuItem(vendor_id=v.id, category_id=cats[it["cat"]].id, name=it["name"], price=it["price"], prep_minutes=it["prep"],
                            spice_level=it["spice"], is_spicy=it["spice"] > 0, traits=it["traits"], meal_types=it["meals"], dietary=it["dietary"],
                            allergens=it["allergens"], dietary_declared=it["declared"], tags=it["tags"], description=f"{it['name']} from {name}."))
        added += 1
    db.commit()
    return len(cats), added


DEMO_PASSWORD = "Demo!2345"                 # public on purpose: these accounts exist only on your own development machine
DEMO_USERS = [("customer@demo.kora", "Demo Customer", UserRole.CUSTOMER), ("vendor@demo.kora", "Demo Vendor", UserRole.VENDOR),
              ("rider@demo.kora", "Demo Rider", UserRole.RIDER), ("admin@demo.kora", "Demo Admin", UserRole.ADMIN)]


def seed_demo_users(db) -> list[str]:
    """Four ready-made logins. The vendor owns 'Auntie Ama's Kitchen'; the rider is approved, online and located in Accra."""
    made = []
    for email, name, role in DEMO_USERS:
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, profile=UserProfile(full_name=name, phone="+233241234567"))
            db.add(u)
            made.append(email)
        u.password_hash, u.role, u.is_email_verified, u.is_active = hash_password(DEMO_PASSWORD), role, True, True
    db.flush()
    vendor_user = db.scalar(select(User).where(User.email == "vendor@demo.kora"))
    v = db.scalar(select(Vendor).where(Vendor.name == "Auntie Ama's Kitchen"))
    if v is not None and v.owner_user_id is None:
        v.owner_user_id = vendor_user.id
    rider_user = db.scalar(select(User).where(User.email == "rider@demo.kora"))
    if db.scalar(select(DeliveryRider).where(DeliveryRider.user_id == rider_user.id)) is None:
        db.add(DeliveryRider(user_id=rider_user.id, phone="+233241234567", vehicle_type=VehicleType.MOTORBIKE, approval_status=ApprovalStatus.APPROVED,
                             is_online=True, current_lat=5.5580, current_lng=-0.1830, last_location_at=datetime.now(timezone.utc)))
    db.commit()
    return made


def main(argv: list[str]) -> None:
    if get_settings().environment == "production":
        raise SystemExit("Refusing to load SAMPLE data or demo logins in production.")
    with SessionLocal() as session:
        c, v = seed(session)
        print(f"Categories ready: {c}. Sample vendors added: {v}.")
        if "--demo-users" in argv:
            made = seed_demo_users(session)
            print(f"Demo logins ready (password: {DEMO_PASSWORD}): " + ", ".join(e for e, _, _ in DEMO_USERS) + (f". Newly created: {len(made)}." if made else ". (already existed)"))


if __name__ == "__main__":
    main(sys.argv[1:])
