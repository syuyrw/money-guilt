"""Disconnecting a bank, and erasing what was saved from it.

Two separate acts, because they are different promises:

  disconnect_bank()    asks Plaid to revoke Money Guilt's access, then removes the
                       saved token. Transactions already saved stay on this Mac.
  erase_local_data()   deletes every saved transaction and account and every
                       merchant lesson. It does not touch the bank connection,
                       and it does not remove anything already shared (that is
                       telemetry.delete_reported_data).

The token is only removed after Plaid has confirmed it: deleting it first and
failing to reach Plaid would leave access alive with no way left to revoke it.
"""
import logging
import sqlite3

import categorizer
import database
import secure_store

logger = logging.getLogger(__name__)

# Fixed statements rather than a table name filled into one, so nothing here is
# ever built from a variable.
ERASE_STATEMENTS = (
    ("transactions", "DELETE FROM transactions"),
    ("accounts", "DELETE FROM accounts"),
    ("categories", "DELETE FROM categories"),
)


def _make_client():
    import plaid_client
    return plaid_client.PlaidClient()


def bank_status(store=None):
    """'linked', 'not_linked', or 'unknown' if the Keychain can't be read."""
    store = store or secure_store       # looked up now, so tests can substitute it
    try:
        token = store.get_access_token()
    except store.SecureStoreError:
        return "unknown"
    return "linked" if token else "not_linked"


def disconnect_bank(client=None, store=None):
    """Revoke Plaid's access, then forget the token.

    Returns "disconnected", "not_linked", "unconfigured" (Plaid credentials
    missing or invalid, so nothing was tried), "keychain" (the token couldn't be
    read), or "failed" (Plaid couldn't be reached or refused). In every case
    except "disconnected" the token is left exactly as it was.
    """
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
        client.remove_item(token)
    except Exception as exc:
        # The type only: a Plaid error can quote the request, token included.
        logger.warning("Could not disconnect the bank (%s)", type(exc).__name__)
        return "failed"

    store.delete_access_token()
    logger.info("Bank disconnected and token removed")
    return "disconnected"


def erase_local_data(get_categorizer=None):
    """Delete every saved transaction, account and merchant lesson.

    Deleted pages are overwritten (secure_delete) and the file is compacted
    (VACUUM), so the erased text isn't left behind inside the database file.
    Returns how many transactions and accounts were removed.
    """
    counts = {"transactions": 0, "accounts": 0}
    with database.get_db() as conn:
        conn.execute("PRAGMA secure_delete = ON")
        for table, statement in ERASE_STATEMENTS:
            try:
                removed = conn.execute(statement).rowcount
            except sqlite3.OperationalError:     # table not created yet: nothing to erase
                continue
            if table in counts:
                counts[table] = max(removed, 0)
        conn.commit()
        conn.execute("VACUUM")

    (get_categorizer or categorizer.get_categorizer)().forget_all()
    logger.info("Local data erased")
    return counts
