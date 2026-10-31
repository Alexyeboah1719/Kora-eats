import re


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:limit] or "item"


def vendor_path(vendor_id: int, name: str) -> str:
    return f"/v/{vendor_id}-{slug(name)}"


def food_path(item_id: int, name: str) -> str:
    return f"/f/{item_id}-{slug(name)}"


def parse_ident(ident: str) -> int | None:
    """'12-auntie-amas-kitchen' -> 12 (the words are only for humans and search engines)."""
    m = re.fullmatch(r"(\d{1,9})(?:-[a-z0-9\-]*)?", ident or "")
    return int(m.group(1)) if m else None
