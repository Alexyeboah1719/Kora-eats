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
        "hi", "hello", "hey", "how", "are", "doing", "up", "whats", "what's", "morning", "afternoon", "evening", "greetings"}
# words that become filters, never search terms
FLAGS = {"spicy", "hot", "pepper", "filling", "hearty", "heavy", "cheap", "cheapest", "affordable", "quick", "fast", "mild",
         "vegetarian", "veggie"}
NUM = r"(\d+(?:\.\d+)?)(?!\.?\d)"   # never match part of a longer number (20 -> 2)
CUR = r"(?:ghs|gh₵|gh¢|₵|cedis?|\$|€|£|usd|eur|gbp|dollar|euro|pound)"


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
    t = text.lower().replace("’", "'")
    p = Parsed(text=text)

    # ---- distance first, so "within 3 km" is never mistaken for a budget
    if m := re.search(NUM + r"\s*(?:km|kms|kilomet(?:er|re)s?)\b", t):
        p.max_km = float(m.group(1))
    if re.search(r"\b(near me|nearby|close by|closest|nearest|around me|near here)\b", t):
        p.near_me = True
        p.max_km = p.max_km or 5.0
        p.sort = "distance"

    # ---- budget (with currency symbols and simple math)
    # First, evaluate any math expressions
    math_pattern = r"(\d+(?:\.\d+)?)\s*([\+\-\*\/])\s*(\d+(?:\.\d+)?)"
    t_evaluated = t
    for match in re.finditer(math_pattern, t):
        num1 = float(match.group(1))
        op = match.group(2)
        num2 = float(match.group(3))
        if op == '+':
            result = num1 + num2
        elif op == '-':
            result = num1 - num2
        elif op == '*':
            result = num1 * num2
        elif op == '/':
            result = num1 / num2 if num2 != 0 else 0
        else:
            continue
        t_evaluated = t_evaluated[:match.start()] + f"{result:.2f}" + t_evaluated[match.end():]
    
    # Now parse budget from the evaluated text
    for pat in (CUR + r"\s*" + NUM, NUM + r"\s*" + CUR,
                r"\b(?:under|below|less than|max(?:imum)?|up to|budget(?: of| is)?|only|have|got|spend(?:ing)?|within|around|about)\s*" + r"(?:" + CUR + r")?\s*" + NUM + r"(?!\s*(?:km|kms|kilomet|min|mins|minute|star))"):
        if m := re.search(pat, t_evaluated):
            p.max_price = float(m.group(1))
            break

    # ---- rating
    if m := re.search(NUM + r"\s*\+?\s*(?:stars?|star rating)", t):
        p.min_rating = min(5.0, float(m.group(1)))
    elif re.search(r"\b(top rated|best rated|highly rated|well reviewed|good reviews)\b", t):
        p.min_rating = 4.3

    # ---- preparation time
    if m := re.search(r"\b(?:under|within|less than|in)\s*" + NUM + r"\s*(?:min|mins|minutes?)\b", t):
        p.max_prep = int(float(m.group(1)))
    elif re.search(r"\b(quick|fast|hurry|asap|urgent)\b", t):
        p.max_prep = 15

    # ---- flags
    if re.search(r"\b(not spicy|no pepper|mild|without pepper|no spice|non-spicy)\b", t):
        p.spicy = False
    elif re.search(r"\b(spicy|hot|pepper|peppery|fiery)\b", t):
        p.spicy = True
    p.filling = bool(re.search(r"\b(filling|hearty|heavy|big portion|full up|very hungry|starving)\b", t))
    p.vegetarian = bool(re.search(r"\b(vegetarian|veggie|vegan|no meat)\b", t))
    if re.search(r"\b(cheap|cheapest|affordable|budget friendly|inexpensive)\b", t):
        p.sort = "price_asc"

    # ---- food words (only words that really exist on the menu)
    if vocab:
        seen: list[str] = []
        for w in re.findall(r"[a-z]+", t):
            s = stem(w)
            if len(w) >= 3 and w not in STOP and w not in FLAGS and s in vocab and s not in seen:
                seen.append(s)
        p.terms = seen
    return p
