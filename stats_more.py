"""Additional stats: small-cut habits, regret, timing, what-ifs, and leaks.

"Regret" means a transaction marked wasteful; "kept" means reviewed and not
wasteful. Each function returns a stat dict, or None when there isn't enough
data (or the needed setting) to say something honest.
"""

import os
from datetime import datetime, timedelta
from statistics import median

import paths

from database import get_db

paths.load_env()

DISCRETIONARY = ("ENTERTAINMENT", "FOOD_AND_DRINK", "GENERAL_MERCHANDISE",
                 "PERSONAL_CARE")
FEE_TERMS = ("overdraft", "atm fee", "atm surcharge", "late fee",
             "foreign transaction", "foreign exch", "nsf fee", "service charge")
PAYDAY_MIN = 500       # a deposit at least this large counts as income
INVEST_RETURN = 0.07
INVEST_YEARS = 10
LATE_NIGHT = (22, 3)   # 10pm up to 3am

# Plaid category first, falling back to whatever the user assigned.
CATEGORY_EXPR = "COALESCE(NULLIF(category_primary, ''), NULLIF(category, ''), 'Uncategorized')"


def _rows(sql, params=()):
    with get_db() as conn:
        return conn.execute(sql, params).fetchall()


def _money(x):
    return f"${x:,.2f}"


def _stat(kind, title, value, subtitle='', wasted=None, **data):
    stat = {'type': kind, 'title': title, 'value': value,
            'subtitle': subtitle, 'data': data}
    if wasted is not None:
        stat['wasted_text'] = _money(wasted)
    return stat


def _env_float(name):
    try:
        value = float(os.getenv(name, ''))
    except ValueError:
        return None
    return value if value > 0 else None


def _year_regret():
    row = _rows("""SELECT SUM(amount) AS t FROM transactions
                   WHERE is_wasteful = 1 AND amount > 0
                   AND date >= date('now', '-365 days')""")[0]
    return row['t'] or 0


# --- Death by a thousand cuts -------------------------------------------

def most_visited_merchant():
    rows = _rows("""SELECT name, COUNT(*) AS n FROM transactions
                    WHERE amount > 0 AND date >= date('now', '-30 days')
                    GROUP BY LOWER(name) ORDER BY n DESC, SUM(amount) DESC
                    LIMIT 1""")
    if not rows or rows[0]['n'] < 2:
        return None
    r = rows[0]
    return _stat('most_visited_merchant', 'Most Visited This Month',
                 r['name'], f"{r['n']} visits in the last 30 days", visits=r['n'])


def avg_days_between_impulse_buys():
    dates = sorted({r['date'] for r in _rows(
        "SELECT date FROM transactions WHERE is_wasteful = 1 AND amount > 0")})
    days = [datetime.strptime(d, '%Y-%m-%d') for d in dates]
    if len(days) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    avg = sum(gaps) / len(gaps)
    return _stat('impulse_gap', 'Days Between Impulse Buys', f"{avg:.1f} days",
                 f"across {len(days)} regretted purchase days", avg=avg)


# --- Regret ---------------------------------------------------------------

def regret_ratio():
    r = _rows("""SELECT
                    SUM(CASE WHEN is_wasteful = 1 THEN amount ELSE 0 END) AS regret,
                    SUM(CASE WHEN is_wasteful = 0 AND reviewed = 1 THEN amount ELSE 0 END) AS kept
                 FROM transactions WHERE amount > 0""")[0]
    regret, kept = r['regret'] or 0, r['kept'] or 0
    if regret + kept <= 0:
        return None
    pct = regret / (regret + kept) * 100
    return _stat('regret_ratio', 'Rated Dollars You Regret', f"{pct:.1f}%",
                 f"{_money(regret)} regret vs {_money(kept)} kept", regret=regret,
                 kept=kept)


def total_regret_all_time():
    total = _rows("""SELECT SUM(amount) AS t FROM transactions
                     WHERE is_wasteful = 1 AND amount > 0""")[0]['t'] or 0
    if total <= 0:
        return None
    return _stat('regret_total', 'Lifetime Regret', _money(total),
                 'and it only goes up', wasted=total, total=total)


def most_regretted_category():
    rows = _rows(f"""SELECT {CATEGORY_EXPR} AS grp, SUM(amount) AS t
                     FROM transactions WHERE is_wasteful = 1 AND amount > 0
                     GROUP BY grp ORDER BY t DESC LIMIT 1""")
    if not rows:
        return None
    grp = rows[0]['grp'].replace('_', ' ').title()
    return _stat('regret_category', 'Most Regretted Category', grp,
                 f"{_money(rows[0]['t'])} regretted", wasted=rows[0]['t'])


def most_regretted_merchant():
    rows = _rows("""SELECT name, COUNT(*) AS n, SUM(amount) AS t FROM transactions
                    WHERE is_wasteful = 1 AND amount > 0
                    GROUP BY LOWER(name) ORDER BY t DESC LIMIT 1""")
    if not rows:
        return None
    r = rows[0]
    return _stat('regret_merchant', 'Most Regretted Merchant', r['name'],
                 f"{_money(r['t'])} over {r['n']} purchases", wasted=r['t'])


def regret_streak():
    rows = _rows("""SELECT is_wasteful FROM transactions
                    WHERE amount > 0 AND (is_wasteful = 1 OR reviewed = 1)
                    ORDER BY date, created_at""")
    best = run = 0
    for r in rows:
        run = run + 1 if r['is_wasteful'] else 0
        best = max(best, run)
    if best < 2:
        return None
    return _stat('regret_streak', 'Longest Regret Streak', f"{best} in a row",
                 'consecutive regretted purchases', streak=best)


# --- Time-based -----------------------------------------------------------

def late_night_spending():
    # Plaid's datetime is often null; those transactions are left out since
    # there is no honest way to guess the hour.
    rows = _rows("""SELECT datetime, amount FROM transactions
                    WHERE amount > 0 AND datetime IS NOT NULL AND datetime != ''
                    AND date >= date('now', '-30 days')""")
    total = count = 0
    for r in rows:
        try:
            hour = datetime.fromisoformat(r['datetime'].replace('Z', '+00:00')).hour
        except ValueError:
            continue
        if hour >= LATE_NIGHT[0] or hour < LATE_NIGHT[1]:
            total += r['amount']
            count += 1
    if count == 0:
        return None
    return _stat('late_night', 'Late-Night Spending', _money(total),
                 f"{count} purchases between 10pm and 3am this month", wasted=total)


def weekend_vs_weekday():
    rows = _rows("""SELECT date, amount FROM transactions
                    WHERE amount > 0 AND date >= date('now', '-28 days')""")
    if not rows:
        return None
    weekend = sum(r['amount'] for r in rows
                  if datetime.strptime(r['date'], '%Y-%m-%d').weekday() >= 5)
    weekday = sum(r['amount'] for r in rows) - weekend
    # 28 days is exactly 4 weeks: 8 weekend days and 20 weekdays.
    we_day, wd_day = weekend / 8, weekday / 20
    if we_day <= 0 or wd_day <= 0:
        return None
    return _stat('weekend_weekday', 'Weekend vs Weekday',
                 f"{we_day / wd_day:.1f}x per day",
                 f"{_money(we_day)}/day weekends, {_money(wd_day)}/day weekdays",
                 weekend=we_day, weekday=wd_day)


def post_payday_spending():
    paydays = _rows("""SELECT date FROM transactions
                       WHERE amount <= ? AND date >= date('now', '-90 days')
                       GROUP BY date""", (-PAYDAY_MIN,))
    if not paydays:
        return None
    totals = []
    for p in paydays:
        d = datetime.strptime(p['date'], '%Y-%m-%d')
        end = (d + timedelta(days=2)).strftime('%Y-%m-%d')
        t = _rows("""SELECT SUM(amount) AS t FROM transactions
                     WHERE amount > 0 AND date > ? AND date <= ?""",
                  (p['date'], end))[0]['t'] or 0
        totals.append(t)
    avg = sum(totals) / len(totals)
    if avg <= 0:
        return None
    return _stat('post_payday', 'Spent Within 48h of Payday', _money(avg),
                 f"on average, across {len(totals)} paydays", wasted=avg)


def discretionary_change():
    marks = ",".join("?" * len(DISCRETIONARY))
    where = f"amount > 0 AND (category_primary IN ({marks}) OR category = 'eating out')"

    def window(start, end):
        return _rows(f"""SELECT SUM(amount) AS t FROM transactions WHERE {where}
                         AND date >= date('now', ?) AND date < date('now', ?)""",
                     DISCRETIONARY + (start, end))[0]['t'] or 0

    current, previous = window('-30 days', '+1 day'), window('-60 days', '-30 days')
    if previous <= 0 or current <= 0:
        return None
    change = (current - previous) / previous * 100
    return _stat('discretionary_change', 'Discretionary Spending',
                 f"{change:+.0f}%", f"{_money(current)} vs {_money(previous)} the "
                 "month before", current=current, previous=previous)


# --- What it could have been ---------------------------------------------

def work_hours_equivalent():
    rate, wasted = _env_float('HOURLY_RATE'), _year_regret()
    if not rate or wasted <= 0:
        return None
    hours = wasted / rate
    return _stat('work_hours', 'Hours of Your Life Wasted', f"{hours:.1f} hours",
                 f"{_money(wasted)} this year at {_money(rate)}/hr", wasted=wasted)


def invested_alternative():
    wasted = _year_regret()
    if wasted <= 0:
        return None
    future = wasted * (1 + INVEST_RETURN) ** INVEST_YEARS
    return _stat('invested', f"Regret, Invested for {INVEST_YEARS} Years",
                 _money(future),
                 f"{_money(wasted)} at {INVEST_RETURN:.0%} a year", wasted=wasted)


def rent_equivalent():
    rent, wasted = _env_float('MONTHLY_RENT'), _year_regret()
    if not rent or wasted <= 0:
        return None
    weeks = wasted / (rent * 12 / 52)
    return _stat('rent_equiv', 'Regret in Weeks of Rent', f"{weeks:.1f} weeks",
                 f"{_money(wasted)} this year", wasted=wasted)


# --- Subscriptions and leaks ---------------------------------------------

def find_recurring():
    """Monthly-ish charges: 3+ from a merchant, ~30 days apart, similar amounts."""
    rows = _rows("""SELECT name, date, amount, is_wasteful, reviewed FROM transactions
                    WHERE amount > 0 AND date >= date('now', '-150 days')
                    ORDER BY date""")
    groups = {}
    for r in rows:
        groups.setdefault(r['name'].lower(), []).append(r)

    found = []
    for charges in groups.values():
        if len(charges) < 3:
            continue
        amounts = [c['amount'] for c in charges]
        typical = median(amounts)
        if any(abs(a - typical) > 0.15 * typical for a in amounts):
            continue
        days = [datetime.strptime(c['date'], '%Y-%m-%d') for c in charges]
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        if not 25 <= median(gaps) <= 35:
            continue
        found.append({
            'name': charges[0]['name'], 'monthly': typical,
            'kept': any(c['reviewed'] and not c['is_wasteful'] for c in charges),
        })
    return found


def recurring_per_month():
    found = find_recurring()
    if not found:
        return None
    total = sum(f['monthly'] for f in found)
    return _stat('recurring', 'Recurring Charges Per Month', _money(total),
                 f"across {len(found)} subscriptions", wasted=total)


def forgotten_subscriptions():
    forgotten = [f for f in find_recurring() if not f['kept']]
    if not forgotten:
        return None
    total = sum(f['monthly'] for f in forgotten)
    names = ", ".join(f['name'] for f in forgotten[:3])
    return _stat('forgotten_subs', 'Possibly Forgotten Subscriptions',
                 f"{_money(total)}/mo", f"{names}: regretted or never rated",
                 wasted=total)


def fees_paid():
    clause = " OR ".join("LOWER(name) LIKE ?" for _ in FEE_TERMS)
    r = _rows(f"""SELECT SUM(amount) AS t, COUNT(*) AS n FROM transactions
                  WHERE amount > 0 AND ({clause}
                  OR category LIKE 'BANK_FEES%' OR category_primary = 'BANK_FEES')
                  AND date >= date('now', '-365 days')""",
              [f"%{t}%" for t in FEE_TERMS])[0]
    if not r['t']:
        return None
    return _stat('fees', 'Fees Paid This Year', _money(r['t']),
                 f"{r['n']} fees, nothing to show for it", wasted=r['t'])


ALL_STATS = [
    most_visited_merchant, avg_days_between_impulse_buys, regret_ratio,
    total_regret_all_time, most_regretted_category, most_regretted_merchant,
    regret_streak, late_night_spending, weekend_vs_weekday,
    post_payday_spending, discretionary_change, work_hours_equivalent,
    invested_alternative, rent_equivalent, recurring_per_month,
    forgotten_subscriptions, fees_paid,
]


def generate_more_stats():
    stats = []
    for fn in ALL_STATS:
        stat = fn()
        if stat:
            stats.append(stat)
    return stats
