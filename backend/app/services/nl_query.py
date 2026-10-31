"""Natural-language -> search filters. Pure functions, no database writes, no model calls.

Rule-based on purpose: a language model could invent vendors/prices. This parser only EXTRACTS
constraints from the words ("under GH₵50", "spicy", "within 3 km"); the answer then comes from
real database rows. Food words are matched against a vocabulary built from the live menu.
"""
import re
from dataclasses import dataclass, field

from app.core.money import cedis_compact

STOP = {"and", "the", "with", "for", "want", "need", "get", "give", "show", "something", "some", "any", "food", "meal", "meals",
        "eat", "eating", "have", "can", "you", "please", "near", "nearby", "under", "below", "than", "less", "over", "from",
        "that", "this", "now", "today", "me", "my", "i", "im", "ghs", "cedis", "cedi", "like", "good", "best", "find", "order", "plate",
        "im", "looking", "find", "would", "love", "know", "tell", "see", "search", "look", "help", "make", "prepare", "cook"}
# words that become filters, never search terms
FLAGS = {"spicy", "hot", "pepper", "filling", "hearty", "heavy", "cheap", "cheapest", "affordable", "quick", "fast", "mild",
         "vegetarian", "veggie", "combo", "combos", "predict", "recommend", "suggestion", "suggestions",
         "hungry", "craving", "starving", "thirsty", "budget", "money", "cash", "limit", "around", "nearby", "open"}
NUM = r"(\d+(?:\.\d+)?)(?!\.?\d)"   # never match part of a longer number (20 -> 2)
CUR = r"(?:ghs|gh₵|gh¢|₵|cedis?)"


def stem(w: str) -> str:
    w = w.lower()
    for suf in ("ies", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


@dataclass
class Parsed:
    text: str
    max_price: float | None = None
    terms: list[str] = field(default_factory=list)
    spicy: bool | None = None
    filling: bool = False
    vegetarian: bool = False
    max_km: float | None = None
    near_me: bool = False
    min_rating: float | None = None
    max_prep: int | None = None
    sort: str = "popular"
    want_combo: bool = False
    want_prediction: bool = False
    open_only: bool = False  # Only show currently open vendors
    hunger_level: str = ""  # none, hungry, very_hungry, starving

    def explain(self) -> list[str]:
        """Human-readable list of what was understood (shown to the user so nothing is hidden)."""
        out = []
        if self.terms:
            out.append("Looking for: " + ", ".join(self.terms))
        if self.max_price is not None:
            out.append(f"Budget: up to {cedis_compact(self.max_price)}")
        if self.spicy is True:
            out.append("Spicy")
        if self.spicy is False:
            out.append("Not spicy")
        if self.filling:
            out.append("Filling")
        if self.vegetarian:
            out.append("Vegetarian")
        if self.max_km is not None:
            out.append(f"Within {self.max_km:g} km")
        if self.min_rating is not None:
            out.append(f"Rated {self.min_rating:g}+ stars")
        if self.max_prep is not None:
            out.append(f"Ready in {self.max_prep} min or less")
        if self.sort == "price_asc":
            out.append("Cheapest first")
        if self.want_combo:
            out.append("Combo suggestions")
        if self.want_prediction:
            out.append("Personalized predictions")
        if self.open_only:
            out.append("Currently open vendors only")
        if self.hunger_level:
            out.append(f"Hunger level: {self.hunger_level.replace('_', ' ')}")
        return out

    @property
    def understood(self) -> bool:
        return bool(self.explain())


def build_vocab(rows: list[tuple[str, str]]) -> set[str]:
    """rows = [(item name or category name, tags)] -> set of stemmed food words in the live menu."""
    vocab: set[str] = set()
    for name, tags in rows:
        for w in re.findall(r"[a-zA-Z]+", f"{name} {tags or ''}"):
            if len(w) >= 3 and w.lower() not in STOP and w.lower() not in FLAGS:
                vocab.add(stem(w))
    return vocab


def parse(text: str, vocab: set[str] | None = None) -> Parsed:
    t = text.lower().replace("'", "'").replace("i'm", "im").replace("im", "i am")
    p = Parsed(text=text)

    # ---- hunger level detection
    if re.search(r"\b(starving|famished|dying of hunger|extremely hungry)\b", t):
        p.hunger_level = "starving"
        p.filling = True  # Starving people want filling food
    elif re.search(r"\b(very hungry|super hungry|extremely hungry|so hungry)\b", t):
        p.hunger_level = "very_hungry"
        p.filling = True
    elif re.search(r"\b(hungry|feeling hungry|need food|want to eat)\b", t):
        p.hunger_level = "hungry"
    elif re.search(r"\b(craving|crave|in the mood for|really want|desire)\b", t):
        p.hunger_level = "craving"

    # ---- distance first, so "within 3 km" is never mistaken for a budget
    if m := re.search(NUM + r"\s*(?:km|kms|kilomet(?:er|re)s?)\b", t):
        p.max_km = float(m.group(1))
    if re.search(r"\b(near me|nearby|close by|closest|nearest|around me|near here|in my area|local|around here)\b", t):
        p.near_me = True
        p.max_km = p.max_km or 5.0
        p.sort = "distance"

    # ---- vendor availability
    if re.search(r"\b(currently open|open now|who is open|available|open vendors|open restaurants)\b", t):
        p.open_only = True

    # ---- budget - more natural language patterns
    for pat in (CUR + r"\s*" + NUM, NUM + r"\s*" + CUR,
                r"\b(?:under|below|less than|max(?:imum)?|up to|budget(?: of| is)?|only|have|got|spend(?:ing)?|within|around|about)\s*" + r"(?:" + CUR + r")?\s*" + NUM + r"(?!\s*(?:km|kms|kilomet|min|mins|minute|star))",
                r"\b(?:i have|i got|my budget is|i can spend|i want to spend|looking to spend)\s*(?:" + CUR + r")?\s*" + NUM,
                r"\bwithin my budget|on a budget|budget of|with\s*" + NUM):
        if m := re.search(pat, t):
            p.max_price = float(m.group(1))
            break

    # ---- rating
    if m := re.search(NUM + r"\s*\+?\s*(?:stars?|star rating)\b", t):
        p.min_rating = min(5.0, float(m.group(1)))
    elif re.search(r"\b(top rated|best rated|highly rated|well reviewed|good reviews|popular|trending)\b", t):
        p.min_rating = 4.3

    # ---- preparation time
    if m := re.search(r"\b(?:under|within|less than|in)\s*" + NUM + r"\s*(?:min|mins|minutes?)\b", t):
        p.max_prep = int(float(m.group(1)))
    elif re.search(r"\b(quick|fast|hurry|asap|urgent|immediately|right now)\b", t):
        p.max_prep = 15

    # ---- spice level - more variations
    if re.search(r"\b(not spicy|no pepper|mild|without pepper|no spice|non-spicy|gentle|light)\b", t):
        p.spicy = False
    elif re.search(r"\b(spicy|hot|pepper|peppery|fiery|extra hot|very spicy|super spicy|blazing)\b", t):
        p.spicy = True

    # ---- filling/hearty based on hunger
    if p.hunger_level in ["starving", "very_hungry"]:
        p.filling = True
    elif re.search(r"\b(filling|hearty|heavy|big portion|full up|satisfying|substantial)\b", t):
        p.filling = True

    # ---- dietary preferences
    p.vegetarian = bool(re.search(r"\b(vegetarian|veggie|vegan|no meat|plant based|meat free)\b", t))
    
    # ---- price sorting
    if re.search(r"\b(cheap|cheapest|affordable|budget friendly|inexpensive|low cost|save money)\b", t):
        p.sort = "price_asc"
    
    # ---- enhanced: combo and prediction requests
    p.want_combo = bool(re.search(r"\b(combo|combos|meal deal|bundle|package|set|platter)\b", t))
    p.want_prediction = bool(re.search(r"\b(predict|recommend|suggest|what would i like|for me|personalized|based on my taste)\b", t))

    # ---- food words (only words that really exist on the menu)
    if vocab:
        seen: list[str] = []
        for w in re.findall(r"[a-z]+", t):
            s = stem(w)
            if len(w) >= 3 and w not in STOP and w not in FLAGS and s in vocab and s not in seen:
                seen.append(s)
        p.terms = seen
    return p
