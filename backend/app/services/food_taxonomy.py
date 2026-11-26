"""Controlled vocabulary for food attributes. Vendors CHOOSE from these lists (no free-text guessing), so the
recommendation engine can state facts like 'high protein' or 'halal' only when a vendor actually said so.

SAFETY RULE: missing information is never treated as 'safe'. A customer who avoids an allergen is only shown
dishes whose vendor confirmed the allergen information."""

TRAITS = {"filling": "Filling", "light": "Light", "sweet": "Sweet", "comfort": "Comfort food", "high_protein": "High protein"}
MEAL_TYPES = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "snack": "Snack", "drink": "Drink", "dessert": "Dessert"}
DIETARY = {"vegetarian": "Vegetarian", "vegan": "Vegan", "halal": "Halal", "pescatarian": "Pescatarian", "gluten_free": "Gluten-free",
           "dairy_free": "Dairy-free", "nut_free": "Nut-free", "no_pork": "No pork"}
ALLERGENS = {"peanuts": "Peanuts", "tree_nuts": "Tree nuts", "fish": "Fish", "shellfish": "Shellfish", "eggs": "Eggs", "dairy": "Dairy",
             "gluten": "Gluten", "soy": "Soy", "pork": "Pork"}
SPICE_LEVELS = {0: "Not spicy", 1: "Mild", 2: "Medium", 3: "Hot"}

# A dietary label also guarantees these (so a vegan dish satisfies "vegetarian" and "dairy-free").
IMPLIES_DIET = {"vegan": {"vegetarian", "dairy_free", "no_pork"}, "vegetarian": {"no_pork"}}
# ...and guarantees the absence of these allergens.
IMPLIES_NO_ALLERGEN = {"vegan": {"fish", "shellfish", "eggs", "dairy", "pork"}, "vegetarian": {"fish", "shellfish", "pork"},
                       "pescatarian": {"pork"}, "dairy_free": {"dairy"}, "gluten_free": {"gluten"}, "nut_free": {"peanuts", "tree_nuts"}, "no_pork": {"pork"}}


def clean(values: list[str] | None, allowed: dict) -> list[str]:
    """Lower-case, de-duplicate (keeping order), and refuse anything outside the controlled list."""
    out: list[str] = []
    for v in values or []:
        k = str(v).strip().lower().replace(" ", "_").replace("-", "_")
        if k not in allowed:
            raise ValueError(f"'{v}' isn't a recognised option. Choose from: {', '.join(sorted(allowed))}")
        if k not in out:
            out.append(k)
    return out


def diet_flags(item_dietary: list[str]) -> set[str]:
    flags = set(item_dietary)
    for d in item_dietary:
        flags |= IMPLIES_DIET.get(d, set())
    return flags


def allergen_free(item_dietary: list[str], item_allergens: list[str], declared: bool, allergen: str) -> bool:
    """True only when we KNOW the dish has none of this allergen: the vendor listed allergens (declared) and it isn't there,
    or a dietary label rules it out."""
    if allergen in item_allergens:
        return False
    if any(allergen in IMPLIES_NO_ALLERGEN.get(d, set()) for d in diet_flags(item_dietary) | set(item_dietary)):
        return True
    return declared


# Dietary needs that are really "contains none of these allergens".
ALLERGEN_BASED_DIET = {"gluten_free": {"gluten"}, "dairy_free": {"dairy"}, "nut_free": {"peanuts", "tree_nuts"}, "no_pork": {"pork"}}


def check_diet(item_dietary: list[str], item_allergens: list[str], declared: bool, need_diet: list[str], avoid_allergens: list[str]) -> tuple[bool, list[str]]:
    """Does this dish satisfy the customer's dietary needs? -> (ok, problems). Unknown is NOT ok."""
    problems: list[str] = []
    flags = diet_flags(item_dietary)
    for need in need_diet:
        if need in flags:
            continue                                       # the vendor labelled it (vegan also counts as vegetarian, etc.)
        absent = ALLERGEN_BASED_DIET.get(need)
        if absent and all(allergen_free(item_dietary, item_allergens, declared, a) for a in absent):
            continue                                       # e.g. "gluten-free": confirmed gluten-free allergen list
        problems.append(f"not labelled {DIETARY[need].lower()}")
    for a in avoid_allergens:
        if not allergen_free(item_dietary, item_allergens, declared, a):
            problems.append(f"contains {ALLERGENS[a].lower()}" if a in item_allergens else f"{ALLERGENS[a].lower()} not confirmed by the vendor")
    return (not problems, problems)
