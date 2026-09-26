from database import (
    get_all_transactions, get_transactions_by_category,
    get_wasteful_spending, get_total_spending, get_accounts
)
from datetime import datetime, timedelta
import random


VACATION_IDEAS = [
    {"name": "Weekend in Cancun", "cost": 1000},
    {"name": "Week in Costa Rica", "cost": 2000},
    {"name": "Paris & London trip", "cost": 3500},
    {"name": "Iceland adventure", "cost": 2500},
    {"name": "Japan exploration", "cost": 4000},
    {"name": "Caribbean cruise", "cost": 2000},
    {"name": "Hawaii vacation", "cost": 1500},
    {"name": "European tour", "cost": 5000},
    {"name": "Bali resort week", "cost": 1800},
    {"name": "New Zealand adventure", "cost": 3000},
    {"name": "Thailand expedition", "cost": 2200},
    {"name": "Ski trip to Colorado", "cost": 1200},
    {"name": "Safari in Kenya", "cost": 3500},
    {"name": "Norway fjord cruise", "cost": 2800},
    {"name": "Greek islands escape", "cost": 2100},
]


def get_wasted_by_period():
    """Get wasted spending by different time periods"""
    # This week
    this_week = get_wasteful_spending_for_days(7)['total']

    # This month
    this_month = get_wasteful_spending_for_days(30)['total']

    # This year
    this_year = get_wasteful_spending_for_days(365)['total']

    return {
        'week': this_week,
        'month': this_month,
        'year': this_year,
    }


def get_wasteful_spending_for_days(days):
    """Get wasteful spending for specific number of days"""
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                SUM(amount) as total_wasteful,
                COUNT(*) as count
            FROM transactions
            WHERE is_wasteful = 1
            AND date >= date('now', '-' || ? || ' days')
        """, (days,))
        result = cursor.fetchone()
    return {
        'total': result['total_wasteful'] or 0,
        'count': result['count'] or 0
    }


def get_wasteful_percentage():
    """Get percentage of spending that is wasteful"""
    total = get_total_spending(365)  # Year
    wasteful = get_wasteful_spending(365)['total']

    if total > 0:
        percentage = (wasteful / total * 100)
    else:
        percentage = 0

    return {
        'percentage': percentage,
        'total_spending': total,
        'total_wasteful': wasteful,
    }


def get_top_wasteful_vendor():
    """Get the vendor where most wasteful money was spent"""
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                name,
                COUNT(*) as count,
                SUM(amount) as total
            FROM transactions
            WHERE is_wasteful = 1
            AND date >= date('now', '-365 days')
            GROUP BY name
            ORDER BY total DESC
            LIMIT 1
        """)
        result = cursor.fetchone()

    if result:
        return {
            'vendor': result['name'],
            'count': result['count'],
            'total': result['total'],
        }
    return None


DELIVERY_MARKUP = 0.30
DELIVERY_VENDORS = ["doordash", "uber eats", "ubereats", "grubhub", "postmates",
                    "seamless", "caviar", "instacart", "gopuff"]


def get_small_purchase_total(days=30, threshold=10):
    """Sum and count of purchases under the threshold"""
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SUM(amount) as total, COUNT(*) as count
            FROM transactions
            WHERE amount > 0 AND amount < ?
            AND date >= date('now', '-' || ? || ' days')
        """, (threshold, days))
        result = cursor.fetchone()
    return {'total': result['total'] or 0, 'count': result['count'] or 0}


def get_food_and_drink_projection(days=30):
    """Food and drink spending over the last `days`, scaled to a full year"""
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE amount > 0
            AND (category_primary = 'FOOD_AND_DRINK' OR category = 'eating out')
            AND date >= date('now', '-' || ? || ' days')
        """, (days,))
        total = cursor.fetchone()['total'] or 0
    return {'month': total, 'year': total * 365 / days}


def get_delivery_markup(days=30):
    """Delivery-app spending and the estimated markup paid on it"""
    from database import get_db
    clause = " OR ".join("LOWER(name) LIKE ?" for _ in DELIVERY_VENDORS)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE amount > 0 AND ({clause})
            AND date >= date('now', '-' || ? || ' days')
        """, [f"%{v}%" for v in DELIVERY_VENDORS] + [days])
        total = cursor.fetchone()['total'] or 0
    return {'spent': total, 'markup': total * DELIVERY_MARKUP}


def get_vacation_suggestion(year_wasted):
    """Get a vacation suggestion based on wasted money"""
    # Find vacations they could afford
    affordable = [v for v in VACATION_IDEAS if v['cost'] <= year_wasted]

    if affordable:
        return random.choice(affordable)

    # If nothing affordable, suggest the cheapest
    return min(VACATION_IDEAS, key=lambda x: x['cost'])


def generate_stats_list():
    """Generate all available stats"""
    stats = []

    # Wasted by period
    wasted = get_wasted_by_period()

    # Stat 1: Total wasted this year
    if wasted['year'] > 0:
        stats.append({
            'type': 'wasted_year',
            'title': 'Total Wasted This Year',
            'value': f"${wasted['year']:.2f}",
            'subtitle': '',
            'wasted_text': f"${wasted['year']:.2f}",
            'data': {'amount': wasted['year']}
        })

    # Stat 2: Total wasted this month
    if wasted['month'] > 0:
        stats.append({
            'type': 'wasted_month',
            'title': 'Total Wasted This Month',
            'value': f"${wasted['month']:.2f}",
            'subtitle': 'Money spent on wasteful purchases',
            'wasted_text': f"${wasted['month']:.2f}",
            'data': {'amount': wasted['month']}
        })

    # Stat 3: Total wasted this week
    if wasted['week'] > 0:
        stats.append({
            'type': 'wasted_week',
            'title': 'Total Wasted This Week',
            'value': f"${wasted['week']:.2f}",
            'subtitle': 'Money spent on wasteful purchases',
            'wasted_text': f"${wasted['week']:.2f}",
            'data': {'amount': wasted['week']}
        })

    # Stat 4: Percentage wasted
    pct_data = get_wasteful_percentage()
    if pct_data['total_spending'] > 0:
        stats.append({
            'type': 'wasted_percentage',
            'title': 'Percent of Spending Wasted',
            'value': f"{pct_data['percentage']:.1f}%",
            # No wasted_text: this figure is total spending, not waste.
            'subtitle': f"Out of ${pct_data['total_spending']:.2f} total spending",
            'data': {
                'percentage': pct_data['percentage'],
                'total': pct_data['total_spending'],
                'wasteful': pct_data['total_wasteful']
            }
        })

    # Stat 5: Vacation suggestion
    if wasted['year'] > 0:
        vacation = get_vacation_suggestion(wasted['year'])
        # "European" opens with a "yoo" sound, so it takes "a" despite the
        # leading vowel.
        name = vacation['name'].lower()
        article = 'an' if name[0] in 'aeiou' and not name.startswith('eu') else 'a'
        stats.append({
            'type': 'vacation_idea',
            'title': f"You could have afforded {article}",
            'value': vacation['name'],
            'subtitle': f"Instead of wasting ${wasted['year']:.2f} this year",
            'wasted_text': f"${wasted['year']:.2f}",
            'data': {'cost': vacation['cost'], 'wasted': wasted['year']}
        })

    # Stat 6: Top wasteful vendor
    vendor = get_top_wasteful_vendor()
    if vendor:
        stats.append({
            'type': 'top_wasteful_vendor',
            'title': 'Biggest Waste Vendor',
            'value': vendor['vendor'],
            # Second line of the value, so the amount matches the vendor's
            # size rather than shrinking into the subtitle.
            'value_extra': f"${vendor['total']:.2f}",
            'subtitle': f"wasted over {vendor['count']} purchases",
            'wasted_text': f"${vendor['total']:.2f}",
            'data': vendor
        })

    # Stat 7: Small purchases this month
    small = get_small_purchase_total()
    if small['total'] > 0:
        stats.append({
            'type': 'small_purchases',
            'title': 'Purchases Under $10 This Month',
            'value': f"${small['total']:.2f}",
            'subtitle': f"across {small['count']} small purchases",
            'wasted_text': f"${small['total']:.2f}",
            'data': small
        })

    # Stat 8: Food and drink, annualized
    food = get_food_and_drink_projection()
    if food['month'] > 0:
        stats.append({
            'type': 'food_yearly_projection',
            'title': 'Food & Drink Per Year',
            'value': f"${food['year']:.2f}",
            'subtitle': f"at this month's ${food['month']:.2f} pace",
            'wasted_text': f"${food['year']:.2f}",
            'data': food
        })

    # Stat 9: Delivery markup
    delivery = get_delivery_markup()
    if delivery['markup'] > 0:
        stats.append({
            'type': 'delivery_markup',
            'title': 'Paid Just to Not Go Get It',
            'value': f"${delivery['markup']:.2f}",
            'subtitle': f"est. {DELIVERY_MARKUP:.0%} markup on ${delivery['spent']:.2f} of delivery",
            'wasted_text': f"${delivery['markup']:.2f}",
            'data': delivery
        })

    from stats_more import generate_more_stats
    stats.extend(generate_more_stats())

    return stats if stats else [get_no_data_stat()]


def get_no_data_stat():
    """Return a stat when no data is available"""
    return {
        'type': 'no_data',
        'title': 'No Data Yet',
        'value': 'Mark transactions as wasteful',
        'subtitle': 'to see spending insights',
        'data': {}
    }


def get_random_stat():
    """Get a random stat from available stats"""
    stats = generate_stats_list()
    if stats:
        return random.choice(stats)
    return get_no_data_stat()


def get_all_stats():
    """Get all stats at once"""
    return generate_stats_list()
