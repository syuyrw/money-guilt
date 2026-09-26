"""Transaction categorization engine for Money Guilt with machine learning"""

import re
import json
import os

import paths
from typing import Dict, List, Tuple, Optional

# Category keywords - maps merchant keywords to spending categories
CATEGORY_KEYWORDS = {
    "fees": [
        "overdraft", "nsf fee", "insufficient funds", "atm fee",
        "atm surcharge", "late fee", "maintenance fee", "service charge",
        "monthly fee", "interest charge", "finance charge"
    ],
    "convenience store": [
        "7-eleven", "7 eleven", "circle k", "wawa", "sheetz", "quiktrip",
        "casey's", "convenience", "kwik"
    ],
    "eating out": [
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
        "uniqlo", "forever 21", "shein", "temu", "aliexpress", "kohl's", "best buy", "apple"
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

# Prior chance that spending in a category is waste. The numbers are a
# ranking with a 0.5 cutoff, not measured rates: the surveys report how many
# people name something as a source of waste, not what share of transactions
# are. All are self-reported, online-panel results. Anything you teach the
# app about a specific merchant overrides them.
WASTE_PRIORS = {
    "fees": 0.95,               # buys nothing; overdraft ~$27-31, ATM ~$4.86 (Bankrate 2025)
    "convenience store": 0.6,   # 26% name it a source of waste (Motley Fool/Pollfish, Jan 2026)
    "subscriptions": 0.45,      # 42% have paid for a forgotten one (C+R Research)
    "shopping": 0.4,            # clothing/luxury is the most-regretted purchase, 19% (Omni, Jan 2026)
    "entertainment": 0.4,       # experiences are regretted less than goods (Gilovich)
    "eating out": 0.4,          # dining out is the #1 named waste at 31%, but a meal isn't waste itself
}
DEFAULT_WASTE_PRIOR = 0.1

# Merchant cues that move the prior.
DELIVERY_CUES = ["doordash", "uber eats", "ubereats", "grubhub", "postmates",
                 "seamless", "caviar", "delivery"]          # 20% cite unneeded delivery orders (Motley Fool)
MARKETPLACE_CUES = ["amazon", "temu", "shein", "ebay", "aliexpress"]  # online impulse buys, 26% (Motley Fool)
STREAMING_CUES = ["netflix", "hulu", "disney", "hbo", "peacock", "paramount",
                  "spotify", "apple music", "youtube premium"]  # unused streaming: 9-26% by generation
FEE_CUES = CATEGORY_KEYWORDS["fees"]
# Spending with little claim to be anything else. Kept from the original list
# minus "coffee": lists of "money wasters" name it, but the latte-factor
# critiques find skipping small treats rarely helps, so it's left to the prior.
STRONG_WASTE_CUES = ["delivery fee", "impulse", "fast food", "snack", "candy",
                     "soda", "energy drink", "gambling", "casino", "lottery",
                     "premium", "upgrade"]
WASTE_THRESHOLD = 0.5


def _plain(text: str) -> str:
    """Lower-case with apostrophes dropped, so "McDonald's" matches "mcdonalds"."""
    return text.lower().replace("'", "").replace("\u2019", "")


class TransactionCategorizer:
    """Categorize transactions with user learning capabilities"""

    def __init__(self, overrides_file: str = None):
        # Build lowercase keyword maps
        self.categories = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            self.categories[category] = [_plain(k) for k in keywords]

        self.wasteful_keywords = [w.lower() for w in STRONG_WASTE_CUES]

        # Learned merchant overrides from user manual categorizations
        self.overrides_file = overrides_file or paths.overrides_path()
        self.merchant_overrides, self.merchant_wasteful = self._load_overrides()

    def _load_overrides(self) -> Tuple[Dict[str, str], Dict[str, bool]]:
        """Load learned categories and wasteful flags from file."""
        if os.path.exists(self.overrides_file):
            try:
                with open(self.overrides_file, 'r') as f:
                    data = json.load(f)
            except Exception as e:
                print(f"Error loading overrides: {e}")
                return {}, {}

            if isinstance(data.get('categories'), dict):
                return data['categories'], data.get('wasteful', {})

            # Files written before wasteful flags were persisted held a bare
            # {merchant: category} map. Carry those categories over.
            if isinstance(data, dict):
                return data, {}

        return {}, {}

    def _save_overrides(self):
        """Save learned categories and wasteful flags to file."""
        try:
            with open(self.overrides_file, 'w') as f:
                json.dump({'categories': self.merchant_overrides,
                           'wasteful': self.merchant_wasteful}, f, indent=2)
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
        text_to_match = _plain(merchant_lower)
        if description:
            text_to_match += " " + _plain(description)

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

    def waste_score(self, merchant_name: str, category: str = None,
                    description: str = None) -> float:
        """Likelihood, 0 to 1, that a purchase is waste, from research priors.

        Ignores anything the user has taught; is_wasteful applies that first.
        """
        if not merchant_name:
            return 0.0

        text = _plain(merchant_name)
        if description:
            text += " " + _plain(description)

        if any(cue in text for cue in FEE_CUES):
            return WASTE_PRIORS["fees"]

        if not category:
            category = self.categorize(merchant_name, description=description)
        score = WASTE_PRIORS.get(category, DEFAULT_WASTE_PRIOR)

        if any(cue in text for cue in STRONG_WASTE_CUES):
            score += 0.6
        if any(cue in text for cue in DELIVERY_CUES):
            score += 0.35
        if any(cue in text for cue in MARKETPLACE_CUES):
            score += 0.15
        if any(cue in text for cue in STREAMING_CUES):
            score += 0.2
        return min(score, 1.0)

    def is_wasteful(self, merchant_name: str, category: str = None,
                    description: str = None) -> bool:
        """
        Determine if a transaction is wasteful.

        1. What the user taught for this merchant, exact or partial
        2. Otherwise the research-based score against WASTE_THRESHOLD
        """
        if not merchant_name:
            return False

        merchant_lower = merchant_name.lower()

        if merchant_lower in self.merchant_wasteful:
            return self.merchant_wasteful[merchant_lower]

        # A partial match returns the flag as taught, including False, so a
        # merchant taught as not wasteful isn't overruled by the score when
        # it arrives as "Netflix.com Subscription".
        for learned_merchant, flag in self.merchant_wasteful.items():
            if learned_merchant in merchant_lower or merchant_lower in learned_merchant:
                return flag

        return self.waste_score(merchant_name, category, description) >= WASTE_THRESHOLD

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
        # Cheap purchases likely groceries or eating out
        if amount < 20:
            scores["eating out"] = 1.0
            scores["groceries"] = 1.0
        # Mid-range could be eating out or shopping
        elif amount < 100:
            scores["eating out"] = 0.5
            scores["shopping"] = 0.5
        # Large purchases likely shopping or utilities. Not "> 100", which
        # leaves a charge of exactly 100 matching no branch at all.
        else:
            scores["shopping"] = 0.5
            scores["utilities"] = 0.3
        return scores

    def get_category_suggestions(self, merchant_name: str,
                                top_n: int = 3) -> List[Tuple[str, float]]:
        """Get top category suggestions with scores"""
        text = _plain(merchant_name)
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
