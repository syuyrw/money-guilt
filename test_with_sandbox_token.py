#!/usr/bin/env python3
"""Test Plaid integration with sandbox test token"""

import os
from plaid_client import PlaidClient
from datetime import datetime, timedelta


def load_access_token():
    """Token from PLAID_ACCESS_TOKEN or access_token.txt, never from source."""
    token = os.getenv("PLAID_ACCESS_TOKEN")
    if not token:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "access_token.txt")
        if os.path.exists(path):
            with open(path) as fh:
                token = fh.read().strip()
    if not token:
        raise SystemExit("No access token. Set PLAID_ACCESS_TOKEN, or link an account "
                         "first (python link_app.py) to create access_token.txt.")
    return token


def mask(token):
    return token[:16] + "..." if token else "(none)"



def test_with_sandbox_token():
    print("=" * 60)
    print("Testing Plaid Integration with Sandbox Token")
    print("=" * 60)

    access_token = load_access_token()
    client = PlaidClient()
    print("✓ Plaid client initialized\n")

    # Test 1: Get accounts
    print("Step 1: Fetching accounts...")
    try:
        accounts = client.get_accounts(access_token)
        print(f"✓ Found {len(accounts)} account(s):\n")
        for account in accounts:
            print(f"  - {account.name} ({account.type})")
            if account.subtype:
                print(f"    Subtype: {account.subtype}")
            print(f"    Balance: ${account.balances.current}")
            print(f"    Account ID: {account.account_id}\n")
    except Exception as e:
        print(f"✗ Error: {e}\n")
        return

    # Test 2: Get transactions (last 30 days)
    print("Step 2: Fetching transactions (last 30 days)...")
    try:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=30)

        transactions = client.get_transactions(access_token, start_date, end_date)
        print(f"✓ Found {len(transactions)} transaction(s):\n")

        # Group by category
        by_category = {}
        for tx in transactions:
            category = tx.personal_finance_category.primary if tx.personal_finance_category else "Uncategorized"
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(tx)

        # Show summary by category
        for category in sorted(by_category.keys()):
            txs = by_category[category]
            total = sum(tx.amount for tx in txs)
            print(f"  {category}: {len(txs)} transactions, ${total:.2f}")

        print(f"\nFirst 5 transactions:")
        for tx in transactions[:5]:
            print(f"  {tx.date}: {tx.name}")
            print(f"    Amount: ${tx.amount}")
            print(f"    Category: {tx.personal_finance_category}\n")

    except Exception as e:
        print(f"✗ Error: {e}\n")
        return

    print("=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
    print(f"\nUsed access token: {mask(access_token)}")

if __name__ == "__main__":
    test_with_sandbox_token()
