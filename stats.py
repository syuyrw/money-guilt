from database import (
    get_all_transactions, get_transactions_by_category,
    get_wasteful_spending, get_total_spending, get_accounts
)
from datetime import datetime, timedelta
import random


def get_spending_stats(days=30):
    """Get various spending statistics"""
    return {
        'total_spending': get_total_spending(days),
        'wasteful_spending': get_wasteful_spending(days),
        'spending_by_category': get_transactions_by_category(days),
        'accounts': get_accounts(),
        'transactions': get_all_transactions(days),
    }


def generate_stats_message(days=30):
    """Generate different random stat messages to display"""
    stats = get_spending_stats(days)

    messages = []

    # Wasteful spending stat
    wasteful = stats['wasteful_spending']
    if wasteful['total'] > 0:
        messages.append({
            'type': 'wasteful_percentage',
            'title': 'Wasteful Spending',
            'value': f"${wasteful['total']:.2f}",
            'subtitle': f"{wasteful['count']} wasteful transactions this month"
        })

    # Top spending category
    categories = stats['spending_by_category']
    if categories:
        top_cat = categories[0]
        messages.append({
            'type': 'top_category',
            'title': 'Top Spending Category',
            'value': top_cat['category_primary'] or 'Uncategorized',
            'subtitle': f"${abs(top_cat['total']):.2f} ({top_cat['count']} transactions)"
        })

    # Largest single transaction
    transactions = stats['transactions']
    if transactions:
        largest_tx = max(transactions, key=lambda x: abs(x['amount']))
        messages.append({
            'type': 'largest_transaction',
            'title': 'Largest Spending',
            'value': largest_tx['name'],
            'subtitle': f"${largest_tx['amount']:.2f} on {largest_tx['date']}"
        })

    # Total spending
    total = stats['total_spending']
    if total > 0:
        messages.append({
            'type': 'total_spending',
            'title': 'Total Spending',
            'value': f"${total:.2f}",
            'subtitle': f"Over the past {days} days"
        })

    # Opportunity cost (what you could've done with wasteful money)
    if wasteful['total'] > 0:
        messages.append({
            'type': 'opportunity_cost',
            'title': 'What You Could Have Done',
            'value': _get_opportunity_cost_item(wasteful['total']),
            'subtitle': f"Instead of spending ${wasteful['total']:.2f} on wasteful things"
        })

    # Most common merchant
    if transactions:
        merchants = {}
        for tx in transactions:
            name = tx['name']
            merchants[name] = merchants.get(name, 0) + 1
        most_common = max(merchants.items(), key=lambda x: x[1])
        if most_common[1] > 1:
            messages.append({
                'type': 'most_common_merchant',
                'title': 'Favorite Merchant',
                'value': most_common[0],
                'subtitle': f"Visited {most_common[1]} times this month"
            })

    # Spending per day
    if transactions:
        spending_per_day = total / days
        messages.append({
            'type': 'spending_per_day',
            'title': 'Daily Average',
            'value': f"${spending_per_day:.2f}/day",
            'subtitle': f"Average daily spending"
        })

    return messages


def get_random_stat(days=30):
    """Get a random stat message"""
    messages = generate_stats_message(days)
    if messages:
        return random.choice(messages)
    return {
        'type': 'no_data',
        'title': 'No Data',
        'value': 'Link your account',
        'subtitle': 'Start tracking your spending'
    }


def _get_opportunity_cost_item(amount):
    """Suggest something the person could have bought with that money"""
    items = [
        ("A nice dinner", 50),
        ("Movie tickets", 30),
        ("Coffee for a month", 60),
        ("A book", 15),
        ("New shoes", 80),
        ("A video game", 60),
        ("Concert tickets", 100),
        ("A flight", 200),
        ("A nice bottle of wine", 40),
        ("A week of groceries", 100),
    ]

    # Find what they could have bought
    affordable_items = [item for item, cost in items if cost <= amount]

    if affordable_items:
        item = random.choice(affordable_items)
        return item

    return f"{int(amount)} coffees"


def get_spending_summary(days=30):
    """Get a text summary of spending"""
    stats = get_spending_stats(days)
    total = stats['total_spending']
    wasteful = stats['wasteful_spending']

    if wasteful['total'] > 0:
        wasteful_pct = (wasteful['total'] / total * 100) if total > 0 else 0
    else:
        wasteful_pct = 0

    return {
        'total_spending': f"${total:.2f}",
        'wasteful_spending': f"${wasteful['total']:.2f}",
        'wasteful_percentage': f"{wasteful_pct:.1f}%",
        'account_balance': stats['accounts'][0]['balance'] if stats['accounts'] else 0,
        'transaction_count': len(stats['transactions'])
    }
