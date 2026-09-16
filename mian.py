import plaid
from plaid.api import plaid_api
import os
from dotenv import load_dotenv

# Import credentials from .env
load_dotenv()

client_id = os.getenv("PLAID_CLIENT_ID")
secret = os.getenv("PLAID_SECRET")
plaid_env = os.getenv("PLAID_ENV")

print(client_id, secret, plaid_env)

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

