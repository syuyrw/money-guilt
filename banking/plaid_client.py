"""A thin wrapper around Plaid's own Python SDK.

Plaid is the third-party service that actually talks to banks. This file
hides the SDK's verbose request/response objects behind a few simple
methods (create_link_token, get_transactions, ...) that the rest of the app
calls instead.
"""
import json

import plaid
# The Plaid SDK models each kind of request as its own class - one import
# per request "shape" it knows how to build.
from plaid import ApiClient, Configuration, Environment
from plaid.apis import PlaidApi
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.products import Products
import os
from data import paths
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Reads PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV out of the private .env
# file into this process's environment variables, so os.getenv(...) below
# can find them.
paths.load_env()


# Plaid's answers that mean "there is nothing left to revoke".
ALREADY_GONE = {"ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"}


def error_code(exc):
    """The error_code Plaid put in an ApiException's body, or None."""
    try:
        # Plaid's errors carry their details as a JSON string in `.body`,
        # not as separate Python attributes - this parses that JSON and
        # pulls out just the one field this app cares about.
        return json.loads(exc.body).get("error_code")
    except (TypeError, ValueError, AttributeError):
        # Covers every way this could go wrong: body is None (TypeError),
        # body isn't valid JSON (ValueError), or exc isn't even the kind of
        # object with a .body at all (AttributeError). In every case, "no
        # error code could be read" is a reasonable answer, not a crash.
        return None


# Only these are accepted. "Production" means real bank data, so it has to be
# asked for by name: anything else, a typo included, is an error rather than
# quietly meaning production.
ENVIRONMENTS = {"sandbox": Environment.Sandbox, "production": Environment.Production}


def parse_environment(value):
    """The environment named by PLAID_ENV, or ValueError if it isn't recognised."""
    # `value if value is not None else "sandbox"` means: if PLAID_ENV was
    # never set at all, default to "sandbox". But if it WAS set - even to
    # something misspelled - that value is used as-is below, and checked
    # against the ENVIRONMENTS dict; a typo is never silently treated as
    # one of the two valid options.
    name = (value if value is not None else "sandbox").strip().lower()
    if name not in ENVIRONMENTS:
        raise ValueError(
            f"PLAID_ENV must be 'sandbox' or 'production', not {value!r}. "
            "Refusing to guess: a wrong guess could mean real bank data.")
    return name


class PlaidClient:
    def __init__(self):
        self.client_id = os.getenv("PLAID_CLIENT_ID")
        self.secret = os.getenv("PLAID_SECRET")
        self.plaid_env = parse_environment(os.getenv("PLAID_ENV"))

        # all([...]) is True only if every item in the list is truthy - so
        # this catches either credential being missing (None) or blank ("").
        if not all([self.client_id, self.secret]):
            raise ValueError("Missing Plaid credentials in .env")

        environment = ENVIRONMENTS[self.plaid_env]

        # Configuration bundles together which Plaid server to talk to
        # (sandbox vs. production have different URLs) and the credentials
        # to authenticate with.
        configuration = Configuration(
            host=environment,
            api_key={
                'clientId': self.client_id,
                'secret': self.secret,
            }
        )

        api_client = ApiClient(configuration)
        # self.client is the actual object every method below calls out to
        # Plaid's servers through.
        self.client = PlaidApi(api_client)
        if self.plaid_env == "production":
            logger.warning("Plaid client initialized in PRODUCTION: real bank data")
        else:
            logger.info("Plaid client initialized in sandbox (test data)")

    def create_link_token(self, user_id="user_id"):
        """Create a link token for Plaid Link flow"""
        try:
            # A "link token" is a short-lived, single-use token that tells
            # Plaid's Link widget (the hosted bank-login UI) which app is
            # asking, and for what ("transactions" data, for a US bank).
            # The browser never sees your actual Plaid secret - only this
            # token, which can't be used for anything else.
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
            raise  # re-raises the same exception after logging it

    def exchange_public_token(self, public_token):
        """Exchange public token for access token"""
        try:
            # After someone logs into their bank through Plaid Link, the
            # browser gets back a "public token" - safe to pass around, but
            # not usable to actually fetch data. This call trades it, on
            # the server side, for the real, long-lived "access token" that
            # can be used to pull transactions. That access token is what
            # secure_store.py then saves to the Keychain.
            request = ItemPublicTokenExchangeRequest(public_token=public_token)
            response = self.client.item_public_token_exchange(request)
            logger.info("Access token exchanged successfully")
            return response.access_token
        except Exception as e:
            logger.error(f"Error exchanging public token: {e}")
            raise

    def remove_item(self, access_token):
        """Revoke Money Guilt's access to the linked bank through Plaid.

        True once the connection is gone, including when Plaid says it already
        was (a token it no longer recognises has nothing left to revoke).
        Anything else raises, so the caller keeps the token and can retry.
        """
        try:
            self.client.item_remove(ItemRemoveRequest(access_token=access_token))
            logger.info("Bank connection removed")
            return True
        except plaid.ApiException as exc:
            if error_code(exc) in ALREADY_GONE:
                # The connection is already gone on Plaid's side (maybe a
                # previous attempt actually succeeded but the response got
                # lost) - from the caller's point of view, that's just as
                # good as this call having removed it just now.
                logger.info("Bank connection was already removed")
                return True
            logger.error(f"Error removing the bank connection: {error_code(exc) or exc.status}")
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
        # Default to the last 30 days if the caller didn't say otherwise.
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

            # Plaid doesn't return every transaction in one response if
            # there are a lot of them - it hands back one "page" at a time,
            # and tells you the total count (`total_transactions`) so you
            # know how many more pages to ask for. `offset` tells Plaid
            # "skip the ones I already have and give me the next batch".
            #
            # Handle pagination. A page that adds nothing would otherwise spin
            # forever on the same offset (this now runs unattended, once a
            # day, so a hang here would silently stop every sync after it).
            total_transactions = response.total_transactions
            while len(transactions) < total_transactions:
                before = len(transactions)
                request = TransactionsGetRequest(
                    access_token=access_token,
                    start_date=start_date,
                    end_date=end_date,
                    offset=before,
                )
                response = self.client.transactions_get(request)
                transactions.extend(response.transactions)
                # Defensive check: if a page came back empty, `transactions`
                # didn't grow, so without this the `while` condition above
                # would still be true next time around and it would just
                # ask for the exact same offset again, forever.
                if len(transactions) == before:
                    logger.warning(
                        "Plaid reported %d transactions but a page at offset "
                        "%d returned none; stopping with what was retrieved.",
                        total_transactions, before)
                    break

            logger.info(f"Retrieved {len(transactions)} transactions from {start_date} to {end_date}")
            return transactions
        except Exception as e:
            logger.error(f"Error getting transactions: {e}")
            raise
