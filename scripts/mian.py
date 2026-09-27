import plaid
from plaid.api import plaid_api
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import paths

# Import credentials from the private data directory
paths.load_env()

client_id = os.getenv("PLAID_CLIENT_ID")
secret = os.getenv("PLAID_SECRET")
plaid_env = os.getenv("PLAID_ENV")

# Never print the secret itself.
print('client_id set:', bool(client_id), '| secret set:', bool(secret), '| env:', plaid_env)

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

