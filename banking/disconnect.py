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

from data import categorizer
from data import database
from banking import secure_store

logger = logging.getLogger(__name__)

# Fixed statements rather than a table name filled into one, so nothing here is
# ever built from a variable. (Building SQL by inserting a variable into the
# string - even a table name - is exactly the kind of thing that leads to
# SQL-injection bugs; writing out each statement in full avoids the
# temptation entirely.)
ERASE_STATEMENTS = (
    ("transactions", "DELETE FROM transactions"),
    ("accounts", "DELETE FROM accounts"),
    ("categories", "DELETE FROM categories"),
)


def _make_client():
    # Imported here, not at the top of the file, mainly so tests can swap
    # in a fake PlaidClient by replacing this function, without ever
    # constructing (or needing credentials for) a real one.
    from banking import plaid_client
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
            # Raised by PlaidClient's constructor when credentials are
            # missing or malformed - there's nothing to even attempt here.
            return "unconfigured"

    try:
        client.remove_item(token)
    except Exception as exc:
        # The type only: a Plaid error can quote the request, token included.
        # Logging type(exc).__name__ (e.g. "ApiException") instead of the
        # exception itself means the token can never accidentally end up in
        # a log file.
        logger.warning("Could not disconnect the bank (%s)", type(exc).__name__)
        return "failed"

    # Only delete the local copy once Plaid has actually confirmed the
    # connection is revoked - see the module docstring above for why this
    # order matters.
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
        # Normally, SQLite's DELETE just marks a row's space as "free to
        # reuse later" - the actual bytes can still be read straight out of
        # the .db file until something else overwrites them. secure_delete
        # makes SQLite overwrite deleted data with zeros immediately instead.
        conn.execute("PRAGMA secure_delete = ON")
        for table, statement in ERASE_STATEMENTS:
            try:
                # cursor.rowcount (returned directly from execute() here) is
                # how many rows a DELETE/UPDATE actually affected.
                removed = conn.execute(statement).rowcount
            except sqlite3.OperationalError:     # table not created yet: nothing to erase
                continue
            if table in counts:
                counts[table] = max(removed, 0)
        conn.commit()
        # VACUUM rebuilds the database file from scratch, which both
        # shrinks it back down and ensures no fragment of the deleted rows'
        # old pages lingers anywhere in the file.
        conn.execute("VACUUM")

    # (get_categorizer or categorizer.get_categorizer)() means: use the
    # function passed in if there is one (tests do this to use a throwaway
    # categorizer), otherwise fall back to the app's real one. Either way,
    # it's then called to get the actual object, and .forget_all() wipes
    # everything it has learned about merchants.
    (get_categorizer or categorizer.get_categorizer)().forget_all()
    logger.info("Local data erased")
    return counts
