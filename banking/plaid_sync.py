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

# So this still works when run directly (python3 banking/plaid_sync.py),
# where Python would otherwise only put banking/ itself on sys.path, not
# the project root the `data`/`banking` packages below live under.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import database
from data import paths
from banking import secure_store

logger = logging.getLogger(__name__)

CONFIG_NAME = "plaid_sync.json"


def _config_path():
    return os.path.join(paths.data_dir(), CONFIG_NAME)


def load_state():
    """The small on-disk record of when the last successful sync happened.

    Returns {} if the file doesn't exist yet, is empty, or is corrupted in
    some way - never raises, since "we don't know of a sync yet" is always
    a safe thing to assume.
    """
    try:
        with open(_config_path()) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        # OSError: the file doesn't exist (first run) or can't be opened.
        # ValueError: json.load() raises this if the file's contents
        # aren't valid JSON (e.g. it got cut off mid-write somehow).
        state = {}
    return state if isinstance(state, dict) else {}


def save_state(state):
    # Writes to a temporary ".part" file first, then atomically renames it
    # into place with os.replace(). This matters because a crash or power
    # loss partway through writing the *real* file would leave a corrupted,
    # half-written plaid_sync.json behind; writing to a throwaway name
    # first means the real file is only ever fully-written-or-untouched.
    path = _config_path()
    partial = path + ".part"
    # os.open with these flags creates the file (or truncates it if it
    # somehow already existed) with permissions 0o600 (owner read/write
    # only) from the moment it's created - there's no window where the
    # file briefly exists with looser permissions for another process to
    # read.
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.replace(partial, path)


def today():
    """Today's date as a string like "2026-11-16"."""
    return date.today().isoformat()


def last_sync_date():
    return load_state().get("last_sync_date")


def has_synced_today(now=None):
    # `now` is only ever passed in by tests, to check against a date other
    # than the real "today" without having to wait for the calendar to
    # actually change.
    return last_sync_date() == (now or today())


def record_sync(day=None):
    # {**load_state(), "last_sync_date": ...} means: start from whatever is
    # already in the saved state (spreading its keys into a new dict with
    # **), then add or overwrite just the last_sync_date key. This means
    # any other keys this file might one day store survive a save here
    # unchanged.
    save_state({**load_state(), "last_sync_date": day or today()})


def _make_client():
    # Imported here (not at the top of the file) so tests can swap in a
    # fake PlaidClient by replacing this function, and so importing this
    # module at all doesn't require Plaid credentials to already be set up.
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
            # Raised by PlaidClient's constructor when credentials are
            # missing or malformed - nothing to even attempt in that case.
            return "unconfigured"

    try:
        accounts = client.get_accounts(token)
        transactions = client.get_transactions(token)
    except Exception as exc:
        # The type only: a Plaid error can quote the request, token included.
        logger.warning("Could not sync transactions (%s)", type(exc).__name__)
        # Deliberately returns without calling record_sync() - a failed
        # attempt must not be remembered as "done", so the very next
        # opportunity (next startup, or tomorrow's midnight run) retries it
        # instead of silently going a whole day with stale data.
        return "failed"

    database.save_accounts(accounts)
    database.save_transactions(transactions)
    record_sync()
    logger.info("Synced %d transaction(s) from Plaid", len(transactions))
    return "synced"


if __name__ == "__main__":
    # Lets this file be run directly from a terminal for debugging/manual
    # use, separately from the widget's own automatic midnight schedule.
    logging.basicConfig(level=logging.INFO)
    if "--status" in sys.argv:
        print("synced today" if has_synced_today() else "not yet synced today")
    else:
        print(sync_now(force="--force" in sys.argv))
