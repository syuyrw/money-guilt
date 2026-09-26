"""Tests for disconnect: revoking a bank connection and erasing local data.

Run with:  python -m unittest test_disconnect -v

Uses a throwaway database, throwaway merchant lessons and an in-memory
Keychain. The real Keychain, database and Plaid are never touched.
"""
import json
import os
import sys
import tempfile
import unittest

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

# The private data directory, redirected before anything reads it.
DATA_DIR = tempfile.mkdtemp(prefix="disconnect_test_")
os.environ["MONEY_GUILD_DATA_DIR"] = DATA_DIR

import categorizer  # noqa: E402
import database  # noqa: E402
import disconnect  # noqa: E402
import plaid  # noqa: E402
import secure_store  # noqa: E402

TOKEN = "access-sandbox-TEST-FIXTURE-NOT-A-REAL-TOKEN"
UNIQUE_MERCHANT = "Zanzibar Zephyr Zinnia Emporium"


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


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.removed = []

    def remove_item(self, token):
        self.removed.append(token)
        if self.error:
            raise self.error
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryKeyring()
        self._keyring = keyring.get_keyring()
        keyring.set_keyring(self.memory)
        os.environ.pop("PLAID_ACCESS_TOKEN", None)
        self._db = database.DATABASE_PATH
        self.db_dir = tempfile.mkdtemp(prefix="disconnect_db_")
        database.DATABASE_PATH = os.path.join(self.db_dir, "t.db")
        database.init_db()
        self._cat = categorizer._categorizer
        self.overrides = os.path.join(self.db_dir, "overrides.json")
        categorizer._categorizer = categorizer.TransactionCategorizer(
            overrides_file=self.overrides)

    def tearDown(self):
        keyring.set_keyring(self._keyring)
        database.DATABASE_PATH = self._db
        categorizer._categorizer = self._cat

    def link(self):
        secure_store.set_access_token(TOKEN)

    def token_is_saved(self):
        return self.memory.get_password(secure_store.SERVICE, secure_store.ACCOUNT) == TOKEN


class BankStatus(Base):
    def test_linked_and_not_linked(self):
        self.assertEqual(disconnect.bank_status(), "not_linked")
        self.link()
        self.assertEqual(disconnect.bank_status(), "linked")

    def test_an_unreadable_keychain_is_unknown_not_a_crash(self):
        from keyring.backends.fail import Keyring as NoKeyring
        keyring.set_keyring(NoKeyring())
        self.assertEqual(disconnect.bank_status(), "unknown")


class DisconnectBank(Base):
    def test_success_revokes_then_removes_the_token(self):
        self.link()
        client = FakeClient()
        self.assertEqual(disconnect.disconnect_bank(client), "disconnected")
        self.assertEqual(client.removed, [TOKEN], "Plaid is asked first, with that token")
        self.assertFalse(self.token_is_saved())
        self.assertEqual(disconnect.bank_status(), "not_linked")

    def test_nothing_linked_makes_no_call(self):
        client = FakeClient()
        self.assertEqual(disconnect.disconnect_bank(client), "not_linked")
        self.assertEqual(client.removed, [])

    def test_every_kind_of_failure_keeps_the_token(self):
        """Removing it after a failed revoke would leave access alive with no
        way left to revoke it."""
        errors = [OSError("no route"), TimeoutError("slow"),
                  plaid.ApiException(status=500, reason="server"),
                  RuntimeError("boom"), ValueError("odd")]
        for error in errors:
            self.link()
            client = FakeClient(error=error)
            self.assertEqual(disconnect.disconnect_bank(client), "failed", repr(error))
            self.assertEqual(len(client.removed), 1, "it did try")
            self.assertTrue(self.token_is_saved(), f"token lost after {error!r}")

    def test_a_retry_after_a_failure_works(self):
        self.link()
        self.assertEqual(disconnect.disconnect_bank(FakeClient(error=OSError())), "failed")
        self.assertEqual(disconnect.disconnect_bank(FakeClient()), "disconnected")
        self.assertFalse(self.token_is_saved())

    def test_missing_plaid_credentials_change_nothing(self):
        self.link()
        from unittest import mock
        with mock.patch.object(disconnect, "_make_client",
                               side_effect=ValueError("Missing Plaid credentials")):
            self.assertEqual(disconnect.disconnect_bank(), "unconfigured")
        self.assertTrue(self.token_is_saved())

    def test_an_unreadable_keychain_changes_nothing(self):
        from keyring.backends.fail import Keyring as NoKeyring
        keyring.set_keyring(NoKeyring())
        client = FakeClient()
        self.assertEqual(disconnect.disconnect_bank(client), "keychain")
        self.assertEqual(client.removed, [])

    def test_the_token_never_appears_in_the_log(self):
        self.link()
        with self.assertLogs("disconnect", level="DEBUG") as logs:
            disconnect.disconnect_bank(FakeClient(error=RuntimeError(TOKEN)))
            self.link()
            disconnect.disconnect_bank(FakeClient())
        text = "\n".join(logs.output)
        self.assertNotIn("TEST-FIXTURE", text)
        self.assertIn("RuntimeError", text, "the failure is still recorded, by type")

    def test_it_leaves_saved_transactions_alone(self):
        self.link()
        with database.get_db() as c:
            c.execute("INSERT INTO accounts (id, name) VALUES ('a1', 'Checking')")
            c.execute("INSERT INTO transactions (id, account_id, date, name, amount)"
                      " VALUES ('t1', 'a1', '2026-01-01', 'X', 1.0)")
            c.commit()
        disconnect.disconnect_bank(FakeClient())
        with database.get_db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)


class EraseLocalData(Base):
    def seed(self):
        with database.get_db() as c:
            c.execute("INSERT INTO accounts (id, name) VALUES ('a1', ?)", (UNIQUE_MERCHANT + " Card",))
            for i in range(5):
                c.execute("INSERT INTO transactions (id, account_id, date, name, amount)"
                          " VALUES (?, 'a1', '2026-01-01', ?, ?)",
                          (f"t{i}", UNIQUE_MERCHANT, 10.0 + i))
            c.execute("INSERT INTO categories (name) VALUES ('anything')")
            c.commit()
        categorizer.get_categorizer().learn_merchant_category(UNIQUE_MERCHANT, "shopping", True)

    def count(self, table):
        with database.get_db() as c:
            return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec B608 - fixed names in this test

    def test_every_row_is_deleted_and_counted(self):
        self.seed()
        counts = disconnect.erase_local_data()
        self.assertEqual(counts, {"transactions": 5, "accounts": 1})
        for table in ("transactions", "accounts", "categories"):
            self.assertEqual(self.count(table), 0, table)

    def test_the_erased_text_is_not_left_inside_the_database_file(self):
        """A plain DELETE only unlinks rows; the words stay in the file's pages."""
        self.seed()
        with open(database.DATABASE_PATH, "rb") as fh:
            self.assertIn(UNIQUE_MERCHANT.encode(), fh.read(), "the seed should be in the file")
        disconnect.erase_local_data()
        with open(database.DATABASE_PATH, "rb") as fh:
            self.assertNotIn(UNIQUE_MERCHANT.encode(), fh.read())

    def test_the_tables_survive_so_the_app_keeps_working(self):
        self.seed()
        disconnect.erase_local_data()
        with database.get_db() as c:
            c.execute("INSERT INTO accounts (id, name) VALUES ('new', 'Fresh')")
            c.commit()
        self.assertEqual(self.count("accounts"), 1)

    def test_merchant_lessons_are_forgotten_in_memory_and_on_disk(self):
        self.seed()
        cat = categorizer.get_categorizer()
        self.assertTrue(cat.merchant_overrides)
        disconnect.erase_local_data()
        self.assertEqual(cat.merchant_overrides, {})
        self.assertEqual(cat.merchant_wasteful, {})
        with open(self.overrides) as fh:
            saved = json.load(fh)
        self.assertEqual(saved, {"categories": {}, "wasteful": {}})
        self.assertNotIn(UNIQUE_MERCHANT.lower(), json.dumps(saved))

    def test_it_does_not_touch_the_bank_connection(self):
        self.seed()
        self.link()
        disconnect.erase_local_data()
        self.assertTrue(self.token_is_saved())

    def test_it_does_not_touch_sharing_settings(self):
        import telemetry
        telemetry.set_enabled(False)
        telemetry.save_config({**telemetry.load_config(), "install_id": "keep-me", "last_sent": [1, 1]})
        self.seed()
        disconnect.erase_local_data()
        config = telemetry.load_config()
        self.assertEqual(config.get("install_id"), "keep-me")
        self.assertFalse(telemetry.is_enabled())

    def test_it_is_safe_to_run_twice_or_on_an_empty_database(self):
        self.assertEqual(disconnect.erase_local_data(), {"transactions": 0, "accounts": 0})
        self.seed()
        disconnect.erase_local_data()
        self.assertEqual(disconnect.erase_local_data(), {"transactions": 0, "accounts": 0})

    def test_missing_tables_are_not_a_crash(self):
        database.DATABASE_PATH = os.path.join(self.db_dir, "bare.db")   # never initialised
        self.assertEqual(disconnect.erase_local_data(), {"transactions": 0, "accounts": 0})

    def test_no_side_files_are_left_behind(self):
        self.seed()
        disconnect.erase_local_data()
        leftovers = [n for n in os.listdir(self.db_dir) if n.startswith("t.db-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
