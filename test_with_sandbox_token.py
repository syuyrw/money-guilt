#!/usr/bin/env python3
"""Test Plaid integration with sandbox test token"""

from plaid_client import PlaidClient
from datetime import datetime, timedelta

# Plaid provides test access tokens for sandbox testing
# This is a test token that works in sandbox environment
TEST_ACCESS_TOKEN = "access-sandbox-c9c140ab-0c32-48f1-8363-9d7af6624e89"

def test_with_sandbox_token():
    print("=" * 60)
    print("Testing Plaid Integration with Sandbox Token")
    print("=" * 60)

    client = PlaidClient()
    print("✓ Plaid client initialized\n")

    # Test 1: Get accounts
    print("Step 1: Fetching accounts...")
    try:
        accounts = client.get_accounts(TEST_ACCESS_TOKEN)
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

        transactions = client.get_transactions(TEST_ACCESS_TOKEN, start_date, end_date)
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
    print(f"\nAccess token for future testing: {TEST_ACCESS_TOKEN}")

if __name__ == "__main__":
    test_with_sandbox_token()
