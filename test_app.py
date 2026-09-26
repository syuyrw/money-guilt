"""Test suite for Money Guilt.

Run with:  python test_app.py

Everything runs against a throwaway database and throwaway overrides
files in a temp directory. The real money_guilt.db and
merchant_overrides.json are never opened, so this is safe to run at any
time. Exits non-zero if anything fails.
"""
import os
import sys
import json
import shutil
import tempfile
import traceback
from datetime import datetime, timedelta
from types import SimpleNamespace

# Must be set before PyQt is imported, so the widget tests need no display.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

import database

SANDBOX = None
PASSED, FAILED = [], []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
    except Exception as exc:
        FAILED.append((name, f"{type(exc).__name__}: {exc}",
                       traceback.format_exc()))


def eq(actual, expected, msg=''):
    assert actual == expected, f"expected {expected!r}, got {actual!r} {msg}"


def sandbox_path(name):
    return os.path.join(SANDBOX, name)


def day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime('%Y-%m-%d')


def fake_tx(tid, name, amount, days_ago, primary='FOOD', detailed='FOOD_FAST'):
    """Stands in for a Plaid transaction object."""
    return SimpleNamespace(
        transaction_id=tid, account_id='acct1', date=day(-days_ago),
        name=name, amount=amount,
        personal_finance_category=SimpleNamespace(
            primary=primary, detailed=detailed))


def fake_account(aid, name, balance):
    return SimpleNamespace(account_id=aid, name=name, type='depository',
                           subtype='checking',
                           balances=SimpleNamespace(current=balance))


# --------------------------------------------------------------- database
def seed_database():
    database.init_db()
    database.save_accounts([fake_account('acct1', 'Checking', 1500.0)])
    database.save_transactions([
        fake_tx('t1', 'Starbucks', 6.50, 2),
        fake_tx('t2', 'Whole Foods', 82.10, 5),
        fake_tx('t3', 'Netflix', 15.99, 20),
        fake_tx('t4', 'Shell Gas', 45.00, 100),
        fake_tx('t5', 'Payroll Deposit', -2000.0, 3),
        fake_tx('t6', 'Starbucks', 7.25, 400),
    ])


def table_names():
    with database.get_db() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()
    return sorted(r['name'] for r in rows)


def t_db_init():
    seed_database()
    eq(table_names(), ['accounts', 'categories', 'transactions'])


def t_db_accounts():
    rows = database.get_accounts()
    eq(len(rows), 1)
    eq(rows[0]['name'], 'Checking')
    eq(rows[0]['balance'], 1500.0)


def t_db_no_duplicates():
    before = len(database.get_all_transactions(days=9999))
    database.save_transactions([fake_tx('t1', 'Starbucks', 6.50, 2)])
    eq(len(database.get_all_transactions(days=9999)), before,
       "re-saving the same id must not duplicate")


def t_db_day_window():
    ids = {r['id'] for r in database.get_all_transactions(days=7)}
    assert 't1' in ids and 't2' in ids, ids
    assert 't3' not in ids, "a 20-day-old row leaked into the 7-day window"
    assert 't6' not in ids, "a 400-day-old row leaked into the 7-day window"


def t_db_by_category():
    rows = database.get_transactions_by_category(days=9999)
    assert any(r['category_primary'] == 'FOOD' for r in rows), rows


def t_db_mark_wasteful():
    database.mark_transaction_wasteful('t1', 1)
    database.mark_transaction_wasteful('t3', 1)
    spend = database.get_wasteful_spending(days=30)
    eq(round(spend['total'], 2), 22.49)
    eq(spend['count'], 2)


def t_db_empty_window():
    spend = database.get_wasteful_spending(days=1)
    eq(spend['total'], 0, "an empty window must total 0, not None")
    eq(spend['count'], 0)


def t_db_excludes_income():
    # 6.50 + 82.10 + 15.99 + 45.00 + 7.25; the -2000 payroll is excluded
    eq(round(database.get_total_spending(days=9999), 2), 156.84)


def t_db_unknown_id():
    database.mark_transaction_wasteful('does-not-exist', 1)  # must not raise


DATABASE_TESTS = [
    ("db: init_db creates the three tables", t_db_init),
    ("db: save_accounts / get_accounts", t_db_accounts),
    ("db: re-saving a transaction id does not duplicate", t_db_no_duplicates),
    ("db: get_all_transactions honours the day window", t_db_day_window),
    ("db: get_transactions_by_category groups", t_db_by_category),
    ("db: mark_transaction_wasteful + get_wasteful_spending", t_db_mark_wasteful),
    ("db: wasteful spending over an empty window returns 0", t_db_empty_window),
    ("db: get_total_spending excludes income", t_db_excludes_income),
    ("db: marking an unknown id is a no-op", t_db_unknown_id),
]


# ------------------------------------------------------------- categorizer
def fresh_categorizer():
    import categorizer
    return categorizer.TransactionCategorizer(
        overrides_file=sandbox_path(f'ov_{os.urandom(4).hex()}.json'))


def t_cat_keywords():
    c = fresh_categorizer()
    eq(c.categorize('Starbucks Coffee'), 'eating out')
    eq(c.categorize('Whole Foods Market'), 'groceries')
    eq(c.categorize('Netflix'), 'entertainment')
    eq(c.categorize('Shell'), 'transportation')


def t_cat_empty_input():
    c = fresh_categorizer()
    eq(c.categorize(''), 'other')
    eq(c.categorize(None), 'other')
    eq(c.is_wasteful(''), False)
    eq(c.is_wasteful(None), False)


def t_cat_category_persists():
    import categorizer
    path = sandbox_path('persist_category.json')
    first = categorizer.TransactionCategorizer(overrides_file=path)
    first.learn_merchant_category('Blue Bottle', 'eating out', True)
    second = categorizer.TransactionCategorizer(overrides_file=path)
    eq(second.categorize('Blue Bottle'), 'eating out')


def t_cat_wasteful_persists():
    """Regression: the flag used to live only in memory."""
    import categorizer
    path = sandbox_path('persist_wasteful.json')
    first = categorizer.TransactionCategorizer(overrides_file=path)
    first.learn_merchant_category('Cigar Shop', 'shopping', True)
    second = categorizer.TransactionCategorizer(overrides_file=path)
    eq(second.is_wasteful('Cigar Shop'), True,
       "a wasteful flag must survive a restart")


def t_cat_legacy_file_migrates():
    """Regression: files predating wasteful flags must not lose data."""
    import categorizer
    path = sandbox_path('legacy.json')
    with open(path, 'w') as fh:
        json.dump({"uber 063015 sf**pool**": "transportation",
                   "united airlines": "transportation"}, fh)

    first = categorizer.TransactionCategorizer(overrides_file=path)
    eq(first.categorize('Uber 063015 SF**POOL**'), 'transportation')
    eq(first.categorize('United Airlines'), 'transportation')

    first.learn_merchant_category('Dunkin', 'eating out', True)
    second = categorizer.TransactionCategorizer(overrides_file=path)
    eq(second.categorize('United Airlines'), 'transportation',
       "the pre-existing entry must survive the format change")
    eq(second.categorize('Dunkin'), 'eating out')
    eq(second.is_wasteful('Dunkin'), True)


def t_cat_partial_match():
    c = fresh_categorizer()
    c.learn_merchant_category('Starbucks', 'eating out')
    eq(c.categorize('Starbucks Downtown #4471'), 'eating out')


def t_cat_override_beats_keyword():
    c = fresh_categorizer()
    c.learn_merchant_category('Netflix', 'subscriptions')
    eq(c.categorize('Netflix'), 'subscriptions')


def t_cat_not_wasteful_respected():
    c = fresh_categorizer()
    c.learn_merchant_category('Netflix', 'entertainment', False)
    eq(c.is_wasteful('Netflix'), False)


def t_cat_partial_honours_not_wasteful():
    """Regression: the partial-match path used to ignore a stored False."""
    c = fresh_categorizer()
    c.learn_merchant_category('Netflix', 'subscriptions', False)
    eq(c.is_wasteful('Netflix.com Subscription'), False,
       "a partial match must honour a not-wasteful lesson")


def t_cat_partial_propagates_wasteful():
    c = fresh_categorizer()
    c.learn_merchant_category('Starbucks', 'eating out', True)
    eq(c.is_wasteful('Starbucks Downtown #4471'), True)


def t_cat_exactly_one_hundred():
    """Regression: 100.00 matched neither "< 100" nor "> 100"."""
    c = fresh_categorizer()
    assert c.categorize('Zzz Unknown Merchant', amount=100.0) != 'other'


def t_cat_amount_fallback():
    c = fresh_categorizer()
    for amount in (19.0, 50.0, 100.0, 150.0):
        got = c.categorize('Zzz Unknown Merchant', amount=amount)
        assert got != 'other', (amount, got)


def t_cat_corrupt_file():
    import categorizer
    path = sandbox_path('corrupt.json')
    with open(path, 'w') as fh:
        fh.write('{not json')
    c = categorizer.TransactionCategorizer(overrides_file=path)
    eq(c.categorize('Starbucks'), 'eating out')


def t_cat_suggestions():
    c = fresh_categorizer()
    ranked = c.get_category_suggestions('Starbucks Coffee', top_n=3)
    assert ranked and ranked[0][0] == 'eating out', ranked


def t_cat_word_boundary():
    c = fresh_categorizer()
    # "gas" appears in both the transportation and utilities keyword lists
    assert c.categorize('Chevron Gas Station') in ('transportation', 'utilities')



def t_cat_fees_flagged():
    c = fresh_categorizer()
    for name in ("Overdraft Fee", "ATM Fee", "Late Fee",
                 "Monthly Maintenance Fee", "Interest Charge"):
        eq(c.categorize(name), 'fees', name)
        assert c.is_wasteful(name), name


def t_cat_delivery_apps_flagged():
    """Regression: 'ubereats' never matched the merchant text 'uber eats'."""
    c = fresh_categorizer()
    for name in ("Uber Eats", "DoorDash", "Grubhub", "Postmates"):
        assert c.is_wasteful(name), name
    assert not c.is_wasteful("Uber"), "a plain ride is not a delivery"


def t_cat_convenience_flagged():
    c = fresh_categorizer()
    for name in ("7-Eleven", "Circle K", "Wawa", "Casey's General Store"):
        eq(c.categorize(name), 'convenience store', name)
        assert c.is_wasteful(name), name


def t_cat_coffee_not_auto_flagged():
    """Every coffee used to be flagged; the evidence on that is mixed."""
    c = fresh_categorizer()
    for name in ("Starbucks Coffee", "Blue Bottle Coffee", "Dunkin"):
        assert not c.is_wasteful(name), name


def t_cat_streaming_and_marketplaces_flagged():
    c = fresh_categorizer()
    for name in ("Netflix", "Spotify", "Hulu", "Amazon", "Temu"):
        assert c.is_wasteful(name), name
    for name in ("Adobe Creative Cloud", "Nike"):
        assert not c.is_wasteful(name), name


def t_cat_essentials_never_flagged():
    c = fresh_categorizer()
    for name in ("Rent Payment", "Whole Foods", "Safeway", "Comcast",
                 "CVS Pharmacy", "Shell"):
        assert not c.is_wasteful(name), name


def t_cat_score_bounded():
    c = fresh_categorizer()
    for name in ("Casino delivery premium upgrade impulse", "Netflix Premium",
                 "Overdraft Fee", "Rent", "", None, "x"):
        score = c.waste_score(name)
        assert 0.0 <= score <= 1.0, (name, score)


def t_cat_priors_are_sane():
    import categorizer
    for category, prior in categorizer.WASTE_PRIORS.items():
        assert 0.0 <= prior <= 1.0, category
    alone = {k for k, v in categorizer.WASTE_PRIORS.items()
             if v >= categorizer.WASTE_THRESHOLD}
    eq(alone, {'fees', 'convenience store'},
       "only fees and convenience stores are waste on category alone; "
       "dining, shopping, entertainment and subscriptions need a cue")


def t_cat_taught_beats_score():
    c = fresh_categorizer()
    c.learn_merchant_category('Uber Eats', 'eating out', False)
    eq(c.is_wasteful('Uber Eats'), False, "taught not-wasteful must win")
    c.learn_merchant_category('Whole Foods', 'groceries', True)
    eq(c.is_wasteful('Whole Foods'), True, "taught wasteful must win")


def t_cat_apostrophes_match():
    """Regression: "McDonald's" fell through to 'other'."""
    c = fresh_categorizer()
    eq(c.categorize("McDonald's"), 'eating out')
    eq(c.categorize("Wendy's"), 'eating out')
    eq(c.categorize("Trader Joe's"), 'groceries')


CATEGORIZER_TESTS = [
    ("cat: keyword matching", t_cat_keywords),
    ("cat: empty / None merchant", t_cat_empty_input),
    ("cat: learned category persists across instances", t_cat_category_persists),
    ("cat: learned WASTEFUL flag persists across instances", t_cat_wasteful_persists),
    ("cat: legacy flat overrides file is migrated, not lost", t_cat_legacy_file_migrates),
    ("cat: partial merchant match", t_cat_partial_match),
    ("cat: learned override beats keyword", t_cat_override_beats_keyword),
    ("cat: explicitly-taught not-wasteful is respected", t_cat_not_wasteful_respected),
    ("cat: partial match honours a not-wasteful lesson", t_cat_partial_honours_not_wasteful),
    ("cat: partial match propagates a wasteful lesson", t_cat_partial_propagates_wasteful),
    ("cat: a charge of exactly $100 still categorises", t_cat_exactly_one_hundred),
    ("cat: amount-based fallback covers all amounts", t_cat_amount_fallback),
    ("cat: corrupt overrides file degrades gracefully", t_cat_corrupt_file),
    ("cat: get_category_suggestions ranks", t_cat_suggestions),
    ("cat: word-boundary scoring", t_cat_word_boundary),
    ("cat: fees are categorised and flagged", t_cat_fees_flagged),
    ("cat: delivery apps flagged, including 'Uber Eats'", t_cat_delivery_apps_flagged),
    ("cat: convenience stores categorised and flagged", t_cat_convenience_flagged),
    ("cat: coffee shops are not auto-flagged", t_cat_coffee_not_auto_flagged),
    ("cat: streaming and marketplaces flagged, work tools and apparel not", t_cat_streaming_and_marketplaces_flagged),
    ("cat: essentials are never flagged", t_cat_essentials_never_flagged),
    ("cat: waste score stays within 0 to 1", t_cat_score_bounded),
    ("cat: only fees and convenience stores are waste on category alone", t_cat_priors_are_sane),
    ("cat: a taught merchant beats the research score", t_cat_taught_beats_score),
    ("cat: apostrophes do not break matching", t_cat_apostrophes_match),
]


# ------------------------------------------------------------------ stats
def t_stats_periods():
    periods = __import__('stats').get_wasted_by_period()
    assert periods['week'] <= periods['month'] <= periods['year'], periods


def t_stats_percentage_range():
    data = __import__('stats').get_wasteful_percentage()
    assert 0 <= data['percentage'] <= 100, data


def t_stats_top_vendor_shape():
    vendor = __import__('stats').get_top_wasteful_vendor()
    assert vendor is None or {'vendor', 'count', 'total'} <= set(vendor), vendor


def t_stats_vacation_affordable():
    vacation = __import__('stats').get_vacation_suggestion(3000)
    assert vacation['cost'] <= 3000, vacation


def t_stats_vacation_broke():
    stats = __import__('stats')
    cheapest = min(v['cost'] for v in stats.VACATION_IDEAS)
    eq(stats.get_vacation_suggestion(1)['cost'], cheapest)


def t_stats_required_keys():
    for stat in __import__('stats').generate_stats_list():
        for key in ('type', 'title', 'value', 'subtitle'):
            assert key in stat, (stat.get('type'), key)


def t_stats_wasted_text_verbatim():
    """wasted_text must appear exactly, or the red highlight never applies."""
    for stat in __import__('stats').generate_stats_list():
        marker = stat.get('wasted_text')
        if not marker:
            continue
        haystack = (f"{stat['value']} {stat.get('value_extra', '')} "
                    f"{stat['subtitle']}")
        assert marker in haystack, (stat['type'], marker, haystack)


def t_stats_percentage_not_reddened():
    """That stat's figure is total spending, so it must not be marked."""
    for stat in __import__('stats').generate_stats_list():
        if stat['type'] == 'wasted_percentage':
            assert 'wasted_text' not in stat


def t_stats_no_data():
    eq(__import__('stats').get_no_data_stat()['type'], 'no_data')


def t_stats_random():
    assert __import__('stats').get_random_stat()['type']


def t_stats_empty_database():
    """Every stat path must survive a database with no rows."""
    stats = __import__('stats')
    original = database.DATABASE_PATH
    database.DATABASE_PATH = sandbox_path('empty.db')
    try:
        database.init_db()
        listed = stats.generate_stats_list()
        eq(len(listed), 1)
        eq(listed[0]['type'], 'no_data')
        stats.get_random_stat()
        eq(stats.get_wasteful_percentage()['percentage'], 0)
        eq(stats.get_top_wasteful_vendor(), None)
    finally:
        database.DATABASE_PATH = original


STATS_TESTS = [
    ("stats: week <= month <= year", t_stats_periods),
    ("stats: percentage within 0-100", t_stats_percentage_range),
    ("stats: top vendor shape", t_stats_top_vendor_shape),
    ("stats: vacation within budget", t_stats_vacation_affordable),
    ("stats: vacation falls back to cheapest", t_stats_vacation_broke),
    ("stats: every stat has the required keys", t_stats_required_keys),
    ("stats: wasted_text appears verbatim in its stat", t_stats_wasted_text_verbatim),
    ("stats: percentage stat carries no wasted_text", t_stats_percentage_not_reddened),
    ("stats: no_data fallback", t_stats_no_data),
    ("stats: get_random_stat", t_stats_random),
    ("stats: empty database yields no_data, no crash", t_stats_empty_database),
]


# ----------------------------------------------------------------- widget
WIDGET = None
QT_APP = None
EVERY_STAT = []


def show(stat):
    WIDGET.display_stat(stat)
    WIDGET.layout().activate()
    QT_APP.processEvents()


def t_widget_renders_every_stat():
    for stat in EVERY_STAT:
        show(stat)
        assert WIDGET.value_label.text(), stat['type']


def t_widget_value_centred():
    for size in [(350, 170), (280, 140), (500, 260)]:
        WIDGET.resize(*size)
        QT_APP.processEvents()
        for stat in EVERY_STAT:
            show(stat)
            box = WIDGET.value_label.geometry()
            offset = (box.y() + box.height() / 2) - WIDGET.height() / 2
            assert abs(offset) <= 3, (size, stat['type'], offset)
    WIDGET.resize(350, 170)
    QT_APP.processEvents()


def t_widget_no_overflow():
    for size in [(350, 170), (280, 140)]:
        WIDGET.resize(*size)
        QT_APP.processEvents()
        for stat in EVERY_STAT:
            show(stat)
            assert WIDGET.value_label.height() <= WIDGET.height(), stat['type']
            assert WIDGET.value_label.sizeHint().width() <= WIDGET.width(), \
                stat['type']
    WIDGET.resize(350, 170)
    QT_APP.processEvents()


def t_widget_escapes_html():
    show({'type': 'vacation_idea', 'title': 'x',
          'value': 'Paris & London <trip>', 'subtitle': '', 'data': {}})
    text = WIDGET.value_label.text()
    assert '&amp;' in text and '&lt;trip&gt;' in text, text


def t_widget_ring_only_on_percentage():
    for stat in EVERY_STAT:
        show(stat)
        if stat['type'] == 'wasted_percentage':
            assert WIDGET.ring_percentage is not None
            assert WIDGET._ring_geometry() is not None
        else:
            assert WIDGET.ring_percentage is None, stat['type']
            assert WIDGET._ring_geometry() is None, stat['type']


def t_widget_ring_extremes():
    for pct in (0, 0.1, 50, 99.9, 100):
        show({'type': 'wasted_percentage',
              'title': 'Percent of Spending Wasted', 'value': f'{pct}%',
              'subtitle': 'x', 'data': {'percentage': pct}})
        assert WIDGET._ring_geometry() is not None, pct
        WIDGET.grab()  # must paint without raising


def t_widget_hides_empty_subtitle():
    show({'type': 'wasted_year', 'title': 'T', 'value': '$1.00',
          'subtitle': '', 'data': {}})
    assert WIDGET.subtitle_label.isHidden()
    show({'type': 'wasted_month', 'title': 'T', 'value': '$1.00',
          'subtitle': 'something', 'data': {}})
    assert not WIDGET.subtitle_label.isHidden()


def t_widget_reddens_wasted_only():
    red = '255, 100, 100'
    show({'type': 'wasted_year', 'title': 'T', 'value': '$9.99',
          'subtitle': '', 'wasted_text': '$9.99', 'data': {}})
    assert red in WIDGET.value_label.text()

    show({'type': 'vacation_idea', 'title': 'T', 'value': 'Bali resort week',
          'subtitle': 'Instead of wasting $9.99 this year',
          'wasted_text': '$9.99', 'data': {}})
    assert red in WIDGET.subtitle_label.text()
    assert red not in WIDGET.value_label.text(), "the name must stay white"


def t_widget_vendor_two_lines():
    show({'type': 'top_wasteful_vendor', 'title': 'Biggest Waste Vendor',
          'value': 'Uber 063015 SF**POOL**', 'value_extra': '$1,268.20',
          'wasted_text': '$1,268.20', 'subtitle': 'wasted over 12 purchases',
          'data': {}})
    eq(len(WIDGET._value_lines), 2)
    assert '<br>' in WIDGET.value_label.text()


def t_widget_long_value_shrinks():
    show({'type': 'no_data', 'title': 'T',
          'value': 'Mark transactions as wasteful', 'subtitle': 'x',
          'data': {}})
    prose = WIDGET.value_label.font().pixelSize()
    show({'type': 'wasted_year', 'title': 'T', 'value': '$1.00',
          'subtitle': '', 'data': {}})
    figure = WIDGET.value_label.font().pixelSize()
    assert prose < figure, (prose, figure)


def t_widget_resize_storm():
    for height in range(140, 262, 20):
        WIDGET.resize(int(height * 2.05), height)
        QT_APP.processEvents()
        for stat in EVERY_STAT:
            show(stat)
    WIDGET.resize(350, 170)
    QT_APP.processEvents()


def t_widget_minimum_size():
    eq(WIDGET.minimumSize().width(), 280)
    eq(WIDGET.minimumSize().height(), 140)


def t_widget_corner_hit_testing():
    from PyQt5.QtCore import QPoint
    WIDGET.resize(350, 170)
    QT_APP.processEvents()
    eq(WIDGET.get_corner_at_pos(QPoint(2, 2)), 'top-left')
    eq(WIDGET.get_corner_at_pos(QPoint(348, 2)), 'top-right')
    eq(WIDGET.get_corner_at_pos(QPoint(2, 168)), 'bottom-left')
    eq(WIDGET.get_corner_at_pos(QPoint(348, 168)), 'bottom-right')
    eq(WIDGET.get_corner_at_pos(QPoint(175, 85)), None)


def t_widget_footer():
    WIDGET.update_footer()
    assert 'Last updated' in WIDGET.footer_label.text()



def fresh_settings():
    from PyQt5.QtCore import QSettings
    return QSettings(sandbox_path(f'pos_{os.urandom(4).hex()}.ini'),
                     QSettings.IniFormat)


def with_settings(fn):
    """Run fn against a clean settings store, restoring the widget's own."""
    original = WIDGET.settings
    WIDGET.settings = fresh_settings()
    try:
        fn(WIDGET.settings)
    finally:
        WIDGET.settings = original


def t_pos_default_when_nothing_saved():
    def body(_):
        eq(WIDGET.initial_position(), WIDGET.default_position())
    with_settings(body)


def t_pos_restores_saved():
    from PyQt5.QtWidgets import QApplication
    def body(settings):
        screen = QApplication.primaryScreen().availableGeometry()
        settings.setValue("window/x", screen.x() + 137)
        settings.setValue("window/y", screen.y() + 211)
        pos = WIDGET.initial_position()
        eq((pos.x(), pos.y()), (screen.x() + 137, screen.y() + 211))
    with_settings(body)


def t_pos_offscreen_falls_back():
    """A saved spot from a display layout that no longer exists must not
    strand the widget where it can't be seen."""
    def body(settings):
        settings.setValue("window/x", 90000)
        settings.setValue("window/y", 90000)
        eq(WIDGET.initial_position(), WIDGET.default_position())
    with_settings(body)


def t_pos_hide_saves():
    def body(settings):
        WIDGET.move(300, 200)
        QT_APP.processEvents()
        expected = (WIDGET.x(), WIDGET.y())
        WIDGET.hide()
        QT_APP.processEvents()
        eq((settings.value("window/x", type=int),
            settings.value("window/y", type=int)), expected)
        WIDGET.show()
        QT_APP.processEvents()
    with_settings(body)


def t_pos_hide_show_keeps_place():
    WIDGET.move(310, 220)
    QT_APP.processEvents()
    before = (WIDGET.x(), WIDGET.y())
    WIDGET.hide()
    WIDGET.show()
    QT_APP.processEvents()
    eq((WIDGET.x(), WIDGET.y()), before)


def t_pos_drag_release_saves():
    from PyQt5.QtCore import QPointF, QEvent, Qt as QtC
    from PyQt5.QtGui import QMouseEvent
    def body(settings):
        WIDGET.move(400, 260)
        QT_APP.processEvents()
        WIDGET.is_dragging = True
        WIDGET.drag_position = WIDGET.pos()
        release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(50, 50),
                              QtC.LeftButton, QtC.NoButton, QtC.NoModifier)
        WIDGET.mouseReleaseEvent(release)
        eq((settings.value("window/x", type=int),
            settings.value("window/y", type=int)), (400, 260))
    with_settings(body)


def t_pos_click_without_drag_does_not_save():
    from PyQt5.QtCore import QPointF, QEvent, Qt as QtC
    from PyQt5.QtGui import QMouseEvent
    def body(settings):
        WIDGET.is_dragging = False
        WIDGET.resize_corner = None
        release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(50, 50),
                              QtC.LeftButton, QtC.NoButton, QtC.NoModifier)
        WIDGET.mouseReleaseEvent(release)  # a click advances the stat
        assert not settings.contains("window/x"), "a plain click saved a position"
    with_settings(body)


def t_pos_round_trip_across_instances():
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import QApplication
    path = sandbox_path('roundtrip.ini')
    screen = QApplication.primaryScreen().availableGeometry()
    first = QSettings(path, QSettings.IniFormat)
    first.setValue("window/x", screen.x() + 50)
    first.setValue("window/y", screen.y() + 60)
    first.sync()
    original = WIDGET.settings
    WIDGET.settings = QSettings(path, QSettings.IniFormat)
    try:
        pos = WIDGET.initial_position()
        eq((pos.x(), pos.y()), (screen.x() + 50, screen.y() + 60))
    finally:
        WIDGET.settings = original



def t_tray_icon_is_template():
    """A fixed-colour icon vanishes on a dark menu bar; a template image is
    tinted by macOS to suit whichever bar it is on."""
    icon = WIDGET.tray_icon.icon()
    assert not icon.isNull(), "tray icon missing"
    assert icon.isMask(), "tray icon must be a template (mask) image"
    img = icon.pixmap(44, 44).toImage()
    opaque = sum(1 for x in range(img.width()) for y in range(img.height())
                 if img.pixelColor(x, y).alpha() > 128)
    assert opaque > 40, f"icon looks empty ({opaque} opaque pixels)"



def t_widget_title_is_larger_and_fits():
    """Title is 50% above the original 13px, and never clips."""
    for size in [(350, 170), (280, 140), (500, 260)]:
        WIDGET.resize(*size)
        QT_APP.processEvents()
        for stat in EVERY_STAT:
            show(stat)
            fm = WIDGET.title_label.fontMetrics()
            need = fm.boundingRect(WIDGET.title_label.text()).width()
            assert need <= WIDGET.title_label.width(), (size, stat['type'], need)
    WIDGET.resize(350, 170)
    QT_APP.processEvents()
    show({'type': 'wasted_year', 'title': 'Total Wasted', 'value': '$1',
          'subtitle': '', 'data': {}})
    px = WIDGET.title_label.font().pixelSize()
    assert 19 <= px <= 20, f"short title should be ~20px (1.5 x 13), got {px}"



# ------------------------------------------- startup prompt (new transactions)
def with_temp_db(fn):
    """Run fn against a fresh database and a throwaway categorizer, so the
    dialog can neither touch the real db nor write merchant_overrides.json."""
    import categorizer
    original_path = database.DATABASE_PATH
    original_cat = categorizer._categorizer
    database.DATABASE_PATH = sandbox_path(f'rev_{os.urandom(4).hex()}.db')
    categorizer._categorizer = categorizer.TransactionCategorizer(
        overrides_file=sandbox_path(f'rev_ov_{os.urandom(4).hex()}.json'))
    try:
        database.init_db()
        database.save_accounts([fake_account('acct1', 'Checking', 1.0)])
        fn()
    finally:
        database.DATABASE_PATH = original_path
        categorizer._categorizer = original_cat


def seed_reviewable(count):
    database.save_transactions([
        fake_tx(f'r{i}', f'Merchant {i}', 10.0 + i, i) for i in range(count)])


def prompted_map():
    with database.get_db() as conn:
        return {r['id']: r['prompted'] for r in
                conn.execute("SELECT id, prompted FROM transactions")}


def quiet_completion(fn):
    """Run fn with the end-of-batch message box stubbed so it can't block."""
    from PyQt5.QtWidgets import QMessageBox
    real = QMessageBox.information
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    try:
        return fn()
    finally:
        QMessageBox.information = real


def reviewed_map():
    with database.get_db() as conn:
        return {r['id']: r['reviewed'] for r in
                conn.execute("SELECT id, reviewed FROM transactions")}


def t_rev_migrates_existing_database():
    """A database from before the reviewed column must gain it, keeping rows."""
    import sqlite3
    path = sandbox_path('legacy_schema.db')
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE transactions (id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL, date DATE NOT NULL, name TEXT NOT NULL,
        amount REAL NOT NULL, category TEXT, category_primary TEXT,
        is_wasteful BOOLEAN DEFAULT 0, created_at TIMESTAMP)""")
    conn.execute("INSERT INTO transactions (id, account_id, date, name, amount,"
                 " category, is_wasteful) VALUES ('old1','a','2026-01-01',"
                 "'Starbucks',4.5,'eating out',1)")
    conn.commit(); conn.close()

    original = database.DATABASE_PATH
    database.DATABASE_PATH = path
    try:
        database.init_db()
        database.init_db()  # running it again must be harmless
        with database.get_db() as c:
            row = c.execute("SELECT * FROM transactions WHERE id='old1'").fetchone()
        eq(row['reviewed'], 0)
        eq(row['category'], 'eating out', "existing category must survive")
        eq(row['is_wasteful'], 1, "existing wasteful flag must survive")
    finally:
        database.DATABASE_PATH = original


def t_rev_resync_keeps_user_choices():
    """Regression: INSERT OR REPLACE wiped category, wasteful and reviewed."""
    def body():
        seed_reviewable(1)
        with database.get_db() as c:
            c.execute("UPDATE transactions SET category='groceries',"
                      " is_wasteful=1, reviewed=1 WHERE id='r0'")
            c.commit()
        # the same transaction arrives again from Plaid with a corrected amount
        database.save_transactions([fake_tx('r0', 'Merchant 0', 99.0, 0)])
        with database.get_db() as c:
            row = c.execute("SELECT * FROM transactions WHERE id='r0'").fetchone()
        eq(row['amount'], 99.0, "fresh data should still update")
        eq(row['category'], 'groceries')
        eq(row['is_wasteful'], 1)
        eq(row['reviewed'], 1)
    with_temp_db(body)


def t_rev_dialog_shows_only_unreviewed_newest_first():
    from categorization_dialog import CategorizationDialog
    def body():
        seed_reviewable(6)
        with database.get_db() as c:
            c.execute("UPDATE transactions SET prompted=1 WHERE id IN ('r0','r1')")
            c.commit()
        dialog = CategorizationDialog(limit=100)
        ids = [t[0] for t in dialog.transactions]
        eq(ids, ['r2', 'r3', 'r4', 'r5'], "already-asked rows excluded, newest first")
    with_temp_db(body)


def t_rev_dialog_respects_limit():
    from categorization_dialog import CategorizationDialog
    def body():
        seed_reviewable(15)
        eq(len(CategorizationDialog(limit=10).transactions), 10)
    with_temp_db(body)


def t_rev_categorizing_marks_reviewed_skip_does_not():
    from categorization_dialog import CategorizationDialog
    from PyQt5.QtWidgets import QMessageBox
    def body():
        seed_reviewable(3)
        real_info = QMessageBox.information
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        try:
            dialog = CategorizationDialog(limit=10)
            first = dialog.transactions[0][0]
            second = dialog.transactions[1][0]
            dialog.category_combo.setCurrentText('groceries')
            dialog.wasteful_checkbox.setChecked(True)
            dialog.categorize_transaction()   # first: categorised
            dialog.skip_transaction()         # second: skipped
        finally:
            QMessageBox.information = real_info
        flags = reviewed_map()
        eq(flags[first], 1, "a categorised transaction is marked reviewed")
        eq(flags[second], 0, "a skipped one is not marked reviewed")
        asked = prompted_map()
        eq((asked[first], asked[second]), (1, 1),
           "both were shown, so neither is asked about again")
        with database.get_db() as c:
            row = c.execute("SELECT category, is_wasteful FROM transactions"
                            " WHERE id=?", (first,)).fetchone()
        eq((row['category'], row['is_wasteful']), ('groceries', 1))
    with_temp_db(body)


def t_rev_prompt_silent_when_nothing_new():
    from categorization_dialog import CategorizationDialog
    def body():
        seed_reviewable(2)
        with database.get_db() as c:
            c.execute("UPDATE transactions SET reviewed=1, prompted=1")
            c.commit()
        opened = []
        real = CategorizationDialog.exec_
        CategorizationDialog.exec_ = lambda self: opened.append(len(self.transactions))
        try:
            WIDGET.prompt_for_new_transactions()
        finally:
            CategorizationDialog.exec_ = real
        eq(opened, [], "no popup when there is nothing new")
    with_temp_db(body)


def t_rev_prompt_opens_with_a_batch():
    from categorization_dialog import CategorizationDialog
    def body():
        seed_reviewable(25)
        opened = []
        real = CategorizationDialog.exec_
        CategorizationDialog.exec_ = lambda self: opened.append(len(self.transactions))
        try:
            WIDGET.prompt_for_new_transactions()
        finally:
            CategorizationDialog.exec_ = real
        eq(len(opened), 1, "exactly one popup")
        assert 0 < opened[0] <= 10, f"batch should be small, got {opened[0]}"
    with_temp_db(body)


def t_rev_each_transaction_is_asked_only_once():
    from categorization_dialog import CategorizationDialog
    def body():
        seed_reviewable(4)
        first = CategorizationDialog(limit=10)
        shown = [first.transactions[0][0]]
        quiet_completion(first.skip_transaction)   # now showing the second
        shown.append(first.transactions[1][0])
        again = CategorizationDialog(limit=10)
        left = [t[0] for t in again.transactions]
        for tid in shown:
            assert tid not in left, f"{tid} was shown already and came back"
        eq(len(left), 2)
    with_temp_db(body)


def t_rev_taught_merchants_are_applied_not_asked():
    import categorizer
    from categorization_dialog import CategorizationDialog
    def body():
        seed_reviewable(3)
        categorizer.get_categorizer().learn_merchant_category(
            'Merchant 1', 'groceries', True)
        dialog = CategorizationDialog(limit=10)
        ids = [t[0] for t in dialog.transactions]
        assert 'r1' not in ids, "a taught merchant must not be asked about"
        with database.get_db() as c:
            row = c.execute("SELECT category, is_wasteful, reviewed, prompted"
                            " FROM transactions WHERE id='r1'").fetchone()
        eq((row['category'], row['is_wasteful'], row['reviewed'], row['prompted']),
           ('groceries', 1, 1, 1))
        with database.get_db() as c:
            other = c.execute("SELECT reviewed FROM transactions WHERE id='r0'").fetchone()
        eq(other['reviewed'], 0, "an untaught merchant must be left alone")
    with_temp_db(body)


def t_rev_prompted_column_migrates_from_reviewed_only_schema():
    import sqlite3
    path = sandbox_path('reviewed_only.db')
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE transactions (id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL, date DATE NOT NULL, name TEXT NOT NULL,
        amount REAL NOT NULL, category TEXT, category_primary TEXT,
        is_wasteful BOOLEAN DEFAULT 0, reviewed BOOLEAN DEFAULT 0,
        created_at TIMESTAMP)""")
    conn.execute("INSERT INTO transactions (id, account_id, date, name, amount, reviewed)"
                 " VALUES ('done','a','2026-01-01','Seen',1,1)")
    conn.execute("INSERT INTO transactions (id, account_id, date, name, amount, reviewed)"
                 " VALUES ('todo','a','2026-01-02','Unseen',2,0)")
    conn.commit(); conn.close()
    original = database.DATABASE_PATH
    database.DATABASE_PATH = path
    try:
        database.init_db()
        database.init_db()   # harmless the second time
        with database.get_db() as c:
            got = {r['id']: r['prompted'] for r in
                   c.execute("SELECT id, prompted FROM transactions")}
        eq(got, {'done': 1, 'todo': 0}, "already-reviewed rows count as asked")
    finally:
        database.DATABASE_PATH = original


def t_rev_tray_menu_still_answers_when_nothing_is_new():
    from categorization_dialog import CategorizationDialog
    def body():
        seed_reviewable(2)
        with database.get_db() as c:
            c.execute("UPDATE transactions SET reviewed=1, prompted=1")
            c.commit()
        opened = []
        real = CategorizationDialog.exec_
        CategorizationDialog.exec_ = lambda self: opened.append(len(self.transactions))
        try:
            WIDGET.open_categorization_dialog()   # what the tray menu calls
        finally:
            CategorizationDialog.exec_ = real
        eq(opened, [0], "the tray menu should show its 'all done' state, not nothing")
    with_temp_db(body)


WIDGET_TESTS = [
    ("widget: every stat renders", t_widget_renders_every_stat),
    ("widget: value stays centred at all sizes", t_widget_value_centred),
    ("widget: value never overflows the widget", t_widget_no_overflow),
    ("widget: HTML in a value is escaped", t_widget_escapes_html),
    ("widget: ring appears only on the percentage stat", t_widget_ring_only_on_percentage),
    ("widget: ring paints at 0 and 100 percent", t_widget_ring_extremes),
    ("widget: empty subtitle is hidden", t_widget_hides_empty_subtitle),
    ("widget: wasted figures are reddened, names are not", t_widget_reddens_wasted_only),
    ("widget: vendor value runs to two lines", t_widget_vendor_two_lines),
    ("widget: long values shrink below hero size", t_widget_long_value_shrinks),
    ("widget: survives repeated resizes", t_widget_resize_storm),
    ("widget: minimum size enforced", t_widget_minimum_size),
    ("widget: corner hit-testing", t_widget_corner_hit_testing),
    ("widget: footer timestamp", t_widget_footer),
    ("widget: tray icon is a template image", t_tray_icon_is_template),
    ("widget: title is 50% larger and never clips", t_widget_title_is_larger_and_fits),
    ("position: default when nothing is saved", t_pos_default_when_nothing_saved),
    ("position: a saved position is restored", t_pos_restores_saved),
    ("position: an off-screen saved position falls back to default", t_pos_offscreen_falls_back),
    ("position: hiding saves the position", t_pos_hide_saves),
    ("position: hide then show keeps the place", t_pos_hide_show_keeps_place),
    ("position: releasing a drag saves the position", t_pos_drag_release_saves),
    ("position: a plain click does not save", t_pos_click_without_drag_does_not_save),
    ("position: survives a new settings instance", t_pos_round_trip_across_instances),
    ("review: an old database gains the reviewed column, keeping rows", t_rev_migrates_existing_database),
    ("review: re-syncing a transaction keeps the user's choices", t_rev_resync_keeps_user_choices),
    ("review: dialog lists only not-yet-asked, newest first", t_rev_dialog_shows_only_unreviewed_newest_first),
    ("review: dialog respects its limit", t_rev_dialog_respects_limit),
    ("review: categorising marks reviewed; skipping still counts as asked", t_rev_categorizing_marks_reviewed_skip_does_not),
    ("review: no popup when nothing is new", t_rev_prompt_silent_when_nothing_new),
    ("review: startup popup opens once with a small batch", t_rev_prompt_opens_with_a_batch),
    ("review: the tray menu still answers when nothing is new", t_rev_tray_menu_still_answers_when_nothing_is_new),
    ("review: each transaction is asked about only once", t_rev_each_transaction_is_asked_only_once),
    ("review: taught merchants are applied, not asked", t_rev_taught_merchants_are_applied_not_asked),
    ("review: prompted column migrates from a reviewed-only database", t_rev_prompted_column_migrates_from_reviewed_only_schema),
]


# ---------------------------------------------------------- plaid / flask
def t_plaid_imports():
    import plaid_client
    assert hasattr(plaid_client, 'PlaidClient')


def t_plaid_credentials_from_env():
    import inspect
    import plaid_client
    source = inspect.getsource(plaid_client.PlaidClient.__init__)
    assert 'environ' in source or 'getenv' in source, \
        "credentials should come from the environment, not the source"


def t_flask_routes():
    import link_app
    rules = {rule.rule for rule in link_app.app.url_map.iter_rules()}
    assert '/' in rules, rules


INTEGRATION_TESTS = [
    ("plaid: module imports", t_plaid_imports),
    ("plaid: credentials read from environment", t_plaid_credentials_from_env),
    ("flask: link app exposes routes", t_flask_routes),
]


# ------------------------------------------------------------------- main
def main():
    global SANDBOX, WIDGET, QT_APP, EVERY_STAT

    SANDBOX = tempfile.mkdtemp(prefix='money_guilt_tests_')
    database.DATABASE_PATH = os.path.join(SANDBOX, 'test.db')

    try:
        for name, fn in DATABASE_TESTS:
            check(name, fn)
        for name, fn in CATEGORIZER_TESTS:
            check(name, fn)
        for name, fn in STATS_TESTS:
            check(name, fn)

        from PyQt5.QtWidgets import QApplication
        import widget
        import stats

        QT_APP = QApplication.instance() or QApplication([])
        from PyQt5.QtCore import QSettings
        WIDGET = widget.MoneyGuiltWidget(settings=QSettings(
            sandbox_path('widget_settings.ini'), QSettings.IniFormat))
        WIDGET.resize(350, 170)
        WIDGET.show()
        QT_APP.processEvents()
        EVERY_STAT = stats.generate_stats_list() + [stats.get_no_data_stat()]

        for name, fn in WIDGET_TESTS:
            check(name, fn)
        for name, fn in INTEGRATION_TESTS:
            check(name, fn)
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)

    print()
    print('=' * 72)
    print(f"PASSED {len(PASSED)}   FAILED {len(FAILED)}")
    print('=' * 72)
    for name in PASSED:
        print(f"  pass  {name}")
    for name, error, tb in FAILED:
        print(f"  FAIL  {name}")
        print(f"        {error}")
    if FAILED:
        print()
        for name, error, tb in FAILED:
            print(f"--- {name} ---")
            print(tb)
    print()
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
