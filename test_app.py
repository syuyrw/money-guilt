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


# ----------------------------------------------------------------- privacy
FAKE_IDLE = [0]


def with_privacy(fn, **state):
    """Run fn against a fresh PrivacyState whose idle time the test controls."""
    import privacy
    original = WIDGET.privacy
    FAKE_IDLE[0] = 0
    WIDGET.privacy = privacy.PrivacyState(idle_fn=lambda: FAKE_IDLE[0], **state)
    try:
        fn()
    finally:
        WIDGET.privacy = original
        WIDGET.refresh_display()
        QT_APP.processEvents()


def click():
    """A plain left click, as the widget receives it."""
    from PyQt5.QtCore import QPointF, QEvent, Qt as QtC
    from PyQt5.QtGui import QMouseEvent
    WIDGET.is_dragging = False
    WIDGET.resize_corner = None
    WIDGET.mouseReleaseEvent(QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(60, 60),
        QtC.LeftButton, QtC.NoButton, QtC.NoModifier))
    QT_APP.processEvents()


PERCENT_STAT = {'type': 'wasted_percentage', 'title': 'Percent of Spending Wasted',
                'value': '98.5%', 'subtitle': 'Out of $352.38 total spending',
                'wasted_text': None, 'data': {'percentage': 98.5}}


def t_priv_masks_value_subtitle_and_ring():
    import privacy
    def body():
        show(PERCENT_STAT)
        assert '98.5' in WIDGET.value_label.text()
        eq(WIDGET.ring_percentage, 98.5)
        WIDGET.privacy.manual = True
        WIDGET.refresh_display()
        eq(WIDGET.value_label.text(), privacy.MASK)
        eq(WIDGET.ring_percentage, 0, "the arc would give the percentage away")
        assert not any(c.isdigit() for c in WIDGET.subtitle_label.text()), \
            WIDGET.subtitle_label.text()
        eq(WIDGET.title_label.text(), 'Percent of Spending Wasted')
        WIDGET.privacy.manual = False
        WIDGET.refresh_display()
        assert '98.5' in WIDGET.value_label.text(), "unmasking restores the figure"
    with_privacy(body)


def t_priv_no_digit_survives_on_any_stat():
    def body():
        for stat in EVERY_STAT:
            show(stat)
            for label in (WIDGET.value_label, WIDGET.subtitle_label):
                assert not any(c.isdigit() for c in label.text()), \
                    (stat['type'], label.text())
    with_privacy(body, manual=True)


def t_priv_masked_amounts_are_not_red():
    def body():
        show({'type': 'wasted_year', 'title': 'T', 'value': '$9.99',
              'subtitle': '', 'wasted_text': '$9.99', 'data': {}})
        assert '255, 100, 100' not in WIDGET.value_label.text()
    with_privacy(body, manual=True)


def t_priv_two_line_vendor_collapses_to_one_mask():
    def body():
        show({'type': 'top_wasteful_vendor', 'title': 'Biggest Waste Vendor',
              'value': 'SparkFun', 'value_extra': '$268.20',
              'wasted_text': '$268.20', 'subtitle': 'wasted over 3 purchases',
              'data': {}})
        eq(len(WIDGET._value_lines), 1)
        assert 'SparkFun' not in WIDGET.value_label.text(), "the name is revealing too"
    with_privacy(body, manual=True)


def t_priv_stays_centred_while_masked():
    def body():
        for size in [(350, 170), (280, 140)]:
            WIDGET.resize(*size)
            QT_APP.processEvents()
            for stat in EVERY_STAT:
                show(stat)
                box = WIDGET.value_label.geometry()
                offset = (box.y() + box.height() / 2) - WIDGET.height() / 2
                assert abs(offset) <= 3, (size, stat['type'], offset)
        WIDGET.resize(350, 170)
        QT_APP.processEvents()
    with_privacy(body, manual=True)


def t_priv_idle_lock_activity_does_not_reveal():
    import privacy
    def body():
        show(PERCENT_STAT)
        FAKE_IDLE[0] = 400
        WIDGET._check_idle()
        eq(WIDGET.value_label.text(), privacy.MASK)
        FAKE_IDLE[0] = 0            # someone bumps the mouse
        WIDGET._check_idle()
        eq(WIDGET.value_label.text(), privacy.MASK, "activity alone must not reveal")
    with_privacy(body, auto_hide=True, idle_limit=300)


def t_priv_click_reveals_without_advancing():
    def body():
        show(PERCENT_STAT)
        FAKE_IDLE[0] = 400
        WIDGET._check_idle()
        click()
        assert not WIDGET.privacy.masked, "the click should reveal"
        assert WIDGET.current_stat is PERCENT_STAT, "the reveal click must not advance"
        assert '98.5' in WIDGET.value_label.text()
        click()                      # now unlocked, a click advances as before
        assert WIDGET.current_stat is not PERCENT_STAT
    with_privacy(body, auto_hide=True, idle_limit=300)


def t_priv_click_does_not_undo_manual_hiding():
    import privacy
    def body():
        show(PERCENT_STAT)
        FAKE_IDLE[0] = 400
        WIDGET._check_idle()          # locked as well as manually hidden
        click()
        eq(WIDGET.value_label.text(), privacy.MASK, "manual hiding must survive the click")
        assert WIDGET.privacy.manual
    with_privacy(body, manual=True, auto_hide=True, idle_limit=300)


def t_priv_settings_persist():
    original = (WIDGET.privacy.manual, WIDGET.privacy.auto_hide, WIDGET.hide_from_capture)
    try:
        WIDGET.set_manual_privacy(True)
        WIDGET.set_auto_hide(False)
        WIDGET.set_hide_from_capture(False)
        st = WIDGET.settings
        eq(st.value("privacy/manual", type=bool), True)
        eq(st.value("privacy/auto_hide", type=bool), False)
        eq(st.value("privacy/hide_from_capture", type=bool), False)
        WIDGET.set_manual_privacy(False)
        eq(st.value("privacy/manual", type=bool), False)
    finally:
        WIDGET.set_manual_privacy(original[0])
        WIDGET.set_auto_hide(original[1])
        WIDGET.set_hide_from_capture(original[2])


def t_priv_a_new_widget_reads_the_saved_settings():
    from PyQt5.QtCore import QSettings
    import widget
    st = QSettings(sandbox_path('priv_boot.ini'), QSettings.IniFormat)
    st.setValue("privacy/manual", True)
    st.setValue("privacy/auto_hide", False)
    st.setValue("privacy/idle_minutes", 2)
    st.setValue("privacy/hide_from_capture", False)
    other = widget.MoneyGuiltWidget(settings=st)
    try:
        eq(other.privacy.manual, True)
        eq(other.privacy.auto_hide, False)
        eq(other.privacy.idle_limit, 120)
        eq(other.hide_from_capture, False)
    finally:
        other.tray_icon.hide()


def t_priv_defaults_keep_the_number_visible():
    from PyQt5.QtCore import QSettings
    import widget
    other = widget.MoneyGuiltWidget(settings=QSettings(
        sandbox_path('priv_defaults.ini'), QSettings.IniFormat))
    try:
        eq(other.privacy.manual, False)
        eq(other.privacy.auto_hide, False,
           "the widget must not hide its numbers by itself; seeing them is the point")
        eq(other.privacy.idle_limit, 300, "five minutes")
        eq(other.hide_from_capture, True, "capture exclusion is on by default")
    finally:
        other.tray_icon.hide()


def t_priv_capture_call_is_skipped_off_cocoa():
    """The native call needs a real NSView; elsewhere it could crash."""
    from unittest import mock
    import privacy
    with mock.patch.object(privacy, 'set_capture_excluded') as native:
        eq(WIDGET.apply_capture_exclusion(), False)
        native.assert_not_called()


def t_priv_capture_call_is_made_on_cocoa():
    from unittest import mock
    import privacy
    from PyQt5.QtWidgets import QApplication
    original = WIDGET.hide_from_capture
    try:
        with mock.patch.object(QApplication, 'platformName', return_value='cocoa'), \
             mock.patch.object(privacy, 'set_capture_excluded', return_value=True) as native:
            WIDGET.hide_from_capture = True
            eq(WIDGET.apply_capture_exclusion(), True)
            native.assert_called_with(int(WIDGET.winId()), True)
            WIDGET.set_hide_from_capture(False)
            native.assert_called_with(int(WIDGET.winId()), False)
    finally:
        WIDGET.set_hide_from_capture(original)


def tray_texts():
    return [a.text() for a in WIDGET.tray_icon.contextMenu().actions() if a.text()]


def t_tray_menu_does_not_duplicate_the_settings_page():
    """Anything on the Settings page lives only there."""
    texts = tray_texts()
    for gone in ("Hide Amounts", "Auto-Hide When Idle",
                 "Hide From Screenshots && Sharing", "Share Anonymous Wasted Total"):
        assert gone not in texts, f"{gone!r} is on the Settings page, not the menu"
    for kept in ("Hide Widget", "Next Stat", "Settings\u2026",
                 "Categorize Transactions", "Quit Money Guilt"):
        assert kept in texts, f"{kept!r} should still be in the menu: {texts}"
    for stale in ("hide_amounts_action", "auto_hide_action", "capture_action",
                  "share_total_action", "sync_tray_actions"):
        assert not hasattr(WIDGET, stale), f"leftover {stale}"


from PyQt5.QtGui import QDesktopServices as WIDGET_MODULE_DESKTOP_SERVICES


def t_feedback_menu_item_opens_a_mail_draft():
    import feedback
    original_form = feedback.form_available
    feedback.form_available = lambda: False
    from urllib.parse import urlparse, parse_qs
    opened = []
    original = WIDGET_MODULE_DESKTOP_SERVICES.openUrl
    WIDGET_MODULE_DESKTOP_SERVICES.openUrl = staticmethod(lambda url: opened.append(url.toString()) or True)
    try:
        assert 'Feedback' in WIDGET.feedback_action.text()
        WIDGET.feedback_action.trigger()
    finally:
        WIDGET_MODULE_DESKTOP_SERVICES.openUrl = original
        feedback.form_available = original_form
    eq(len(opened), 1)
    parsed = urlparse(opened[0])
    eq(parsed.scheme, 'mailto')
    eq(parsed.path, feedback.FEEDBACK_EMAIL)
    eq(parse_qs(parsed.query)['subject'], [feedback.SUBJECT])
    assert not any(c.isdigit() for c in parse_qs(parsed.query)['body'][0]), \
        "the draft must not carry spending figures"


def t_feedback_form_validates_then_sends():
    import time
    from feedback_dialog import FeedbackDialog
    sent = []
    dialog = FeedbackDialog(send=lambda m, r: sent.append((m, r)) or (True, None))
    dialog.submit()
    assert 'message' in dialog.status.text().lower(), dialog.status.text()
    eq(sent, [])
    dialog.message.setPlainText("Great widget")
    dialog.reply_to.setText("bad")
    dialog.submit()
    assert 'email' in dialog.status.text().lower(), dialog.status.text()
    eq(sent, [])
    dialog.reply_to.setText("me@example.com")
    dialog.submit()
    for _ in range(50):
        QT_APP.processEvents()
        if dialog.result() == FeedbackDialog.Accepted:
            break
        time.sleep(0.02)
    eq(sent, [("Great widget", "me@example.com")])
    eq(dialog.result(), FeedbackDialog.Accepted)


def t_feedback_form_shows_errors_and_allows_retry():
    import time
    from feedback_dialog import FeedbackDialog
    dialog = FeedbackDialog(send=lambda m, r: (False, "Couldn't send it."))
    dialog.message.setPlainText("hi")
    dialog.submit()
    for _ in range(50):
        QT_APP.processEvents()
        time.sleep(0.02)
    eq(dialog.status.text(), "Couldn't send it.")
    assert dialog.send_button.isEnabled(), "the user must be able to try again"


def t_priv_idle_timer_runs_twice_a_minute():
    eq(WIDGET.privacy_timer.interval(), 15000)
    assert WIDGET.privacy_timer.isActive()


# ------------------------------------------------------------ settings window
def with_telemetry(fn, url="https://collector.example", post_delete=None):
    """Run fn with telemetry pointed at a temp file and a fake collector, so
    nothing touches the real config or the network."""
    import telemetry
    from unittest import mock
    config = sandbox_path(f'tel_{os.urandom(4).hex()}.json')
    env = {'MONEY_GUILT_COLLECTOR_URL': url} if url else {}
    with mock.patch.object(telemetry, '_config_path', lambda: config), \
         mock.patch.object(telemetry.paths, 'load_env', lambda: None), \
         mock.patch.dict(os.environ, env), \
         mock.patch.object(telemetry, '_post_delete', post_delete or (lambda u, i: True)):
        if not url:
            os.environ.pop('MONEY_GUILT_COLLECTOR_URL', None)
        fn()


class FakeStore:
    """Stands in for the Keychain module for the whole run, so no test can read
    or delete the real Money Guilt token."""
    class SecureStoreError(Exception):
        pass

    def __init__(self):
        self.reset()

    def reset(self, token=None, unreadable=False):
        self.token, self.unreadable = token, unreadable

    def get_access_token(self):
        if self.unreadable:
            raise self.SecureStoreError("unreadable")
        return self.token

    def delete_access_token(self):
        self.token = None


class FakePlaid:
    """Stands in for Plaid: records calls, and can be told to fail."""
    def __init__(self):
        self.reset()

    def reset(self, error=None, gate=None):
        self.error, self.gate, self.removed = error, gate, []

    def remove_item(self, token):
        self.removed.append(token)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return True


BANK_STORE = FakeStore()
BANK_PLAID = FakePlaid()


def fresh_bank(token=None, error=None, gate=None, unreadable=False):
    BANK_STORE.reset(token, unreadable)
    BANK_PLAID.reset(error, gate)


def make_dialog():
    import settings_dialog
    return settings_dialog.SettingsDialog(WIDGET)


def wait_for_deletion(dialog, seconds=5):
    from PyQt5.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    got = []
    dialog.deletion_finished.connect(lambda outcome: (got.append(outcome), loop.quit()))
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    return loop, got


def pretend_shared():
    """Make this install look like it has reported a total before."""
    import telemetry
    config = telemetry.load_config()
    config["install_id"] = "123e4567-e89b-12d3-a456-426614174000"
    config["last_sent"] = [12.5, 3]
    telemetry.save_config(config)


def restore_widget_defaults():
    WIDGET.set_idle_minutes(5)
    WIDGET.set_rotation_minutes(60)
    WIDGET.set_ask_categorize(True)
    WIDGET.set_manual_privacy(False)
    WIDGET.set_auto_hide(True)
    WIDGET.set_hide_from_capture(True)


def t_set_widget_idle_minutes_applies_persists_and_clamps():
    try:
        WIDGET.set_idle_minutes(10)
        eq(WIDGET.privacy.idle_limit, 600)
        eq(WIDGET.settings.value("privacy/idle_minutes", type=int), 10)
        WIDGET.set_idle_minutes(0)
        eq(WIDGET.privacy.idle_limit, 60, "never below a minute")
        WIDGET.set_idle_minutes(9999)
        eq(WIDGET.privacy.idle_limit, 7200, "never above two hours")
    finally:
        restore_widget_defaults()


def t_set_widget_rotation_drives_the_timer():
    try:
        eq(WIDGET.stat_timer.interval(), 3600000, "hourly by default")
        WIDGET.set_rotation_minutes(15)
        eq(WIDGET.stat_timer.interval(), 900000)
        assert WIDGET.stat_timer.isActive()
        WIDGET.advance_stat()
        eq(WIDGET.stat_timer.interval(), 900000, "advancing must keep the chosen interval")
        WIDGET.set_rotation_minutes(0)
        assert not WIDGET.stat_timer.isActive(), "0 means only when asked"
        WIDGET.advance_stat()
        assert not WIDGET.stat_timer.isActive(), "advancing must not switch it back on"
        eq(WIDGET.settings.value("display/rotation_minutes", type=int), 0)
        WIDGET.set_rotation_minutes(60)
        assert WIDGET.stat_timer.isActive()
    finally:
        restore_widget_defaults()


def t_a_new_widget_reads_rotation_and_startup_settings():
    from PyQt5.QtCore import QSettings
    import widget
    st = QSettings(sandbox_path('settings_boot.ini'), QSettings.IniFormat)
    st.setValue("display/rotation_minutes", 30)
    st.setValue("startup/ask_categorize", False)
    other = widget.MoneyGuiltWidget(settings=st)
    try:
        eq(other.rotation_minutes, 30)
        eq(other.stat_timer.interval(), 1800000)
        eq(other.ask_categorize_at_start, False)
    finally:
        other.tray_icon.hide()


def t_startup_prompt_follows_the_setting():
    from unittest import mock
    import telemetry
    try:
        for enabled in (True, False):
            WIDGET.set_ask_categorize(enabled)
            with mock.patch.object(WIDGET, 'prompt_for_new_transactions') as prompt, \
                 mock.patch.object(WIDGET, 'show_sharing_notice'), \
                 mock.patch.object(telemetry, 'report_in_background') as report:
                WIDGET.startup()
            eq(prompt.called, enabled)
            assert report.called, "reporting still runs at startup either way"
    finally:
        restore_widget_defaults()


def t_the_startup_prompt_is_on_by_default():
    """Standing requirement: every launch asks, unless the user opts out."""
    from PyQt5.QtCore import QSettings
    import widget
    other = widget.MoneyGuiltWidget(settings=QSettings(
        sandbox_path('settings_default_ask.ini'), QSettings.IniFormat))
    try:
        eq(other.ask_categorize_at_start, True)
    finally:
        other.tray_icon.hide()


def t_tray_menu_opens_settings_without_being_moved_by_macos():
    from PyQt5.QtWidgets import QAction
    actions = {a.text(): a for a in WIDGET.tray_icon.contextMenu().actions()}
    assert 'Settings\u2026' in actions, list(actions)
    eq(actions['Settings\u2026'].menuRole(), QAction.NoRole)


def t_open_settings_shows_the_window():
    from unittest import mock
    import settings_dialog
    with mock.patch.object(settings_dialog.SettingsDialog, 'exec_') as shown:
        WIDGET.open_settings()
    eq(shown.call_count, 1)


def t_settings_window_is_native_looking_and_on_top():
    """A child of the widget would inherit its dark translucent stylesheet."""
    from PyQt5.QtCore import Qt as QtC
    d = make_dialog()
    assert d.parent() is None
    assert d.windowFlags() & QtC.WindowStaysOnTopHint
    eq(d.styleSheet(), "")


def t_settings_window_shows_the_current_state():
    try:
        WIDGET.set_manual_privacy(True)
        WIDGET.set_auto_hide(False)
        WIDGET.set_idle_minutes(12)
        WIDGET.set_hide_from_capture(False)
        WIDGET.set_ask_categorize(False)
        WIDGET.set_rotation_minutes(240)
        d = make_dialog()
        eq(d.hide_checkbox.isChecked(), True)
        eq(d.auto_checkbox.isChecked(), False)
        eq(d.idle_spin.value(), 12)
        eq(d.idle_spin.isEnabled(), False, "minutes only matter while auto-hide is on")
        eq(d.capture_checkbox.isChecked(), False)
        eq(d.ask_checkbox.isChecked(), False)
        eq(d.rotation_combo.currentData(), 240)
    finally:
        restore_widget_defaults()


def t_opening_the_window_changes_nothing():
    from unittest import mock
    with mock.patch.object(WIDGET, 'set_manual_privacy') as a, \
         mock.patch.object(WIDGET, 'set_auto_hide') as b, \
         mock.patch.object(WIDGET, 'set_idle_minutes') as c, \
         mock.patch.object(WIDGET, 'set_hide_from_capture') as e, \
         mock.patch.object(WIDGET, 'set_ask_categorize') as f, \
         mock.patch.object(WIDGET, 'set_rotation_minutes') as g:
        make_dialog()
    for handler in (a, b, c, e, f, g):
        handler.assert_not_called()


def t_settings_controls_apply_immediately():
    try:
        d = make_dialog()
        d.hide_checkbox.setChecked(True)
        eq(WIDGET.privacy.manual, True)

        d.auto_checkbox.setChecked(False)
        eq(WIDGET.privacy.auto_hide, False)
        eq(d.idle_spin.isEnabled(), False)
        d.auto_checkbox.setChecked(True)
        eq(d.idle_spin.isEnabled(), True)

        d.idle_spin.setValue(20)
        eq(WIDGET.privacy.idle_limit, 1200)

        d.capture_checkbox.setChecked(False)
        eq(WIDGET.hide_from_capture, False)

        d.ask_checkbox.setChecked(False)
        eq(WIDGET.ask_categorize_at_start, False)

        d.rotation_combo.setCurrentIndex(d.rotation_combo.findData(30))
        eq(WIDGET.rotation_minutes, 30)
        eq(WIDGET.stat_timer.interval(), 1800000)
    finally:
        restore_widget_defaults()


def t_a_setting_stored_outside_the_choices_is_shown_not_hidden():
    try:
        WIDGET.rotation_minutes = 7
        d = make_dialog()
        eq(d.rotation_combo.currentData(), 7)
        eq(WIDGET.rotation_minutes, 7, "opening the window must not change it")
    finally:
        restore_widget_defaults()


def t_share_checkbox_controls_reporting():
    import telemetry
    def body():
        d = make_dialog()
        eq(d.share_checkbox.isChecked(), True, "on by default")
        d.share_checkbox.setChecked(False)
        eq(telemetry.is_enabled(), False)
        assert 'off' in d.share_status.text(), d.share_status.text()
        d.share_checkbox.setChecked(True)
        eq(telemetry.is_enabled(), True)
        assert 'on' in d.share_status.text()
    with_telemetry(body)


def t_status_text_covers_every_state():
    d = make_dialog()
    base = {"enabled": True, "collector_configured": True,
            "has_shared": False, "delete_pending": False}
    assert 'Sharing is on' in d.status_text(base)
    assert 'Sharing is off' in d.status_text(dict(base, enabled=False))
    assert 'No reporting server' in d.status_text(dict(base, collector_configured=False))
    pending = d.status_text(dict(base, delete_pending=True, enabled=False))
    assert 'pending' in pending and 'retried automatically' in pending


def t_one_click_deletes_what_was_reported_and_turns_sharing_off():
    import telemetry
    asked = []
    def body():
        pretend_shared()
        d = make_dialog()
        loop, got = wait_for_deletion(d)
        d.delete_button.click()               # the only step
        loop.exec_()
        eq(got, ['deleted'])
        eq(asked, [("https://collector.example", "123e4567-e89b-12d3-a456-426614174000")])
        eq(telemetry.is_enabled(), False, "deleting must stop sharing")
        eq(d.share_checkbox.isChecked(), False)
        assert 'Deleted' in d.delete_result.text(), d.delete_result.text()
        assert d.delete_button.isEnabled(), "usable again afterwards"
        assert 'install_id' not in telemetry.load_config()
    with_telemetry(body, post_delete=lambda url, i: asked.append((url, i)) or True)


def t_deletion_says_so_when_nothing_had_been_shared():
    def body():
        d = make_dialog()
        loop, got = wait_for_deletion(d)
        d.delete_button.click()
        loop.exec_()
        eq(got, ['nothing'])
        assert 'Nothing had been shared' in d.delete_result.text()
    with_telemetry(body)


def t_an_unreachable_server_is_reported_and_queued_not_lost():
    import telemetry
    def body():
        pretend_shared()
        d = make_dialog()
        loop, got = wait_for_deletion(d)
        d.delete_button.click()
        loop.exec_()
        eq(got, ['pending'])
        assert 'retried automatically' in d.delete_result.text()
        assert telemetry.status()['delete_pending']
        assert 'pending' in d.share_status.text()
        eq(telemetry.is_enabled(), False, "still stops sharing")
    with_telemetry(body, post_delete=lambda url, i: False)


def t_the_button_is_blocked_while_a_deletion_is_running():
    import threading
    release = threading.Event()
    calls = []
    def slow(url, install_id):
        calls.append(install_id)
        release.wait(5)
        return True
    def body():
        pretend_shared()
        d = make_dialog()
        loop, got = wait_for_deletion(d, seconds=8)
        d.delete_button.click()
        QT_APP.processEvents()
        eq(d.delete_button.isEnabled(), False, "no second click while it runs")
        assert 'Deleting' in d.delete_result.text()
        d.delete_reported()                    # a second attempt is ignored
        release.set()
        loop.exec_()
        eq(got, ['deleted'])
        eq(len(calls), 1, "exactly one request went out")
    with_telemetry(body, post_delete=slow)


def t_closing_waits_for_a_running_deletion():
    import threading
    release = threading.Event()
    def slow(url, install_id):
        release.wait(5)
        return True
    def body():
        pretend_shared()
        d = make_dialog()
        d.delete_button.click()
        threading.Timer(0.3, release.set).start()
        d.done(0)                              # must not destroy a running thread
        assert not d._worker.isRunning()
    with_telemetry(body, post_delete=slow)


def t_a_pending_deletion_is_retried_every_fifteen_minutes():
    import telemetry
    eq(WIDGET.delete_retry_timer.interval(), 900000)
    assert WIDGET.delete_retry_timer.isActive()


def wait_for_signal(signal, seconds=5):
    from PyQt5.QtCore import QEventLoop, QTimer
    loop, got = QEventLoop(), []
    signal.connect(lambda outcome: (got.append(outcome), loop.quit()))
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    return loop, got


def answer(reply):
    """Patch the confirmation box so a test chooses Yes or Cancel."""
    from unittest import mock
    from PyQt5.QtWidgets import QMessageBox
    return mock.patch.object(QMessageBox, 'question', return_value=(
        QMessageBox.Yes if reply == "yes" else QMessageBox.Cancel))


def t_bank_status_reflects_the_keychain():
    fresh_bank(token="tok")
    d = make_dialog()
    assert 'is linked' in d.bank_status_label.text(), d.bank_status_label.text()
    assert d.disconnect_button.isEnabled()
    fresh_bank()
    d = make_dialog()
    assert 'No bank account' in d.bank_status_label.text()
    assert not d.disconnect_button.isEnabled(), "nothing to disconnect"
    fresh_bank(unreadable=True)
    d = make_dialog()
    assert 'unknown' in d.bank_status_label.text()
    assert d.disconnect_button.isEnabled(), "still offered: it may well be linked"


def t_disconnect_asks_first_and_cancel_is_the_default_answer():
    from PyQt5.QtWidgets import QMessageBox
    fresh_bank(token="tok")
    d = make_dialog()
    with answer("cancel") as question:
        d.disconnect_button.click()
    eq(question.call_count, 1)
    args = question.call_args.args
    assert 'Disconnect' in args[2], args[2]
    eq(args[3], QMessageBox.Yes | QMessageBox.Cancel)
    eq(args[4], QMessageBox.Cancel, "Cancel must be the default, for a step like this")
    eq(BANK_PLAID.removed, [], "declining must not contact Plaid")
    eq(BANK_STORE.token, "tok")
    assert d.disconnect_button.isEnabled()


def t_confirmed_disconnect_revokes_then_forgets_the_token():
    fresh_bank(token="tok")
    d = make_dialog()
    loop, got = wait_for_signal(d.disconnect_finished)
    with answer("yes"):
        d.disconnect_button.click()
    loop.exec_()
    eq(got, ["disconnected"])
    eq(BANK_PLAID.removed, ["tok"])
    eq(BANK_STORE.token, None)
    assert 'Disconnected' in d.bank_result.text(), d.bank_result.text()
    assert 'still on this Mac' in d.bank_result.text()
    assert 'No bank account' in d.bank_status_label.text()
    assert not d.disconnect_button.isEnabled()


def t_a_failed_disconnect_keeps_the_token_and_can_be_retried():
    fresh_bank(token="tok", error=OSError("offline"))
    d = make_dialog()
    loop, got = wait_for_signal(d.disconnect_finished)
    with answer("yes"):
        d.disconnect_button.click()
    loop.exec_()
    eq(got, ["failed"])
    eq(BANK_STORE.token, "tok", "the token must survive a failed revoke")
    assert 'nothing was changed' in d.bank_result.text()
    assert d.disconnect_button.isEnabled(), "and it can be tried again"
    BANK_PLAID.error = None
    loop, got = wait_for_signal(d.disconnect_finished)
    with answer("yes"):
        d.disconnect_button.click()
    loop.exec_()
    eq(got, ["disconnected"])


def t_the_disconnect_button_is_blocked_while_it_runs():
    import threading
    gate = threading.Event()
    fresh_bank(token="tok", gate=gate)
    d = make_dialog()
    loop, got = wait_for_signal(d.disconnect_finished, seconds=8)
    with answer("yes") as question:
        d.disconnect_button.click()
        QT_APP.processEvents()
        eq(d.disconnect_button.isEnabled(), False)
        assert 'Disconnecting' in d.bank_result.text()
        d.disconnect_bank_clicked()          # a second attempt is ignored outright
        eq(question.call_count, 1, "it must not even ask again")
        gate.set()
        loop.exec_()
    eq(got, ["disconnected"])
    eq(len(BANK_PLAID.removed), 1, "exactly one request")


def t_closing_waits_for_a_running_disconnect():
    import threading
    gate = threading.Event()
    fresh_bank(token="tok", gate=gate)
    d = make_dialog()
    with answer("yes"):
        d.disconnect_button.click()
    threading.Timer(0.3, gate.set).start()
    d.done(0)                                 # must not destroy a running thread
    assert not d._disconnect_worker.isRunning()


def t_disconnect_without_plaid_credentials_changes_nothing():
    from unittest import mock
    import disconnect
    fresh_bank(token="tok")
    d = make_dialog()
    loop, got = wait_for_signal(d.disconnect_finished)
    with answer("yes"), mock.patch.object(disconnect, '_make_client',
                                          side_effect=ValueError("Missing credentials")):
        d.disconnect_button.click()
        loop.exec_()
    eq(got, ["unconfigured"])
    eq(BANK_STORE.token, "tok")
    assert "isn't set up" in d.bank_result.text()


def seed_erasable(count=3):
    seed_reviewable(count)


def rows_left():
    return len(database.get_all_transactions(days=99999))


def t_erase_asks_first_and_cancel_keeps_everything():
    from unittest import mock
    def body():
        seed_erasable(3)
        d = make_dialog()
        with answer("cancel") as question, mock.patch.object(WIDGET, 'advance_stat') as adv:
            d.erase_button.click()
        eq(question.call_count, 1)
        assert "can't be undone" in question.call_args.args[2]
        eq(question.call_args.args[4], __import__('PyQt5.QtWidgets', fromlist=['QMessageBox']).QMessageBox.Cancel)
        eq(rows_left(), 3)
        adv.assert_not_called()
        assert d.erase_result.isHidden()
    with_temp_db(body)


def t_confirmed_erase_deletes_everything_and_refreshes_the_widget():
    from unittest import mock
    def body():
        seed_erasable(3)
        d = make_dialog()
        with answer("yes"), mock.patch.object(WIDGET, 'advance_stat') as adv:
            d.erase_button.click()
        eq(rows_left(), 0)
        eq(adv.call_count, 1, "the widget must stop showing what was erased")
        text = d.erase_result.text()
        assert 'Deleted 3 transactions and 1 account,' in text, text
    with_temp_db(body)


def t_erase_wording_is_singular_when_it_should_be():
    from unittest import mock
    def body():
        seed_erasable(1)
        d = make_dialog()
        with answer("yes"), mock.patch.object(WIDGET, 'advance_stat'):
            d.erase_button.click()
        assert 'Deleted 1 transaction and 1 account,' in d.erase_result.text(), d.erase_result.text()
    with_temp_db(body)


def t_erase_leaves_the_bank_link_and_sharing_alone():
    from unittest import mock
    import telemetry
    def inner():
        def body():
            seed_erasable(2)
            fresh_bank(token="tok")
            telemetry.set_enabled(True)
            d = make_dialog()
            with answer("yes"), mock.patch.object(WIDGET, 'advance_stat'):
                d.erase_button.click()
            eq(BANK_STORE.token, "tok", "erasing data must not disconnect the bank")
            eq(BANK_PLAID.removed, [])
            eq(telemetry.is_enabled(), True, "nor change sharing")
        with_temp_db(body)
    with_telemetry(inner)


def t_the_confirmations_say_what_they_do_and_do_not_do():
    import settings_dialog as sd
    for text in (sd.CONFIRM_DISCONNECT,):
        assert 'revoke' in text and 'stay on this Mac' in text, text
    assert "can't be undone" in sd.CONFIRM_ERASE
    assert "doesn't disconnect your bank" in sd.CONFIRM_ERASE
    assert "doesn't remove anything you shared" in sd.CONFIRM_ERASE


def t_show_data_folder_opens_the_real_folder():
    from unittest import mock
    from PyQt5.QtCore import QUrl
    import paths
    from PyQt5.QtGui import QDesktopServices
    d = make_dialog()
    with mock.patch.object(QDesktopServices, 'openUrl') as opener:
        d.show_data_folder()
    opener.assert_called_once_with(QUrl.fromLocalFile(paths.data_dir()))
    eq(d.folder_label.text(), paths.data_dir())


# ----------------------------------------------------- first-launch notice
def run_notice(click_text):
    """Show the notice and press the named button, without a modal loop."""
    from unittest import mock
    from PyQt5.QtWidgets import QMessageBox
    import telemetry
    shown = {}
    def fake_exec(box):
        shown["text"] = box.informativeText()
        shown["buttons"] = [b.text() for b in box.buttons()]
        if click_text:
            [b for b in box.buttons() if b.text() == click_text][0].click()
    with mock.patch.object(QMessageBox, 'exec_', new=fake_exec):
        WIDGET.show_sharing_notice()
    return shown


def t_notice_tells_people_to_use_settings_not_a_menu_item_that_is_gone():
    def body():
        shown = run_notice("OK")
        assert "Settings" in shown["text"], shown["text"]
        assert "Share Anonymous Wasted Total" not in shown["text"]
        eq(shown["buttons"], ["OK", "Turn Off Sharing"])
    with_telemetry(body)


def t_notice_turn_off_sharing_really_turns_it_off():
    import telemetry
    def body():
        run_notice("Turn Off Sharing")
        eq(telemetry.is_enabled(), False)
        eq(telemetry.needs_notice(), False, "and it isn't shown again")
    with_telemetry(body)


def t_notice_ok_leaves_sharing_on_and_is_shown_once():
    import telemetry
    def body():
        run_notice("OK")
        eq(telemetry.is_enabled(), True)
        eq(telemetry.needs_notice(), False)
        eq(run_notice(None), {}, "the second launch shows nothing")
    with_telemetry(body)


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
    ("privacy: masking hides the value, subtitle figures and the ring arc", t_priv_masks_value_subtitle_and_ring),
    ("privacy: no digit survives on any stat while masked", t_priv_no_digit_survives_on_any_stat),
    ("privacy: masked amounts are not highlighted red", t_priv_masked_amounts_are_not_red),
    ("privacy: a vendor and its amount collapse to one mask", t_priv_two_line_vendor_collapses_to_one_mask),
    ("privacy: the value stays centred while masked", t_priv_stays_centred_while_masked),
    ("privacy: activity alone does not reveal after an idle lock", t_priv_idle_lock_activity_does_not_reveal),
    ("privacy: a click reveals after an idle lock without advancing", t_priv_click_reveals_without_advancing),
    ("privacy: a click does not undo manual hiding", t_priv_click_does_not_undo_manual_hiding),
    ("privacy: settings persist", t_priv_settings_persist),
    ("privacy: a new widget reads the saved settings", t_priv_a_new_widget_reads_the_saved_settings),
    ("privacy: defaults keep the number visible", t_priv_defaults_keep_the_number_visible),
    ("privacy: the native capture call is skipped off cocoa", t_priv_capture_call_is_skipped_off_cocoa),
    ("privacy: the native capture call is made on cocoa", t_priv_capture_call_is_made_on_cocoa),
    ("privacy: the tray menu does not duplicate the Settings page", t_tray_menu_does_not_duplicate_the_settings_page),
    ("feedback: the menu item opens a mail draft", t_feedback_menu_item_opens_a_mail_draft),
    ("feedback: the form validates, then sends", t_feedback_form_validates_then_sends),
    ("feedback: a failed send shows why and can be retried", t_feedback_form_shows_errors_and_allows_retry),
    ("privacy: the idle check runs twice a minute", t_priv_idle_timer_runs_twice_a_minute),
    ("settings: idle minutes apply, persist and clamp", t_set_widget_idle_minutes_applies_persists_and_clamps),
    ("settings: the rotation interval drives the timer", t_set_widget_rotation_drives_the_timer),
    ("settings: a new widget reads rotation and startup settings", t_a_new_widget_reads_rotation_and_startup_settings),
    ("settings: the startup prompt follows its setting", t_startup_prompt_follows_the_setting),
    ("settings: the startup prompt is on by default", t_the_startup_prompt_is_on_by_default),
    ("settings: the tray has a Settings item macOS won't move", t_tray_menu_opens_settings_without_being_moved_by_macos),
    ("settings: open_settings shows the window", t_open_settings_shows_the_window),
    ("settings: the window is unparented, unstyled and on top", t_settings_window_is_native_looking_and_on_top),
    ("settings: the window shows the current state", t_settings_window_shows_the_current_state),
    ("settings: opening the window changes nothing", t_opening_the_window_changes_nothing),
    ("settings: controls apply immediately", t_settings_controls_apply_immediately),
    ("settings: an odd stored value is shown, not hidden", t_a_setting_stored_outside_the_choices_is_shown_not_hidden),
    ("settings: the share checkbox controls reporting", t_share_checkbox_controls_reporting),
    ("settings: status text covers every state", t_status_text_covers_every_state),
    ("settings: one click deletes the reported data and stops sharing", t_one_click_deletes_what_was_reported_and_turns_sharing_off),
    ("settings: deleting says so when nothing was shared", t_deletion_says_so_when_nothing_had_been_shared),
    ("settings: an unreachable server is reported and queued", t_an_unreachable_server_is_reported_and_queued_not_lost),
    ("settings: the delete button is blocked while it runs", t_the_button_is_blocked_while_a_deletion_is_running),
    ("settings: closing waits for a running deletion", t_closing_waits_for_a_running_deletion),
    ("settings: a pending deletion is retried every fifteen minutes", t_a_pending_deletion_is_retried_every_fifteen_minutes),
    ("bank: status reflects the Keychain", t_bank_status_reflects_the_keychain),
    ("bank: disconnect asks first and Cancel is the default", t_disconnect_asks_first_and_cancel_is_the_default_answer),
    ("bank: a confirmed disconnect revokes then forgets the token", t_confirmed_disconnect_revokes_then_forgets_the_token),
    ("bank: a failed disconnect keeps the token and can be retried", t_a_failed_disconnect_keeps_the_token_and_can_be_retried),
    ("bank: the disconnect button is blocked while it runs", t_the_disconnect_button_is_blocked_while_it_runs),
    ("bank: closing waits for a running disconnect", t_closing_waits_for_a_running_disconnect),
    ("bank: no Plaid credentials changes nothing", t_disconnect_without_plaid_credentials_changes_nothing),
    ("erase: asks first and Cancel keeps everything", t_erase_asks_first_and_cancel_keeps_everything),
    ("erase: confirming deletes everything and refreshes the widget", t_confirmed_erase_deletes_everything_and_refreshes_the_widget),
    ("erase: wording is singular when it should be", t_erase_wording_is_singular_when_it_should_be),
    ("erase: leaves the bank link and sharing alone", t_erase_leaves_the_bank_link_and_sharing_alone),
    ("bank: the confirmations say what they do and do not do", t_the_confirmations_say_what_they_do_and_do_not_do),
    ("settings: Show in Finder opens the real data folder", t_show_data_folder_opens_the_real_folder),
    ("notice: points at Settings, not a removed menu item", t_notice_tells_people_to_use_settings_not_a_menu_item_that_is_gone),
    ("notice: Turn Off Sharing really turns it off", t_notice_turn_off_sharing_really_turns_it_off),
    ("notice: OK leaves sharing on, and it is shown once", t_notice_ok_leaves_sharing_on_and_is_shown_once),
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

    from unittest import mock
    import disconnect
    bank_patches = [mock.patch.object(disconnect, 'secure_store', BANK_STORE),
                    mock.patch.object(disconnect, '_make_client', lambda: BANK_PLAID)]
    for patch in bank_patches:
        patch.start()

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
        for patch in bank_patches:
            patch.stop()
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
