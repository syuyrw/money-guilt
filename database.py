import sqlite3
from datetime import datetime
from contextlib import contextmanager
import logging

import paths

logger = logging.getLogger(__name__)

DATABASE_PATH = paths.db_path()


@contextmanager
def get_db():
    """Get database connection with context manager"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize database schema"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Accounts table
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

        # Transactions table
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

        # Create index on date for faster queries
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

        conn.commit()
        logger.info("Database initialized")


def save_accounts(accounts):
    """Save accounts from Plaid to database"""
    with get_db() as conn:
        cursor = conn.cursor()

        for account in accounts:
            cursor.execute("""
                INSERT OR REPLACE INTO accounts
                (id, name, type, subtype, balance, last_synced)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                account.account_id,
                account.name,
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

            if tx.personal_finance_category:
                category_primary = tx.personal_finance_category.primary
                if tx.personal_finance_category.detailed:
                    category = tx.personal_finance_category.detailed

            cursor.execute("""
                INSERT OR REPLACE INTO transactions
                (id, account_id, date, name, amount, category, category_primary)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                tx.transaction_id,
                tx.account_id,
                tx.date,
                tx.name,
                tx.amount,
                category,
                category_primary
            ))

        conn.commit()
        logger.info(f"Saved {len(transactions)} transaction(s)")


def get_all_transactions(days=30):
    """Get all transactions from the last N days"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM transactions
            WHERE date >= date('now', '-' || ? || ' days')
            ORDER BY date DESC
        """, (days,))
        return [dict(row) for row in cursor.fetchall()]


def get_transactions_by_category(days=30):
    """Get spending by category"""
    with get_db() as conn:
        cursor = conn.cursor()
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
        return {
            'total': result['total_wasteful'] or 0,
            'count': result['count'] or 0
        }


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
