from pydantic import BaseModel, ConfigDict


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    icon: str


class MenuItemOut(BaseModel):
    id: int
    vendor_id: int
    vendor_name: str
    name: str
    description: str
    price: float                      # price the customer pays (after any promotion)
    original_price: float | None = None  # set only when a promotion applies
    image_url: str | None
    prep_minutes: int
    is_available: bool
    is_spicy: bool
    tags: list[str]
    category: str | None
    distance_km: float | None = None
    is_favorite: bool = False
    traits: list[str] = []
    meal_types: list[str] = []
    dietary: list[str] = []
    allergens: list[str] = []
    dietary_declared: bool = False
    spice_level: int = 0
    share_path: str = ""                # public link to share this dish, e.g. /f/7-jollof-rice
    why: list[str] = []                 # 'Why this recommendation?' (filled by the decision engine)
    match_score: int | None = None


class VendorCard(BaseModel):
    id: int
    name: str
    description: str
    category: str | None
    logo_url: str | None
    cover_url: str | None
    rating_avg: float
    rating_count: int
    avg_prep_minutes: int
    is_open: bool
    business_type: str = "RESTAURANT"
    business_label: str = "Restaurant"
    area: str | None = None
    region: str | None = None
    distance_km: float | None = None
    delivery_fee: float | None = None
    share_path: str = ""                # public link to share this vendor, e.g. /v/3-auntie-amas-kitchen
    delivery_radius_km: float | None = None
    in_zone: bool | None = None         # does this vendor deliver to the customer's location?
    is_favorite: bool = False


class MenuSection(BaseModel):
    category: str
    items: list[MenuItemOut]


class VendorDetail(VendorCard):
    address_text: str
    phone: str | None
    latitude: float
    longitude: float
    menu: list[MenuSection]


class HomeOut(BaseModel):
    greeting: str
    headline: str | None = None         # e.g. 'Good afternoon Alex, here are 4 lunch options under GH₵35...'
    categories: list[CategoryOut]
    nearby_vendors: list[VendorCard]
    popular_items: list[MenuItemOut]
    recommended_items: list[MenuItemOut]
    promotions: list[dict] = []
