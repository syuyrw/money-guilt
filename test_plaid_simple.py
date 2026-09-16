#!/usr/bin/env python3
"""Simple non-interactive Plaid test"""

from plaid_client import PlaidClient

client = PlaidClient()

# Test 1: Create link token
link_token = client.create_link_token()
print(f"✓ Link token created: {link_token}")
print("\nTo complete testing, you need to:")
print("1. Open this link in a browser:")
print(f"   https://sandbox.plaid.com/link?token={link_token}")
print("2. Use these sandbox credentials:")
print("   Username: user_good")
print("   Password: pass_good")
print("   Institution: Chase")
print("3. Complete the flow to get a public_token")
print("4. Come back and we'll exchange it for an access_token")
