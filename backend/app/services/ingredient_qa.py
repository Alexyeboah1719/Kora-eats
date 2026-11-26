"""Ingredient Q&A - answers questions about ingredients, nutrition, and meal details."""
import re
from sqlalchemy.orm import Session

from app.models.catalog import MenuItem


INGREDIENT_INFO = {
    # Common Ghanaian ingredients
    "waakye": {"description": "A Ghanaian dish of rice and beans cooked together, often served with stew, fried plantain, eggs, and gari. It's a popular street food.", "spice": "mild to medium", "protein": "moderate"},
    "banku": {"description": "A fermented corn and cassava dough, typically served with hot pepper soup, fish, or okro stew. It's a staple in Ghanaian cuisine.", "spice": "medium to hot", "protein": "varies with accompaniment"},
    "fufu": {"description": "A pounded starchy dough made from boiled cassava, yam, or plantain. Usually served with light soup, palm nut soup, or groundnut soup.", "spice": "mild to medium", "protein": "varies with soup"},
    "jollof rice": {"description": "A one-pot rice dish cooked in tomato sauce and spices. A West African favorite, often served with chicken, beef, or fish.", "spice": "mild to medium", "protein": "varies"},
    "fried rice": {"description": "Stir-fried rice with vegetables, eggs, and meat. A popular Ghanaian street food and party dish.", "spice": "mild", "protein": "moderate"},
    "kenkey": {"description": "Fermented corn dough wrapped in corn husks and boiled. Typically served with pepper sauce, fried fish, or shito.", "spice": "medium to hot", "protein": "moderate"},
    "gari": {"description": "Dried, grated cassava. Often used as a side dish or added to waakye. Can be soaked with water, milk, and sugar as a snack.", "spice": "none", "protein": "low"},
    "shito": {"description": "A spicy Ghanaian black pepper sauce made with dried fish, shrimp, ginger, and spices. Used as a condiment.", "spice": "hot", "protein": "moderate"},
    "tilapia": {"description": "A freshwater fish, popular in Ghana. Often grilled or fried and served with banku, rice, or kenkey. Rich in protein.", "spice": "none to mild", "protein": "high"},
    "chicken": {"description": "Versatile protein source in Ghanaian cuisine. Can be grilled, fried, stewed, or boiled. Popular in jollof, fried rice, and soups.", "spice": "varies", "protein": "high"},
    "goat meat": {"description": "A popular meat in Ghanaian stews and soups. Has a distinct flavor and is often considered a delicacy.", "spice": "medium to hot", "protein": "high"},
    "beef": {"description": "Common protein in Ghanaian dishes. Used in stews, soups, and as grilled meat. Rich in iron and protein.", "spice": "varies", "protein": "high"},
    "plantain": {"description": "A starchy fruit similar to banana. Can be fried (koko), boiled, or roasted. A staple side dish in Ghana.", "spice": "none", "protein": "low"},
    "yam": {"description": "A tuber vegetable, often boiled, fried, or pounded into fufu. Rich in carbohydrates and fiber.", "spice": "none", "protein": "low"},
    "cocoyam": {"description": "A tuber used in Ghanaian cuisine. Can be boiled, fried, or used in soups. Good source of fiber.", "spice": "none", "protein": "low"},
    "eggs": {"description": "Common protein source in Ghanaian breakfast and street food. Often fried, boiled, or used in omelets.", "spice": "none to mild", "protein": "high"},
    "pepper": {"description": "Various types of chili peppers used in Ghanaian cooking, from mild to very hot. Essential for authentic flavor.", "spice": "hot", "protein": "none"},
    "tomatoes": {"description": "Essential base for many Ghanaian stews and sauces. Rich in vitamins and antioxidants.", "spice": "none", "protein": "low"},
    "onions": {"description": "Fundamental aromatic in Ghanaian cooking. Used in almost every stew, sauce, and rice dish.", "spice": "none", "protein": "low"},
    "okro": {"description": "A vegetable used in okro soup or as a side. Known for its mucilaginous (thickening) properties.", "spice": "none", "protein": "low"},
    "garden eggs": {"description": "A type of eggplant popular in Ghana. Often eaten raw with pepper sauce or used in stews.", "spice": "none", "protein": "low"},
}


def answer_ingredient_question(text: str, db: Session, menu_item_id: int | None = None) -> str | None:
    """Answer questions about ingredients or specific menu items."""
    text_lower = text.lower()
    
    # Check if asking about a specific menu item
    if menu_item_id:
        item = db.get(MenuItem, menu_item_id)
        if item:
            return answer_about_item(item, text_lower)
    
    # Check for ingredient questions
    for ingredient, info in INGREDIENT_INFO.items():
        if ingredient in text_lower:
            return format_ingredient_answer(ingredient, info, text_lower)
    
    # Check for general ingredient questions
    if re.search(r"\b(what|describe|tell me|explain|ingredients|contain|made of)\b", text_lower):
        return "I can tell you about ingredients like waakye, banku, fufu, jollof rice, tilapia, chicken, and more. Just ask about a specific ingredient or dish!"
    
    return None


def answer_about_item(item: MenuItem, question: str) -> str:
    """Answer questions about a specific menu item."""
    response = f"{item.name} is {item.description or 'a delicious dish'}"
    
    # Check for specific questions
    if "spicy" in question or "hot" in question:
        if item.tags and "spicy" in item.tags:
            response += " and it's spicy."
        else:
            response += " with mild to medium spice level."
    
    if "ingredient" in question or "contain" in question or "made of" in question:
        if item.tags:
            response += f" It contains: {', '.join(item.tags)}."
        else:
            response += " Contact the vendor for full ingredient details."
    
    if "vegetarian" in question or "vegan" in question:
        if item.tags and "vegetarian" in item.tags:
            response += " It's vegetarian-friendly."
        else:
            response += " It may contain meat or animal products."
    
    return response


def format_ingredient_answer(ingredient: str, info: dict, question: str) -> str:
    """Format a comprehensive answer about an ingredient."""
    response = f"{ingredient.title()}: {info['description']}"
    
    if "spicy" in question:
        response += f" Spice level: {info['spice']}."
    
    if "protein" in question or "healthy" in question or "nutrition" in question:
        response += f" Protein content: {info['protein']}."
    
    return response


def extract_ingredients_from_name(item_name: str) -> list[str]:
    """Extract known ingredients from a menu item name."""
    name_lower = item_name.lower()
    found = []
    
    for ingredient in INGREDIENT_INFO.keys():
        if ingredient in name_lower:
            found.append(ingredient)
    
    return found
