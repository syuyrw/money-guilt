from plaid import ApiClient, Configuration, Environment
from plaid.apis import PlaidApi
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.products import Products
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()


class PlaidClient:
    def __init__(self):
        self.client_id = os.getenv("PLAID_CLIENT_ID")
        self.secret = os.getenv("PLAID_SECRET")
        self.plaid_env = os.getenv("PLAID_ENV", "sandbox")

        if not all([self.client_id, self.secret]):
            raise ValueError("Missing Plaid credentials in .env")

        environment = Environment.Sandbox if self.plaid_env == "sandbox" else Environment.Production

        configuration = Configuration(
            host=environment,
            api_key={
                'clientId': self.client_id,
                'secret': self.secret,
            }
        )

        api_client = ApiClient(configuration)
        self.client = PlaidApi(api_client)
        logger.info(f"Plaid client initialized in {self.plaid_env} environment")

    def create_link_token(self, user_id="user_id"):
        """Create a link token for Plaid Link flow"""
        try:
            request = LinkTokenCreateRequest(
                user=LinkTokenCreateRequestUser(
                    client_user_id=user_id
                ),
                client_name="Money Guilt",
                products=[Products("transactions")],
                country_codes=[CountryCode("US")],
                language="en",
            )
            response = self.client.link_token_create(request)
            logger.info("Link token created successfully")
            return response.link_token
        except Exception as e:
            logger.error(f"Error creating link token: {e}")
            raise

    def exchange_public_token(self, public_token):
        """Exchange public token for access token"""
        try:
            request = ItemPublicTokenExchangeRequest(public_token=public_token)
            response = self.client.item_public_token_exchange(request)
            logger.info("Access token exchanged successfully")
            return response.access_token
        except Exception as e:
            logger.error(f"Error exchanging public token: {e}")
            raise

    def get_accounts(self, access_token):
        """Get all accounts for the linked item"""
        try:
            request = AccountsGetRequest(access_token=access_token)
            response = self.client.accounts_get(request)
            logger.info(f"Retrieved {len(response.accounts)} accounts")
            return response.accounts
        except Exception as e:
            logger.error(f"Error getting accounts: {e}")
            raise

    def get_transactions(self, access_token, start_date=None, end_date=None):
        """Fetch transactions for a date range"""
        if not end_date:
            end_date = datetime.now().date()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        try:
            request = TransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
            )
            response = self.client.transactions_get(request)
            transactions = response.transactions

            # Handle pagination
            total_transactions = response.total_transactions
            while len(transactions) < total_transactions:
                request = TransactionsGetRequest(
                    access_token=access_token,
                    start_date=start_date,
                    end_date=end_date,
                    offset=len(transactions),
                )
                response = self.client.transactions_get(request)
                transactions.extend(response.transactions)

            logger.info(f"Retrieved {len(transactions)} transactions from {start_date} to {end_date}")
            return transactions
        except Exception as e:
            logger.error(f"Error getting transactions: {e}")
            raise
