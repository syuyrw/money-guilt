"""An early, exploratory prototype of the Plaid connection code.

Not part of the real app anymore (superseded entirely by
banking/plaid_client.py) and never finished - it builds a Plaid client and
stops. It's kept only because a regression test (in tests/test_paths.py)
checks it never prints the secret, a real bug this file used to have.
"""
import plaid
from plaid.api import plaid_api
import os
import sys

# So "from data import paths" below works when this is run directly, since
# Python would otherwise only know about this scripts/ folder, not the
# project root the `data` package lives under.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import paths

# Import credentials from the private data directory
paths.load_env()

client_id = os.getenv("PLAID_CLIENT_ID")
secret = os.getenv("PLAID_SECRET")
plaid_env = os.getenv("PLAID_ENV")

# Never print the secret itself. (Only whether each one is set at all, plus
# the environment name, which isn't sensitive on its own.)
print('client_id set:', bool(client_id), '| secret set:', bool(secret), '| env:', plaid_env)

# Note: unlike the real plaid_client.py, this always hard-codes Sandbox
# (test data) regardless of what PLAID_ENV actually says - another reason
# this file is a dead-end prototype, not something to build on.
configuration = plaid.Configuration(
    host=plaid.Environment.Sandbox,
    api_key={
        'clientId' : client_id,
        'secret' : secret,
    }
)

api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

# Create link token
