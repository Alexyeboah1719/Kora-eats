"""Ghana-specific rules in one place: regions, coordinates, phone numbers, Ghana Post digital addresses."""
import re

REGIONS = ["Greater Accra", "Ashanti", "Western", "Western North", "Central", "Eastern", "Volta", "Oti", "Northern",
           "Savannah", "North East", "Upper East", "Upper West", "Bono", "Bono East", "Ahafo"]

# A generous box around Ghana (lat 4.5-11.3 N, lng 3.4 W-1.3 E): rejects pins placed in another country or the ocean far away.
LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX = 4.5, 11.3, -3.4, 1.3

# Map "jump to" shortcuts (city centres).
CITIES = {"Accra": (5.6037, -0.1870), "Kumasi": (6.6885, -1.6244), "Tema": (5.6698, -0.0166), "Takoradi": (4.8845, -1.7554),
          "Cape Coast": (5.1053, -1.2466), "Tamale": (9.4075, -0.8534), "Ho": (6.6008, 0.4713), "Koforidua": (6.0941, -0.2591),
          "Sunyani": (7.3349, -2.3123), "Bolgatanga": (10.7856, -0.8514)}

# Mobile money / mobile networks by number prefix (national format without the leading 0).
NETWORKS = {"MTN": {"24", "25", "53", "54", "55", "59"}, "Telecel": {"20", "50"}, "AirtelTigo": {"26", "27", "56", "57"}}

DIGITAL_ADDRESS_RE = re.compile(r"^[A-Z]{2,3}-\d{3,4}-\d{4}$")


def in_ghana(lat: float, lng: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def normalize_phone(raw: str) -> str | None:
    """'024 123 4567', '0241234567', '+233 24 123 4567' and '233241234567' all become '+233241234567'.
    Returns None when it isn't a Ghanaian number."""
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if digits.startswith("+233"):
        national = digits[4:]
    elif digits.startswith("233"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        return None
    if not re.fullmatch(r"[235]\d{8}", national):          # 02x/05x mobile, 03x landline
        return None
    return "+233" + national


def phone_network(e164: str) -> str | None:
    prefix = e164[4:6]
    return next((n for n, p in NETWORKS.items() if prefix in p), None)


def normalize_digital_address(raw: str) -> str | None:
    """Ghana Post GPS code such as GA-543-4531. Returns the upper-cased code or None if malformed."""
    v = re.sub(r"[\s_]+", "-", (raw or "").strip().upper())      # "ga 543 4531" -> "GA-543-4531"
    return v if DIGITAL_ADDRESS_RE.fullmatch(v) else None
