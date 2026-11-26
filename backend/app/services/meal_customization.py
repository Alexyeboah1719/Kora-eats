"""Meal customization parser - converts natural language requests into structured customizations.
Supports: portion preferences, side dishes, ingredient exclusions, spice levels, dietary preferences."""
import re
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class Customization:
    """Structured meal customization request."""
    portion: Literal["small", "regular", "large", "extra_large"] | None = None
    spice_level: Literal["none", "mild", "medium", "hot", "extra_hot"] | None = None
    add_ingredients: list[str] = field(default_factory=list)
    remove_ingredients: list[str] = field(default_factory=list)
    extra_sides: list[str] = field(default_factory=list)
    remove_sides: list[str] = field(default_factory=list)
    dietary_notes: list[str] = field(default_factory=list)
    special_instructions: str = ""
    
    def to_instruction(self) -> str:
        """Convert to a human-readable instruction for the restaurant."""
        parts = []
        if self.portion:
            parts.append(f"Portion: {self.portion.replace('_', ' ').title()}")
        if self.spice_level:
            parts.append(f"Spice level: {self.spice_level.replace('_', ' ').title()}")
        if self.add_ingredients:
            parts.append(f"Add: {', '.join(self.add_ingredients)}")
        if self.remove_ingredients:
            parts.append(f"No: {', '.join(self.remove_ingredients)}")
        if self.extra_sides:
            parts.append(f"Extra sides: {', '.join(self.extra_sides)}")
        if self.remove_sides:
            parts.append(f"No sides: {', '.join(self.remove_sides)}")
        if self.dietary_notes:
            parts.append(f"Dietary: {', '.join(self.dietary_notes)}")
        if self.special_instructions:
            parts.append(f"Special: {self.special_instructions}")
        return ". ".join(parts) if parts else "No special requests"


# Common ingredient keywords
COMMON_INGREDIENTS = {
    "spaghetti", "gari", "stew", "rice", "beans", "plantain", "yam", "cassava", "cocoyam",
    "chicken", "fish", "beef", "goat", "pork", "eggs", "shrimp", "crab",
    "tomatoes", "onions", "peppers", "garlic", "ginger", "vegetables", "salad",
    "cheese", "butter", "oil", "mayonnaise", "ketchup", "mustard",
    "bread", "tortilla", "wrap", "dough", "crust"
}

COMMON_SIDES = {
    "fried rice", "jollof rice", "plain rice", "waakye", "banku", "fufu", "kenkey",
    "plantain", "fried plantain", "boiled plantain", "yam", "fried yam", "boiled yam",
    "cassava", "gari", "eggs", "fried eggs", "boiled eggs", "salad", "coleslaw",
    "soup", "stew", "gravy", "shito", "pepper sauce"
}

SPICE_KEYWORDS = {
    "no pepper": "none", "without pepper": "none", "no spice": "none", "not spicy": "none",
    "mild": "mild", "less pepper": "mild", "little pepper": "mild",
    "medium": "medium", "normal pepper": "medium", "regular spice": "medium",
    "spicy": "hot", "hot": "hot", "extra pepper": "hot", "lots of pepper": "hot",
    "very spicy": "extra_hot", "extra hot": "extra_hot", "fire": "extra_hot", "very hot": "extra_hot"
}

PORTION_KEYWORDS = {
    "small": "small", "little": "small", "mini": "small", "kid": "small",
    "regular": "regular", "normal": "regular", "standard": "regular", "medium": "regular",
    "large": "large", "big": "large", "jumbo": "large", "family": "large",
    "extra large": "extra_large", "xl": "extra_large", "huge": "extra_large", "sharing": "extra_large"
}

DIETARY_KEYWORDS = {
    "vegetarian": "vegetarian", "veggie": "vegetarian", "no meat": "vegetarian",
    "vegan": "vegan", "plant based": "vegan", "dairy free": "vegan",
    "gluten free": "gluten_free", "no gluten": "gluten_free",
    "halal": "halal",
    "low carb": "low_carb", "keto": "low_carb",
    "high protein": "high_protein"
}


def parse_customization(text: str) -> Customization:
    """Parse natural language customization request into structured format."""
    text_lower = text.lower()
    custom = Customization()
    
    # Parse spice level
    for pattern, level in SPICE_KEYWORDS.items():
        if pattern in text_lower:
            custom.spice_level = level
            break
    
    # Parse portion
    for pattern, portion in PORTION_KEYWORDS.items():
        if pattern in text_lower:
            custom.portion = portion
            break
    
    # Parse dietary preferences
    for pattern, dietary in DIETARY_KEYWORDS.items():
        if pattern in text_lower:
            if dietary not in custom.dietary_notes:
                custom.dietary_notes.append(dietary)
    
    # Parse "extra [ingredient]" patterns
    extra_pattern = r"\bextra\s+([a-z\s]+?)(?:\s*(?:,|and|or|please|\.|$))"
    for match in re.finditer(extra_pattern, text_lower):
        ingredient = match.group(1).strip()
        if ingredient in COMMON_INGREDIENTS or ingredient in COMMON_SIDES:
            if ingredient not in custom.extra_sides:
                custom.extra_sides.append(ingredient)
        elif ingredient not in custom.add_ingredients:
            custom.add_ingredients.append(ingredient)
    
    # Parse "no [ingredient]" or "without [ingredient]" patterns
    no_pattern = r"\b(?:no|without|less)\s+([a-z\s]+?)(?:\s*(?:,|and|or|please|\.|$))"
    for match in re.finditer(no_pattern, text_lower):
        ingredient = match.group(1).strip()
        if ingredient in COMMON_INGREDIENTS or ingredient in COMMON_SIDES:
            if ingredient not in custom.remove_sides:
                custom.remove_sides.append(ingredient)
        elif ingredient not in custom.remove_ingredients:
            custom.remove_ingredients.append(ingredient)
    
    # Parse "add [ingredient]" patterns
    add_pattern = r"\badd\s+([a-z\s]+?)(?:\s*(?:,|and|or|please|\.|$))"
    for match in re.finditer(add_pattern, text_lower):
        ingredient = match.group(1).strip()
        if ingredient not in custom.add_ingredients:
            custom.add_ingredients.append(ingredient)
    
    # Capture special instructions (anything not captured above)
    words_to_remove = set()
    for pattern in SPICE_KEYWORDS.keys():
        words_to_remove.add(pattern)
    for pattern in PORTION_KEYWORDS.keys():
        words_to_remove.add(pattern)
    for pattern in DIETARY_KEYWORDS.keys():
        words_to_remove.add(pattern)
    
    # Extract special instructions
    instructions = text_lower
    for word in words_to_remove:
        instructions = instructions.replace(word, "")
    instructions = re.sub(r'\bextra\s+[a-z\s]+?\b', '', instructions)
    instructions = re.sub(r'\b(?:no|without|less)\s+[a-z\s]+?\b', '', instructions)
    instructions = re.sub(r'\badd\s+[a-z\s]+?\b', '', instructions)
    instructions = re.sub(r'[,.]', '', instructions)
    instructions = ' '.join(instructions.split())
    
    if instructions and len(instructions) > 3:
        custom.special_instructions = instructions
    
    return custom


def explain_customization(custom: Customization) -> str:
    """Generate a natural language explanation of the customization."""
    if not any([custom.portion, custom.spice_level, custom.add_ingredients, 
                custom.remove_ingredients, custom.extra_sides, custom.remove_sides,
                custom.dietary_notes, custom.special_instructions]):
        return "No special customization requested."
    
    parts = []
    if custom.portion:
        parts.append(f"{custom.portion.replace('_', ' ')} portion")
    if custom.spice_level:
        parts.append(f"{custom.spice_level.replace('_', ' ')} spice level")
    if custom.add_ingredients:
        parts.append(f"adding {', '.join(custom.add_ingredients)}")
    if custom.remove_ingredients:
        parts.append(f"removing {', '.join(custom.remove_ingredients)}")
    if custom.extra_sides:
        parts.append(f"extra {', '.join(custom.extra_sides)}")
    if custom.remove_sides:
        parts.append(f"no {', '.join(custom.remove_sides)}")
    if custom.dietary_notes:
        parts.append(f"dietary: {', '.join(custom.dietary_notes)}")
    if custom.special_instructions:
        parts.append(f"special: {custom.special_instructions}")
    
    return "I understood you want " + ", ".join(parts) + "."
