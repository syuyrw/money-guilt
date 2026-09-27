"""Pulling transactions from Plaid, once a day.

Real bank data doesn't need to be fetched more than once a day, and every call
counts against Plaid's limits, so the widget only ever pulls once per calendar
day (local time), no matter how often it's asked. The date of the last
successful pull is written to disk (plaid_sync.json in the private data
directory), so this survives a restart: closing and reopening the widget
doesn't trigger a second pull for the same day, and a day the widget wasn't
running at midnight is caught up the next time it starts, instead of being
skipped.

    python3 banking/plaid_sync.py            sync now (what the widget does at midnight)
    python3 banking/plaid_sync.py --status   show whether today's pull has happened
    python3 banking/plaid_sync.py --force    sync even if today's pull already happened
"""
import json
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import database
from data import paths
from banking import secure_store

logger = logging.getLogger(__name__)

CONFIG_NAME = "plaid_sync.json"


def _config_path():
    return os.path.join(paths.data_dir(), CONFIG_NAME)


def load_state():
    try:
        with open(_config_path()) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    return state if isinstance(state, dict) else {}


def save_state(state):
    path = _config_path()
    partial = path + ".part"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.replace(partial, path)


def today():
    return date.today().isoformat()


def last_sync_date():
    return load_state().get("last_sync_date")


def has_synced_today(now=None):
    return last_sync_date() == (now or today())


def record_sync(day=None):
    save_state({**load_state(), "last_sync_date": day or today()})


def _make_client():
    from banking import plaid_client
    return plaid_client.PlaidClient()


def sync_now(client=None, store=None, force=False):
    """Pull today's accounts and transactions from Plaid, once a day.

    A day that already had a successful pull is a no-op unless force=True.
    Returns "synced", "already_synced_today", "not_linked", "unconfigured"
    (Plaid credentials missing or invalid, so nothing was tried), "keychain"
    (the token couldn't be read), or "failed" (Plaid couldn't be reached or
    refused, so nothing is marked as synced and the next attempt will retry).
    """
    if not force and has_synced_today():
        return "already_synced_today"

    store = store or secure_store
    try:
        token = store.get_access_token()
    except store.SecureStoreError:
        return "keychain"
    if not token:
        return "not_linked"

    if client is None:
        try:
            client = _make_client()
        except ValueError:
            return "unconfigured"

    try:
        accounts = client.get_accounts(token)
        transactions = client.get_transactions(token)
    except Exception as exc:
        # The type only: a Plaid error can quote the request, token included.
        logger.warning("Could not sync transactions (%s)", type(exc).__name__)
        return "failed"

    database.save_accounts(accounts)
    database.save_transactions(transactions)
    record_sync()
    logger.info("Synced %d transaction(s) from Plaid", len(transactions))
    return "synced"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if "--status" in sys.argv:
        print("synced today" if has_synced_today() else "not yet synced today")
    else:
        print(sync_now(force="--force" in sys.argv))
