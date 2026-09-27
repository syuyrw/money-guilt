"""Tests for plaid_sync: pulling transactions from Plaid once a day.

Run with:  python -m unittest test_plaid_sync -v

Uses a throwaway database and an in-memory Keychain. The real Keychain,
database and Plaid are never touched.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

# The private data directory, redirected before anything reads it.
DATA_DIR = tempfile.mkdtemp(prefix="plaid_sync_test_")
os.environ["MONEY_GUILD_DATA_DIR"] = DATA_DIR

import database  # noqa: E402
import plaid_sync  # noqa: E402
import secure_store  # noqa: E402

TOKEN = "access-sandbox-TEST-FIXTURE-NOT-A-REAL-TOKEN"


class Account:
    def __init__(self, account_id="a1"):
        self.account_id = account_id
        self.name = "Checking"
        self.type = None
        self.subtype = None

        class _Balances:
            current = 100.0
        self.balances = _Balances()


class Transaction:
    def __init__(self, transaction_id="t1"):
        self.transaction_id = transaction_id
        self.account_id = "a1"
        self.date = "2026-01-01"
        self.name = "Widget Store"
        self.amount = 12.34
        self.personal_finance_category = None
        self.datetime = None
        self.authorized_datetime = None


class FakeClient:
    def __init__(self, error=None, accounts=None, transactions=None):
        self.error = error
        self.calls = []
        self.accounts = accounts if accounts is not None else [Account()]
        self.transactions = transactions if transactions is not None else [Transaction()]

    def get_accounts(self, token):
        self.calls.append(("accounts", token))
        if self.error:
            raise self.error
        return self.accounts

    def get_transactions(self, token):
        self.calls.append(("transactions", token))
        if self.error:
            raise self.error
        return self.transactions


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.data:
            raise PasswordDeleteError()
        del self.data[(service, username)]


class Base(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryKeyring()
        self._keyring = keyring.get_keyring()
        keyring.set_keyring(self.memory)
        self._db = database.DATABASE_PATH
        self.db_dir = tempfile.mkdtemp(prefix="plaid_sync_db_")
        database.DATABASE_PATH = os.path.join(self.db_dir, "t.db")
        database.init_db()
        self._config = plaid_sync._config_path()

    def tearDown(self):
        keyring.set_keyring(self._keyring)
        database.DATABASE_PATH = self._db
        try:
            os.remove(plaid_sync._config_path())
        except OSError:
            pass

    def link(self):
        secure_store.set_access_token(TOKEN)


class HasSyncedToday(Base):
    def test_false_until_recorded(self):
        self.assertFalse(plaid_sync.has_synced_today())
        plaid_sync.record_sync()
        self.assertTrue(plaid_sync.has_synced_today())

    def test_a_different_day_is_not_today(self):
        plaid_sync.record_sync("2000-01-01")
        self.assertFalse(plaid_sync.has_synced_today())

    def test_survives_a_fresh_read_from_disk(self):
        """A restart re-reads the file rather than trusting anything in memory."""
        plaid_sync.record_sync()
        self.assertEqual(plaid_sync.load_state(), {"last_sync_date": plaid_sync.today()})


class SyncNow(Base):
    def test_success_saves_accounts_and_transactions_and_records_the_day(self):
        self.link()
        client = FakeClient()
        self.assertEqual(plaid_sync.sync_now(client), "synced")
        self.assertEqual(client.calls, [("accounts", TOKEN), ("transactions", TOKEN)])
        self.assertEqual(len(database.get_accounts()), 1)
        self.assertEqual(len(database.get_all_transactions(days=99999)), 1)
        self.assertTrue(plaid_sync.has_synced_today())

    def test_a_second_call_the_same_day_does_nothing(self):
        self.link()
        client = FakeClient()
        self.assertEqual(plaid_sync.sync_now(client), "synced")
        self.assertEqual(plaid_sync.sync_now(client), "already_synced_today")
        self.assertEqual(len(client.calls), 2, "no second round of Plaid calls")

    def test_force_bypasses_the_once_a_day_check(self):
        self.link()
        client = FakeClient()
        plaid_sync.sync_now(client)
        self.assertEqual(plaid_sync.sync_now(client, force=True), "synced")
        self.assertEqual(len(client.calls), 4)

    def test_nothing_linked_makes_no_call(self):
        client = FakeClient()
        self.assertEqual(plaid_sync.sync_now(client), "not_linked")
        self.assertEqual(client.calls, [])
        self.assertFalse(plaid_sync.has_synced_today())

    def test_a_failure_is_not_recorded_so_the_next_attempt_retries(self):
        self.link()
        client = FakeClient(error=OSError("no route"))
        self.assertEqual(plaid_sync.sync_now(client), "failed")
        self.assertFalse(plaid_sync.has_synced_today())
        self.assertEqual(len(database.get_all_transactions(days=99999)), 0)

    def test_a_retry_after_a_failure_works(self):
        self.link()
        self.assertEqual(plaid_sync.sync_now(FakeClient(error=OSError())), "failed")
        self.assertEqual(plaid_sync.sync_now(FakeClient()), "synced")

    def test_missing_plaid_credentials_change_nothing(self):
        self.link()
        with mock.patch.object(plaid_sync, "_make_client",
                               side_effect=ValueError("Missing Plaid credentials")):
            self.assertEqual(plaid_sync.sync_now(), "unconfigured")
        self.assertFalse(plaid_sync.has_synced_today())

    def test_an_unreadable_keychain_changes_nothing(self):
        from keyring.backends.fail import Keyring as NoKeyring
        keyring.set_keyring(NoKeyring())
        client = FakeClient()
        self.assertEqual(plaid_sync.sync_now(client), "keychain")
        self.assertEqual(client.calls, [])

    def test_the_token_never_appears_in_the_log(self):
        self.link()
        with self.assertLogs("plaid_sync", level="DEBUG") as logs:
            plaid_sync.sync_now(FakeClient(error=RuntimeError(TOKEN)))
        self.assertNotIn("TEST-FIXTURE", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
