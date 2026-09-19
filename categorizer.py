"""Transaction categorization engine for Money Guilt with machine learning"""

import re
import json
import os
from typing import Dict, List, Tuple, Optional

# Category keywords - maps merchant keywords to spending categories
CATEGORY_KEYWORDS = {
    "food": [
        "restaurant", "cafe", "coffee", "pizza", "burger", "taco", "sushi",
        "doordash", "uber eats", "grubhub", "postmates", "diner", "grill",
        "bistro", "bakery", "smoothie", "juice", "starbucks", "mcdonalds",
        "chipotle", "panera", "subway", "dunkin", "taco bell", "wendys",
        "arbys", "chick-fil-a", "popeyes", "sonic", "whataburger", "applebees",
        "food delivery", "eats", "ramen", "bbq", "wings", "deli", "sandwich",
        "falafel", "pho", "pad thai", "korean", "mexican", "indian", "thai",
        "brunch", "waffle", "pancake", "breakfast", "lunch"
    ],
    "groceries": [
        "grocery", "safeway", "whole foods", "kroger", "trader joe's",
        "sprouts", "albertsons", "wegmans", "publix", "market", "supermarket",
        "instacart", "amazon fresh", "food lion", "harris teeter"
    ],
    "entertainment": [
        "movie", "cinema", "theater", "netflix", "spotify", "hulu", "disney",
        "game", "steam", "playstation", "xbox", "twitch", "ticket", "concert",
        "music", "park", "arcade", "bowling", "cinemark", "amc", "regal",
        "fandango", "live music", "theatre", "show"
    ],
    "shopping": [
        "amazon", "ebay", "shop", "store", "mall", "retail", "clothing",
        "apparel", "fashion", "nike", "adidas", "gap", "h&m", "zara",
        "uniqlo", "forever 21", "shein", "kohl's", "best buy", "apple"
    ],
    "transportation": [
        "uber", "lyft", "taxi", "cab", "gas", "fuel", "shell", "chevron",
        "exxon", "bp", "parking", "transit", "metro", "train", "bus",
        "airline", "flight", "airport", "car rental", "hertz", "avis"
    ],
    "utilities": [
        "electric", "water", "gas", "internet", "phone", "cable", "verizon",
        "at&t", "t-mobile", "comcast", "spectrum", "utility", "power",
        "cox", "dish", "directv", "frontier"
    ],
    "health": [
        "pharmacy", "doctor", "hospital", "clinic", "dentist", "gym",
        "yoga", "fitness", "cvs", "walgreens", "medical",
        "vitamin", "supplement", "wellness", "therapist", "counselor",
        "chiropractor", "dermatologist", "psychiatrist"
    ],
    "subscriptions": [
        "subscription", "monthly", "annual", "membership", "plan", "premium",
        "app store", "play store", "patreon", "adobe", "microsoft",
        "aws", "heroku", "digitalocean"
    ],
    "housing": [
        "rent", "mortgage", "landlord", "lease", "property management",
        "apartment", "condo", "home"
    ],
}

# Wasteful category keywords
WASTEFUL_KEYWORDS = [
    "delivery fee", "impulse", "fast food", "coffee", "snack", "candy",
    "soda", "energy drink", "gambling", "casino", "lottery", "premium", "upgrade"
]


class TransactionCategorizer:
    """Categorize transactions with user learning capabilities"""

    def __init__(self, overrides_file: str = "merchant_overrides.json"):
        # Build lowercase keyword maps
        self.categories = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            self.categories[category] = [k.lower() for k in keywords]

        self.wasteful_keywords = [w.lower() for w in WASTEFUL_KEYWORDS]

        # Learned merchant overrides from user manual categorizations
        self.overrides_file = overrides_file
        self.merchant_overrides: Dict[str, str] = self._load_overrides()
        self.merchant_wasteful: Dict[str, bool] = {}

    def _load_overrides(self) -> Dict[str, str]:
        """Load merchant overrides from file"""
        if os.path.exists(self.overrides_file):
            try:
                with open(self.overrides_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading overrides: {e}")
        return {}

    def _save_overrides(self):
        """Save merchant overrides to file"""
        try:
            with open(self.overrides_file, 'w') as f:
                json.dump(self.merchant_overrides, f, indent=2)
        except Exception as e:
            print(f"Error saving overrides: {e}")

    def learn_merchant_category(self, merchant_name: str, category: str, is_wasteful: bool = None):
        """
        Learn from user manual categorization.

        Args:
            merchant_name: Merchant name
            category: Category to associate
            is_wasteful: Whether this merchant sells wasteful items
        """
        merchant_lower = merchant_name.lower()
        self.merchant_overrides[merchant_lower] = category

        if is_wasteful is not None:
            self.merchant_wasteful[merchant_lower] = is_wasteful

        self._save_overrides()

    def categorize(self, merchant_name: str, amount: float = None,
                   description: str = None) -> str:
        """
        Categorize a transaction.

        Priority:
        1. Learned merchant overrides
        2. Keyword matching with scoring
        3. Amount-based heuristics
        4. Default to "other"
        """
        if not merchant_name:
            return "other"

        merchant_lower = merchant_name.lower()

        # Check if we've learned this merchant before
        if merchant_lower in self.merchant_overrides:
            return self.merchant_overrides[merchant_lower]

        # Try partial merchant match (e.g., "Starbucks Downtown" contains "starbucks")
        for learned_merchant, category in self.merchant_overrides.items():
            if learned_merchant in merchant_lower or merchant_lower in learned_merchant:
                return category

        # Score categories based on keywords
        text_to_match = merchant_lower
        if description:
            text_to_match += " " + description.lower()

        category_scores = {}
        for category, keywords in self.categories.items():
            score = self._score_category(text_to_match, keywords)
            if score > 0:
                category_scores[category] = score

        # Amount-based heuristics
        if amount and not category_scores:
            category_scores = self._amount_based_category(amount)

        # Return best match or default
        if category_scores:
            return max(category_scores, key=category_scores.get)
        return "other"

    def is_wasteful(self, merchant_name: str, category: str = None,
                    description: str = None) -> bool:
        """
        Determine if a transaction is wasteful.

        Priority:
        1. User-learned merchant wasteful status
        2. Keyword matching
        3. Category-based rules
        4. Default to False
        """
        if not merchant_name:
            return False

        merchant_lower = merchant_name.lower()

        # Check if we've learned this merchant's wasteful status
        if merchant_lower in self.merchant_wasteful:
            return self.merchant_wasteful[merchant_lower]

        # Check partial merchant match
        for learned_merchant, is_wasteful in self.merchant_wasteful.items():
            if learned_merchant in merchant_lower or merchant_lower in learned_merchant:
                if is_wasteful:
                    return True

        # Check wasteful keywords
        text_to_match = merchant_lower
        if description:
            text_to_match += " " + description.lower()

        for keyword in self.wasteful_keywords:
            if keyword in text_to_match:
                return True

        # Category-based rules
        if not category:
            category = self.categorize(merchant_name, description=description)

        if category:
            # High-discretionary categories
            wasteful_categories = {
                "entertainment": 0.8,  # 80% of entertainment is wasteful
                "food": 0.5,  # 50% of food (delivery, fast food)
                "subscriptions": 0.9,  # 90% of subscriptions are wasteful
                "shopping": 0.4,  # 40% of shopping is impulse
            }

            if category in wasteful_categories:
                # Check specific merchants
                if category == "food":
                    if any(k in merchant_lower for k in ["delivery", "ubereats", "doordash", "grubhub"]):
                        return True
                elif category == "entertainment":
                    return True
                elif category == "subscriptions":
                    return True

        return False

    def _score_category(self, text: str, keywords: List[str]) -> float:
        """Score how well text matches a category"""
        score = 0.0
        for keyword in keywords:
            if keyword in text:
                # Word boundary match scores higher
                if self._is_word_boundary_match(text, keyword):
                    score += 2.0
                else:
                    score += 1.0
        return score

    def _is_word_boundary_match(self, text: str, keyword: str) -> bool:
        """Check if keyword matches at word boundaries"""
        pattern = r'\b' + re.escape(keyword) + r'\b'
        return bool(re.search(pattern, text))

    def _amount_based_category(self, amount: float) -> Dict[str, float]:
        """Guess category based on amount"""
        scores = {}
        # Cheap purchases likely groceries or food
        if amount < 20:
            scores["food"] = 1.0
            scores["groceries"] = 1.0
        # Mid-range could be food or shopping
        elif amount < 100:
            scores["food"] = 0.5
            scores["shopping"] = 0.5
        # Large purchases likely shopping or utilities
        elif amount > 100:
            scores["shopping"] = 0.5
            scores["utilities"] = 0.3
        return scores

    def get_category_suggestions(self, merchant_name: str,
                                top_n: int = 3) -> List[Tuple[str, float]]:
        """Get top category suggestions with scores"""
        text = merchant_name.lower()
        scores = {}

        for category, keywords in self.categories.items():
            score = self._score_category(text, keywords)
            if score > 0:
                scores[category] = score

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_scores[:top_n]


# Global instance
_categorizer = None

def get_categorizer() -> TransactionCategorizer:
    """Get or create global categorizer"""
    global _categorizer
    if _categorizer is None:
        _categorizer = TransactionCategorizer()
    return _categorizer


def categorize_transaction(merchant_name: str, amount: float = None,
                           description: str = None) -> str:
    """Categorize a transaction"""
    return get_categorizer().categorize(merchant_name, amount, description)


def is_transaction_wasteful(merchant_name: str, category: str = None,
                            description: str = None) -> bool:
    """Check if transaction is wasteful"""
    return get_categorizer().is_wasteful(merchant_name, category, description)


def learn_merchant(merchant_name: str, category: str, is_wasteful: bool = None):
    """Learn from user categorization"""
    get_categorizer().learn_merchant_category(merchant_name, category, is_wasteful)
