"""Review analysis: sentiment + which aspect (food / delivery / packaging / price / service) is praised or criticised.

RULE-BASED (keyword lexicon with simple negation handling), not machine learning. It is transparent and
predictable; the interface (`analyse(text, ratings)`) is the single place to swap in a trained model later.
"""
import re

POS = {"good", "great", "excellent", "amazing", "delicious", "tasty", "yummy", "fresh", "hot", "fast", "quick", "love", "loved",
       "perfect", "best", "friendly", "polite", "helpful", "affordable", "cheap", "worth", "nice", "awesome", "sweet", "clean",
       "crispy", "juicy", "generous", "recommend", "prompt", "wonderful", "superb", "satisfying", "lovely"}
NEG = {"bad", "terrible", "awful", "horrible", "cold", "stale", "bland", "raw", "undercooked", "overcooked", "salty", "late", "slow",
       "rude", "expensive", "overpriced", "pricey", "leaked", "spilled", "soggy", "dirty", "disappointing", "disappointed", "worst",
       "burnt", "small", "tiny", "wrong", "missing", "poor", "unhappy", "never", "waste", "greasy", "dry", "delayed", "hair", "smelly"}
NEGATORS = {"not", "no", "never", "isnt", "wasnt", "didnt", "dont", "doesnt", "wont", "hardly", "without", "cant"}
ASPECTS = {
    "food": {"food", "taste", "tasty", "delicious", "bland", "salty", "raw", "stale", "fresh", "portion", "jollof", "rice", "chicken", "soup",
             "meal", "flavour", "flavor", "spicy", "cooked", "overcooked", "undercooked", "burnt", "crispy", "juicy"},
    "delivery": {"delivery", "rider", "late", "slow", "fast", "quick", "arrived", "waiting", "wait", "time", "delayed", "prompt", "on", "minutes"},
    "packaging": {"packaging", "package", "packed", "container", "leaked", "spilled", "soggy", "wrapped", "bag", "box", "lid"},
    "price": {"price", "prices", "expensive", "cheap", "overpriced", "pricey", "value", "affordable", "worth", "cost", "money"},
    "service": {"service", "staff", "rude", "polite", "friendly", "helpful", "attitude", "customer", "support", "manager"},
}


def _tokens(sentence: str) -> list[str]:
    return re.findall(r"[a-z]+", sentence.lower().replace("'", ""))


def _polarity(tokens: list[str]) -> int:
    score = 0
    for i, w in enumerate(tokens):
        val = 1 if w in POS else -1 if w in NEG else 0
        if not val:
            continue
        if w == "never" and i + 1 < len(tokens) and tokens[i + 1] in POS | NEG:
            continue                                        # "never" is handled as a negator of the next word
        if any(t in NEGATORS for t in tokens[max(0, i - 2): i]):
            val = -val                                      # "not good", "wasn't tasty"
        score += val
    return score


def analyse(text: str | None, food_rating: int, vendor_rating: int) -> dict:
    """-> {"sentiment": POSITIVE|NEUTRAL|NEGATIVE, "score": -1..1, "aspects": {aspect: POSITIVE|NEGATIVE|MIXED}}"""
    stars = ((food_rating + vendor_rating) / 2 - 3) / 2          # -1 (1 star) .. +1 (5 stars)
    aspect_votes: dict[str, list[int]] = {}
    lex = 0.0
    if text:
        for sentence in re.split(r"[.!?;\n]+|\bbut\b|\bhowever\b", text.lower()):
            toks = _tokens(sentence)
            pol = _polarity(toks)
            lex += pol
            if pol:
                for aspect, words in ASPECTS.items():
                    if words & set(toks):
                        aspect_votes.setdefault(aspect, []).append(1 if pol > 0 else -1)
    lex_norm = max(-1.0, min(1.0, lex / 3))
    score = round(0.6 * stars + 0.4 * lex_norm, 2) if text else round(stars, 2)
    sentiment = "POSITIVE" if score > 0.2 else "NEGATIVE" if score < -0.2 else "NEUTRAL"
    aspects = {a: ("MIXED" if len(set(v)) > 1 else "POSITIVE" if v[0] > 0 else "NEGATIVE") for a, v in aspect_votes.items()}
    return {"sentiment": sentiment, "score": score, "aspects": aspects}
