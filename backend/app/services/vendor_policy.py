"""Who may sell on KORA EATS and what each kind of seller must show before approval.
Light for home cooks and street-food sellers, fuller for restaurants and caterers."""
from app.models.catalog import BUSINESS_LABELS, Vendor

DOC_LABELS = {"ID_CARD": "Owner ID card (Ghana Card, passport or driver's licence)", "BUSINESS_REGISTRATION": "Business registration",
              "FOOD_HANDLER_CERT": "Food handler / health certificate", "OTHER": "Other supporting document"}

_FULL = {"required": ["ID_CARD", "BUSINESS_REGISTRATION"], "any_of": [], "optional": ["FOOD_HANDLER_CERT", "OTHER"]}
_SMALL = {"required": ["ID_CARD"], "any_of": [["BUSINESS_REGISTRATION", "FOOD_HANDLER_CERT"]], "optional": ["OTHER"]}
_PERSONAL = {"required": ["ID_CARD"], "any_of": [], "optional": ["FOOD_HANDLER_CERT", "OTHER"]}
POLICY = {
    "RESTAURANT": {**_FULL, "blurb": "A registered restaurant or eatery."},
    "CATERER": {**_FULL, "blurb": "Event and bulk-order catering."},
    "CHOP_BAR": {**_SMALL, "blurb": "A chop bar or local canteen."},
    "BAKER": {**_SMALL, "blurb": "Bread, pastries and cakes."},
    "SMALL_BUSINESS": {**_SMALL, "blurb": "A small food business."},
    "HOME_COOK": {**_PERSONAL, "blurb": "Cooking from your own kitchen."},
    "STREET_FOOD": {**_PERSONAL, "blurb": "A street-food or roadside seller."},
}


def describe() -> list[dict]:
    return [{"id": k, "label": BUSINESS_LABELS[k], "blurb": p["blurb"], "required": p["required"],
             "any_of": p["any_of"], "optional": p["optional"]} for k, p in POLICY.items()]


def missing_documents(v: Vendor) -> list[str]:
    """Human-readable list of what's still needed (empty = verification requirements are met)."""
    have = {d.doc_type for d in v.documents}
    p = POLICY[v.business_type.value]
    out = [DOC_LABELS[d] for d in p["required"] if d not in have]
    for group in p["any_of"]:
        if not have & set(group):
            out.append(" or ".join(DOC_LABELS[d] for d in group))
    return out
