"""Tests for secure_store: keychain storage, migration and file permissions.

Run with:  python -m unittest test_secure_store -v

Uses an in-memory keychain and temp files; the real Keychain and the real
project files are never touched.
"""
import os
import stat
import sys
import tempfile
import unittest

import keyring
from keyring.backend import KeyringBackend
from keyring.backends.fail import Keyring as NoKeyring
from keyring.errors import PasswordDeleteError

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

import secure_store  # noqa: E402

TOKEN = 'access-sandbox-TEST-FIXTURE-NOT-A-REAL-TOKEN'


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


class DroppingKeyring(MemoryKeyring):
    """Accepts writes and silently forgets them, like a broken backend."""

    def set_password(self, service, username, password):
        pass


class SecureStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='secure_store_')
        self.legacy = os.path.join(self.tmp, 'access_token.txt')
        self.memory = MemoryKeyring()
        self._orig = (keyring.get_keyring(), secure_store.LEGACY_TOKEN_FILE)
        keyring.set_keyring(self.memory)
        secure_store.LEGACY_TOKEN_FILE = self.legacy
        os.environ.pop('PLAID_ACCESS_TOKEN', None)

    def tearDown(self):
        keyring.set_keyring(self._orig[0])
        secure_store.LEGACY_TOKEN_FILE = self._orig[1]
        os.environ.pop('PLAID_ACCESS_TOKEN', None)

    def write_legacy(self, text=TOKEN):
        with open(self.legacy, 'w') as fh:
            fh.write(text)

    # ---- keychain storage
    def test_round_trip(self):
        secure_store.set_access_token(TOKEN)
        self.assertEqual(secure_store.get_access_token(), TOKEN)

    def test_nothing_stored_returns_none(self):
        self.assertIsNone(secure_store.get_access_token())

    def test_storing_writes_no_file(self):
        secure_store.set_access_token(TOKEN)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_rejects_empty_and_non_string_tokens(self):
        for bad in ('', '   ', None, 123, b'bytes'):
            with self.assertRaises(secure_store.SecureStoreError, msg=repr(bad)):
                secure_store.set_access_token(bad)
        self.assertEqual(self.memory.data, {})

    def test_delete(self):
        secure_store.set_access_token(TOKEN)
        secure_store.delete_access_token()
        self.assertIsNone(secure_store.get_access_token())
        secure_store.delete_access_token()  # deleting again is harmless

    def test_environment_variable_overrides(self):
        secure_store.set_access_token(TOKEN)
        os.environ['PLAID_ACCESS_TOKEN'] = 'access-sandbox-from-env'
        self.assertEqual(secure_store.get_access_token(), 'access-sandbox-from-env')

    def test_no_keychain_raises_instead_of_falling_back_to_a_file(self):
        keyring.set_keyring(NoKeyring())
        with self.assertRaises(secure_store.SecureStoreError):
            secure_store.set_access_token(TOKEN)
        with self.assertRaises(secure_store.SecureStoreError):
            secure_store.get_access_token()
        self.assertEqual(os.listdir(self.tmp), [], 'no plaintext fallback')

    # ---- migration from access_token.txt
    def test_legacy_file_is_migrated_and_removed(self):
        self.write_legacy(TOKEN + '\n')
        self.assertEqual(secure_store.get_access_token(), TOKEN)
        self.assertFalse(os.path.exists(self.legacy), 'the plaintext file must go')
        self.assertEqual(keyring.get_password(secure_store.SERVICE,
                                              secure_store.ACCOUNT), TOKEN)

    def test_migration_happens_once_and_later_reads_use_the_keychain(self):
        self.write_legacy()
        secure_store.get_access_token()
        self.write_legacy('access-sandbox-someone-elses')  # must not be re-read
        self.assertEqual(secure_store.get_access_token(), TOKEN)

    def test_a_failed_write_never_deletes_the_file(self):
        keyring.set_keyring(DroppingKeyring())
        self.write_legacy()
        with self.assertRaises(secure_store.SecureStoreError):
            secure_store.get_access_token()
        self.assertTrue(os.path.exists(self.legacy), 'token must not be lost')
        with open(self.legacy) as fh:
            self.assertEqual(fh.read(), TOKEN)

    def test_no_legacy_file_and_empty_file(self):
        self.assertIsNone(secure_store.migrate_legacy_file())
        self.write_legacy('  \n')
        self.assertIsNone(secure_store.migrate_legacy_file())

    # ---- masking
    def test_mask_never_returns_the_whole_token(self):
        shown = secure_store.mask(TOKEN)
        self.assertNotEqual(shown, TOKEN)
        self.assertNotIn(TOKEN[16:], shown)
        self.assertEqual(secure_store.mask(None), '(none)')

    # ---- file permissions
    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_files_become_owner_only(self):
        paths = []
        for name in ('.env', 'money_guilt.db', 'merchant_overrides.json'):
            path = os.path.join(self.tmp, name)
            with open(path, 'w') as fh:
                fh.write('x')
            os.chmod(path, 0o644)
            paths.append(path)
        changed = secure_store.harden_data_files(paths)
        self.assertEqual(changed, paths)
        for path in paths:
            self.assertEqual(self.mode(path), 0o600, path)

    def test_hardening_is_idempotent_and_reports_only_changes(self):
        path = os.path.join(self.tmp, '.env')
        with open(path, 'w') as fh:
            fh.write('x')
        os.chmod(path, 0o644)
        self.assertEqual(secure_store.harden_data_files([path]), [path])
        self.assertEqual(secure_store.harden_data_files([path]), [])

    def test_missing_files_are_ignored(self):
        self.assertEqual(secure_store.harden_data_files(
            [os.path.join(self.tmp, 'absent.db')]), [])

    def test_a_looser_group_or_other_bit_is_removed_even_if_owner_is_fine(self):
        path = os.path.join(self.tmp, 'x.db')
        with open(path, 'w') as fh:
            fh.write('x')
        os.chmod(path, 0o604)
        secure_store.harden_file(path)
        self.assertEqual(self.mode(path), 0o600)


if __name__ == '__main__':
    unittest.main()
