"""Transaction categorization engine for Money Guilt"""

import re
from typing import Dict, List, Tuple

# Category keywords - maps merchant keywords to spending categories
CATEGORY_KEYWORDS = {
    "food": [
        "restaurant", "cafe", "coffee", "pizza", "burger", "taco", "sushi",
        "doordash", "uber eats", "grubhub", "postmates", "diner", "grill",
        "bistro", "bakery", "smoothie", "juice", "starbucks", "mcdonalds",
        "chipotle", "panera", "subway", "dunkin", "taco bell", "wendys",
        "arbys", "chick-fil-a", "popeyes", "sonic", "whataburger"
    ],
    "groceries": [
        "grocery", "safeway", "whole foods", "kroger", "trader joe's",
        "sprouts", "albertsons", "wegmans", "publix", "target", "walmart",
        "costco", "bjs", "sam's club", "market", "supermarket"
    ],
    "entertainment": [
        "movie", "cinema", "theater", "netflix", "spotify", "hulu", "disney",
        "game", "steam", "playstation", "xbox", "twitch", "ticket", "concert",
        "music", "entertainment", "park", "arcade", "bowling"
    ],
    "shopping": [
        "amazon", "ebay", "shop", "store", "mall", "retail", "clothing",
        "apparel", "fashion", "nike", "adidas", "gap", "h&m", "zara",
        "uniqlo", "forever 21", "shein", "target", "kohl's", "walmart"
    ],
    "transportation": [
        "uber", "lyft", "taxi", "cab", "gas", "fuel", "shell", "chevron",
        "exxon", "bp", "parking", "transit", "metro", "train", "bus",
        "airline", "flight", "airport", "car rental"
    ],
    "utilities": [
        "electric", "water", "gas", "internet", "phone", "cable", "verizon",
        "at&t", "t-mobile", "comcast", "spectrum", "utility", "power"
    ],
    "health": [
        "pharmacy", "doctor", "hospital", "clinic", "dentist", "gym",
        "yoga", "fitness", "health", "cvs", "walgreens", "medical",
        "vitamin", "supplement", "wellness", "therapist", "counselor"
    ],
    "subscriptions": [
        "subscription", "monthly", "annual", "membership", "plan", "premium",
        "app store", "play store", "patreon", "adobe", "microsoft"
    ],
    "utilities_bills": [
        "rent", "mortgage", "landlord", "lease", "property management"
    ],
}

# Wasteful category keywords - spending that should be marked as wasteful
WASTEFUL_KEYWORDS = [
    "delivery fee", "subscription", "impulse buy", "online shopping",
    "fast food", "coffee", "snack", "candy", "soda", "energy drink",
    "gambling", "casino", "lottery", "dating app", "premium", "upgrade"
]

class TransactionCategorizer:
    """Automatically categorize transactions based on merchant name and metadata"""

    def __init__(self):
        # Build lowercase keyword maps for faster matching
        self.categories = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            self.categories[category] = [k.lower() for k in keywords]

        self.wasteful_keywords = [w.lower() for w in WASTEFUL_KEYWORDS]

    def categorize(self, merchant_name: str, amount: float = None,
                   description: str = None) -> str:
        """
        Categorize a transaction based on merchant name and optional metadata.

        Args:
            merchant_name: Name of the merchant/vendor
            amount: Transaction amount (optional)
            description: Additional description (optional)

        Returns:
            Category string, or "other" if no match found
        """
        if not merchant_name:
            return "other"

        # Combine all text for matching
        text_to_match = merchant_name.lower()
        if description:
            text_to_match += " " + description.lower()

        # Score each category based on keyword matches
        category_scores = {}
        for category, keywords in self.categories.items():
            score = self._score_category(text_to_match, keywords)
            if score > 0:
                category_scores[category] = score

        # Return category with highest score
        if category_scores:
            best_category = max(category_scores, key=category_scores.get)
            return best_category

        return "other"

    def is_wasteful(self, merchant_name: str, category: str = None,
                    description: str = None) -> bool:
        """
        Determine if a transaction is wasteful based on merchant and category.

        Args:
            merchant_name: Name of the merchant/vendor
            category: Transaction category (optional)
            description: Additional description (optional)

        Returns:
            True if likely wasteful, False otherwise
        """
        if not merchant_name:
            return False

        text_to_match = merchant_name.lower()
        if description:
            text_to_match += " " + description.lower()

        # Check if any wasteful keywords match
        for keyword in self.wasteful_keywords:
            if keyword in text_to_match:
                return True

        # Category-based wasteful detection
        if category:
            wasteful_categories = ["entertainment", "subscriptions", "food"]
            if category in wasteful_categories:
                # Check if it's excessive spending in these categories
                # (This is simplified - could be enhanced with amount analysis)
                if category == "food":
                    # Fast food/delivery is wasteful
                    if any(k in text_to_match for k in ["doordash", "uber eats", "grubhub", "fast food"]):
                        return True
                elif category == "entertainment":
                    # Most entertainment spending is discretionary
                    return True

        return False

    def _score_category(self, text: str, keywords: List[str]) -> float:
        """
        Score how well text matches a category based on keywords.

        Args:
            text: Text to match against
            keywords: List of keywords for this category

        Returns:
            Score (higher = better match)
        """
        score = 0.0

        for keyword in keywords:
            if keyword in text:
                # Exact word boundary match scores higher
                if self._is_word_boundary_match(text, keyword):
                    score += 2.0
                else:
                    score += 1.0

        return score

    def _is_word_boundary_match(self, text: str, keyword: str) -> bool:
        """Check if keyword matches at word boundaries"""
        pattern = r'\b' + re.escape(keyword) + r'\b'
        return bool(re.search(pattern, text))

    def get_category_suggestions(self, merchant_name: str, top_n: int = 3) -> List[Tuple[str, float]]:
        """
        Get top N category suggestions for a merchant with confidence scores.

        Args:
            merchant_name: Name of the merchant
            top_n: Number of suggestions to return

        Returns:
            List of (category, score) tuples sorted by score
        """
        text = merchant_name.lower()
        scores = {}

        for category, keywords in self.categories.items():
            score = self._score_category(text, keywords)
            if score > 0:
                scores[category] = score

        # Return top N sorted by score
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_scores[:top_n]


# Global categorizer instance
_categorizer = None

def get_categorizer() -> TransactionCategorizer:
    """Get or create the global categorizer instance"""
    global _categorizer
    if _categorizer is None:
        _categorizer = TransactionCategorizer()
    return _categorizer


def categorize_transaction(merchant_name: str, amount: float = None,
                           description: str = None) -> str:
    """
    Convenience function to categorize a single transaction.

    Args:
        merchant_name: Name of the merchant/vendor
        amount: Transaction amount (optional)
        description: Additional description (optional)

    Returns:
        Category string
    """
    return get_categorizer().categorize(merchant_name, amount, description)


def is_transaction_wasteful(merchant_name: str, category: str = None,
                            description: str = None) -> bool:
    """
    Convenience function to check if a transaction is wasteful.

    Args:
        merchant_name: Name of the merchant/vendor
        category: Transaction category (optional)
        description: Additional description (optional)

    Returns:
        True if likely wasteful, False otherwise
    """
    return get_categorizer().is_wasteful(merchant_name, category, description)
