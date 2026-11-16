"""The local SQLite database: schema, and every read/write to it.

Everything Money Guilt knows about your spending lives in one SQLite file
(a lightweight, file-based database - no separate server to run). This
module is the only place that talks to it directly; every other file goes
through the functions defined here.
"""
import sqlite3
from datetime import datetime
from contextlib import contextmanager
import logging

from data import paths

logger = logging.getLogger(__name__)

# Where the .db file lives on disk. Computed once, at import time, from
# paths.py (which knows about the private Application Support folder).
DATABASE_PATH = paths.db_path()


@contextmanager
def get_db():
    """Get database connection with context manager"""
    # sqlite3.connect() opens (or creates) the .db file and returns a
    # connection object you can run SQL through.
    conn = sqlite3.connect(DATABASE_PATH)
    # By default, a row comes back as a plain tuple of values (e.g.
    # ('t1', 'a1', 12.5)), where you'd have to remember that index 2 is the
    # amount. sqlite3.Row lets you access columns by name instead
    # (row['amount']), which is what dict(row) below relies on.
    conn.row_factory = sqlite3.Row
    try:
        # @contextmanager + yield is what makes this function usable in a
        # `with get_db() as conn:` block elsewhere in this file. Code before
        # `yield` runs when the `with` block starts; the `conn` object is
        # handed to the caller; and the `finally` block below runs no
        # matter what - even if the code inside `with` raises an exception -
        # which guarantees the connection always gets closed.
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize database schema"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Accounts table: one row per linked bank account (checking,
        # savings, credit card, ...).
        # "IF NOT EXISTS" makes this safe to run every time the app starts:
        # it only creates the table the first time, and does nothing on
        # every later run where the table's already there.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT,
                subtype TEXT,
                balance REAL,
                last_synced TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Transactions table: one row per individual purchase/deposit/etc.
        # FOREIGN KEY ties each transaction back to the account it came
        # from, so the database itself enforces that account_id refers to
        # a real row in the accounts table.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                date DATE NOT NULL,
                name TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT,
                category_primary TEXT,
                is_wasteful BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
        """)

        # An index speeds up lookups on a column at the cost of a little
        # extra disk space and slightly slower writes - worth it here since
        # almost every query below filters or sorts by date or account_id.
        # Without these, SQLite would have to scan every single row.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_date
            ON transactions(date)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_account
            ON transactions(account_id)
        """)

        # Categories table (for customizing what's "wasteful")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                is_wasteful BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # --- Migrations ---
        # These three blocks let the app add new columns to a database that
        # already exists on someone's machine from an older version of the
        # app, without destroying their data. PRAGMA table_info() asks
        # SQLite to describe the table; each row it returns is a tuple
        # where index 1 is the column's name, so this builds a plain list
        # of every column name currently in the table.
        columns = [row[1] for row in cursor.execute("PRAGMA table_info(transactions)")]

        # Track which transactions the user has reviewed (marked wasteful
        # or not). Only added if an older database doesn't have it yet.
        if "reviewed" not in columns:
            cursor.execute("ALTER TABLE transactions ADD COLUMN reviewed BOOLEAN DEFAULT 0")

        if "prompted" not in columns:
            cursor.execute("ALTER TABLE transactions ADD COLUMN prompted BOOLEAN DEFAULT 0")
            # Anything already reviewed has been asked about. Without this,
            # every old transaction would look "never asked" the first time
            # this migration runs, and the categorize dialog would try to
            # ask about all of them again.
            cursor.execute("UPDATE transactions SET prompted = 1 WHERE reviewed = 1")
        if "datetime" not in columns:
            cursor.execute("ALTER TABLE transactions ADD COLUMN datetime TEXT")

        conn.commit()  # writes are not actually saved to disk until commit()
        logger.info("Database initialized")


def save_accounts(accounts):
    """Save accounts from Plaid to database"""
    with get_db() as conn:
        cursor = conn.cursor()

        for account in accounts:
            # INSERT OR REPLACE: if a row with this id already exists
            # (same account, synced before), overwrite it; otherwise create
            # a new row. This is how re-syncing the same account every day
            # updates its balance instead of creating duplicates.
            #
            # The `?` placeholders (and the tuple passed as the second
            # argument) are a *parameterized query*: SQLite fills in the
            # values itself, safely, rather than this code building a SQL
            # string by hand. Never paste values directly into SQL text -
            # that's how SQL-injection bugs happen.
            cursor.execute("""
                INSERT OR REPLACE INTO accounts
                (id, name, type, subtype, balance, last_synced)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                account.account_id,
                account.name,
                # Plaid's `type`/`subtype` fields are enum-like objects, not
                # plain strings; str(...) converts them, and the `if ... else
                # None` guards against a field Plaid left empty.
                str(account.type) if account.type else None,
                str(account.subtype) if account.subtype else None,
                account.balances.current,
                datetime.now()
            ))

        conn.commit()
        logger.info(f"Saved {len(accounts)} account(s)")


def save_transactions(transactions):
    """Save transactions from Plaid to database"""
    with get_db() as conn:
        cursor = conn.cursor()

        for tx in transactions:
            category = ""
            category_primary = ""

            # Plaid's category info can be missing entirely, or present but
            # with its "detailed" sub-field empty - each `if` here guards
            # against one of those two possibilities so this never crashes
            # on a transaction Plaid didn't fully categorize.
            if tx.personal_finance_category:
                category_primary = tx.personal_finance_category.primary
                if tx.personal_finance_category.detailed:
                    category = tx.personal_finance_category.detailed

            # Unlike save_accounts' blanket "replace whatever was there",
            # this uses "ON CONFLICT ... DO UPDATE" (an "upsert"): insert a
            # new row normally, but if a transaction with this id already
            # exists, only refresh the fields that can legitimately change
            # (Plaid sometimes finalizes a transaction's date/amount after
            # it was first seen as "pending"). Notably, it deliberately does
            # NOT touch is_wasteful, reviewed or prompted on conflict - a
            # re-sync must never undo a choice the user already made.
            cursor.execute("""
                INSERT INTO transactions
                (id, account_id, date, name, amount, category, category_primary, datetime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    datetime = excluded.datetime,
                    date = excluded.date,
                    name = excluded.name,
                    amount = excluded.amount
            """, (
                tx.transaction_id,
                tx.account_id,
                tx.date,
                tx.name,
                tx.amount,
                category,
                category_primary,
                # Plaid often leaves both null; stored as NULL when missing.
                # getattr(tx, 'datetime', None) reads the attribute if it
                # exists, or returns None instead of crashing if it doesn't;
                # the `or` chain tries datetime, then authorized_datetime,
                # then falls back to an empty string, and the final
                # `... or None` turns that empty string into a real NULL.
                str(getattr(tx, 'datetime', None) or getattr(tx, 'authorized_datetime', None) or '') or None
            ))

        conn.commit()
        logger.info(f"Saved {len(transactions)} transaction(s)")


def get_all_transactions(days=30):
    """Get all transactions from the last N days"""
    with get_db() as conn:
        cursor = conn.cursor()
        # SQLite's date('now', '-N days') computes "N days before right
        # now" directly in the database, so the comparison happens entirely
        # in SQL rather than pulling every row into Python first. The `||`
        # is SQLite's string-concatenation operator, used here to build a
        # string like "-30 days" out of the `days` parameter.
        cursor.execute("""
            SELECT * FROM transactions
            WHERE date >= date('now', '-' || ? || ' days')
            ORDER BY date DESC
        """, (days,))
        # dict(row) turns each sqlite3.Row into a plain dictionary, which is
        # easier for the rest of the app to work with than a Row object.
        return [dict(row) for row in cursor.fetchall()]


def get_transactions_by_category(days=30):
    """Get spending by category"""
    with get_db() as conn:
        cursor = conn.cursor()
        # GROUP BY category_primary collapses all transactions that share a
        # category into one summary row each, with COUNT/SUM computed per
        # group rather than per individual transaction.
        cursor.execute("""
            SELECT
                category_primary,
                COUNT(*) as count,
                SUM(amount) as total
            FROM transactions
            WHERE date >= date('now', '-' || ? || ' days')
            GROUP BY category_primary
            ORDER BY total DESC
        """, (days,))
        return [dict(row) for row in cursor.fetchall()]


def get_accounts():
    """Get all linked accounts"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts")
        return [dict(row) for row in cursor.fetchall()]


def mark_transaction_wasteful(transaction_id, is_wasteful):
    """Mark a transaction as wasteful/not wasteful"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE transactions SET is_wasteful = ? WHERE id = ?
        """, (is_wasteful, transaction_id))
        conn.commit()


def get_wasteful_spending(days=30):
    """Get total wasteful spending"""
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
        # SUM()/COUNT() return SQL NULL (which Python sees as None) when no
        # rows match at all - the `or 0` turns that into a plain 0 so
        # callers never have to special-case "no data yet" themselves.
        return {
            'total': result['total_wasteful'] or 0,
            'count': result['count'] or 0
        }


def get_total_wasted():
    """Total wasted dollars across every transaction, all time"""
    with get_db() as conn:
        # `amount > 0` excludes refunds/income (Plaid represents money
        # leaving the account as positive and money coming in as negative),
        # so a refund on a wasteful purchase doesn't count as "more waste".
        row = conn.execute("""
            SELECT SUM(amount) AS total, COUNT(*) AS count
            FROM transactions
            WHERE is_wasteful = 1 AND amount > 0
        """).fetchone()
    return {'total': row['total'] or 0, 'count': row['count'] or 0}


def get_total_spending(days=30):
    """Get total spending (excluding income)"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE amount > 0
            AND date >= date('now', '-' || ? || ' days')
        """, (days,))
        result = cursor.fetchone()
        return result['total'] or 0
