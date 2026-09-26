#!/usr/bin/env python3
"""Test script for Plaid integration"""

from plaid_client import PlaidClient
import json

def test_plaid():
    print("=" * 60)
    print("Plaid Integration Test")
    print("=" * 60)

    try:
        client = PlaidClient()
        print("✓ Plaid client initialized successfully\n")
    except Exception as e:
        print(f"✗ Failed to initialize Plaid client: {e}")
        return

    # Step 1: Create link token
    print("Step 1: Creating link token...")
    try:
        link_token = client.create_link_token()
        print(f"✓ Link token created: {link_token}\n")
    except Exception as e:
        print(f"✗ Failed to create link token: {e}\n")
        return

    # Step 2: Instructions for linking account in sandbox
    print("Step 2: Link your account in Plaid Sandbox")
    print("-" * 60)
    print("For SANDBOX TESTING:")
    print("1. Username: user_good")
    print("2. Password: pass_good")
    print("3. Institution: Chase")
    print("\nAfter linking, you'll get a public_token. Paste it below.")
    print("-" * 60)

    public_token = input("\nEnter public_token (or 'skip' to test with hardcoded): ").strip()

    if public_token.lower() == 'skip':
        # For testing, we can use a hardcoded flow
        print("\nSkipping token exchange for now.")
        print("To test fully, you need to:")
        print("1. Use Plaid Link UI to authenticate")
        print("2. Get a public_token from the auth flow")
        print("3. Run this script again and paste the token")
        return

    # Step 3: Exchange public token for access token
    print("\nStep 3: Exchanging public token for access token...")
    try:
        access_token = client.exchange_public_token(public_token)
        print(f"✓ Access token obtained: {access_token[:20]}...\n")
    except Exception as e:
        print(f"✗ Failed to exchange token: {e}\n")
        return

    # Step 4: Get accounts
    print("Step 4: Fetching accounts...")
    try:
        accounts = client.get_accounts(access_token)
        print(f"✓ Found {len(accounts)} account(s):\n")
        for account in accounts:
            print(f"  - {account.name} ({account.type})")
            print(f"    ID: {account.account_id}")
            print(f"    Balance: ${account.balances.current}\n")
    except Exception as e:
        print(f"✗ Failed to get accounts: {e}\n")
        return

    # Step 5: Get transactions
    print("Step 5: Fetching last 30 days of transactions...")
    try:
        transactions = client.get_transactions(access_token)
        print(f"✓ Found {len(transactions)} transaction(s):\n")
        for tx in transactions[:5]:  # Show first 5
            print(f"  - {tx.date}: {tx.name}")
            print(f"    Amount: ${tx.amount}")
            print(f"    Category: {tx.personal_finance_category}\n")
        if len(transactions) > 5:
            print(f"  ... and {len(transactions) - 5} more transactions\n")
    except Exception as e:
        print(f"✗ Failed to get transactions: {e}\n")
        return

    print("=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
    print(f"\nAccess token obtained: {access_token[:16]}... (not shown in full)")
    print("Save this token to test other parts of the app.")

if __name__ == "__main__":
    test_plaid()
