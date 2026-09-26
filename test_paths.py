"""Tests for paths: the private data directory and the migration into it.

Run with:  python -m unittest test_paths -v

Everything happens in temp directories. The real Application Support folder
and the real project files are never read or moved.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

import paths  # noqa: E402


def write(path, data=b'x'):
    mode = 'wb' if isinstance(data, bytes) else 'w'
    with open(path, mode) as fh:
        fh.write(data)


def read(path):
    with open(path, 'rb') as fh:
        return fh.read()


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class DataDirectory(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='paths_test_')
        self.data = os.path.join(self.root, 'nested', 'MoneyGuilt')
        self._env = mock.patch.dict(os.environ, {'MONEY_GUILD_DATA_DIR': self.data})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_created_owner_only_including_parents(self):
        result = paths.data_dir()
        self.assertEqual(result, self.data)
        self.assertTrue(os.path.isdir(self.data))
        self.assertEqual(mode(self.data), 0o700)

    def test_a_loose_existing_directory_is_tightened(self):
        os.makedirs(self.data)
        os.chmod(self.data, 0o755)
        paths.data_dir()
        self.assertEqual(mode(self.data), 0o700)

    def test_file_paths_live_inside_it(self):
        for fn, name in [(paths.db_path, paths.DB_NAME),
                         (paths.overrides_path, paths.OVERRIDES_NAME),
                         (paths.env_path, paths.ENV_NAME)]:
            self.assertEqual(fn(), os.path.join(self.data, name))

    def test_private_paths_cover_every_private_file(self):
        self.assertEqual([os.path.basename(p) for p in paths.private_paths()],
                         list(paths.PRIVATE_FILES))


class DefaultLocation(unittest.TestCase):
    def test_default_is_not_in_a_folder_icloud_can_sync(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MONEY_GUILD_DATA_DIR', None)
            default = os.path.expanduser("~/Library/Application Support/MoneyGuilt")
        for synced in ('~/Documents', '~/Desktop'):
            self.assertFalse(default.startswith(os.path.expanduser(synced)), synced)
        self.assertNotIn(PROJECT, default)

    def test_other_modules_default_into_the_data_directory(self):
        import database
        import categorizer
        self.assertFalse(database.DATABASE_PATH.startswith(PROJECT + os.sep))
        self.assertTrue(os.path.isabs(database.DATABASE_PATH))
        self.assertTrue(database.DATABASE_PATH.endswith(paths.DB_NAME))
        cat = categorizer.TransactionCategorizer()
        self.assertFalse(cat.overrides_file.startswith(PROJECT + os.sep))
        self.assertTrue(os.path.isabs(cat.overrides_file))


class Migration(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='paths_mig_')
        self.src = os.path.join(self.root, 'project')
        self.dst = os.path.join(self.root, 'private')
        os.makedirs(self.src)
        os.makedirs(self.dst)
        self.payload = {
            paths.ENV_NAME: b'PLAID_SECRET=abc\n',
            paths.DB_NAME: bytes(range(256)) * 40,      # binary, like a database
            paths.OVERRIDES_NAME: b'{"categories": {}, "wasteful": {}}',
        }
        for name, data in self.payload.items():
            write(os.path.join(self.src, name), data)
            os.chmod(os.path.join(self.src, name), 0o644)

    def run_migration(self):
        return paths.migrate_private_files(self.src, self.dst)

    def test_moves_every_file_byte_for_byte(self):
        report = self.run_migration()
        for name, data in self.payload.items():
            self.assertEqual(report[name], 'moved', name)
            self.assertEqual(read(os.path.join(self.dst, name)), data, name)
            self.assertFalse(os.path.exists(os.path.join(self.src, name)),
                             f'{name} must be gone from the project folder')

    def test_moved_files_are_owner_only(self):
        self.run_migration()
        for name in self.payload:
            self.assertEqual(mode(os.path.join(self.dst, name)), 0o600, name)

    def test_leaves_no_partial_files(self):
        self.run_migration()
        self.assertEqual(sorted(os.listdir(self.dst)), sorted(self.payload))

    def test_running_twice_is_harmless(self):
        self.run_migration()
        again = self.run_migration()
        self.assertTrue(all(v == 'not in the project folder' for v in again.values()))
        for name, data in self.payload.items():
            self.assertEqual(read(os.path.join(self.dst, name)), data)

    def test_missing_files_are_reported_not_errors(self):
        os.remove(os.path.join(self.src, paths.ENV_NAME))
        report = self.run_migration()
        self.assertEqual(report[paths.ENV_NAME], 'not in the project folder')
        self.assertEqual(report[paths.DB_NAME], 'moved')

    def test_an_identical_copy_already_there_just_drops_the_original(self):
        write(os.path.join(self.dst, paths.ENV_NAME), self.payload[paths.ENV_NAME])
        report = self.run_migration()
        self.assertIn('already there', report[paths.ENV_NAME])
        self.assertFalse(os.path.exists(os.path.join(self.src, paths.ENV_NAME)))

    def test_a_different_file_at_the_destination_is_never_overwritten(self):
        existing = os.path.join(self.dst, paths.DB_NAME)
        write(existing, b'newer data the user has since built up')
        report = self.run_migration()
        self.assertIn('CONFLICT', report[paths.DB_NAME])
        self.assertEqual(read(existing), b'newer data the user has since built up')
        self.assertEqual(read(os.path.join(self.src, paths.DB_NAME)),
                         self.payload[paths.DB_NAME], 'the original must survive')

    def test_a_database_with_a_journal_file_is_not_moved(self):
        for suffix in paths.DB_SIDE_FILES:
            journal = os.path.join(self.src, paths.DB_NAME + suffix)
            write(journal)
            report = self.run_migration()
            self.assertIn('in use', report[paths.DB_NAME], suffix)
            self.assertTrue(os.path.exists(os.path.join(self.src, paths.DB_NAME)))
            self.assertFalse(os.path.exists(os.path.join(self.dst, paths.DB_NAME)))
            os.remove(journal)

    def test_a_bad_copy_keeps_the_original_and_cleans_up(self):
        def corrupt_copy(src, dst):
            write(dst, b'garbage')
        with mock.patch.object(paths.shutil, 'copyfile', corrupt_copy):
            report = self.run_migration()
        for name, data in self.payload.items():
            self.assertIn('FAILED', report[name])
            self.assertEqual(read(os.path.join(self.src, name)), data,
                             f'{name} must not be lost')
        self.assertEqual(os.listdir(self.dst), [], 'no partial files left behind')


class LoadEnv(unittest.TestCase):
    KEY = 'MG_TEST_SECRET_VALUE'

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='paths_env_')
        self.data = os.path.join(self.root, 'data')
        self.project = os.path.join(self.root, 'project')
        os.makedirs(self.project)
        self._patches = [
            mock.patch.dict(os.environ, {'MONEY_GUILD_DATA_DIR': self.data}),
            mock.patch.object(paths, 'PROJECT_DIR', self.project),
        ]
        for p in self._patches:
            p.start()
        os.environ.pop(self.KEY, None)

    def tearDown(self):
        os.environ.pop(self.KEY, None)
        for p in self._patches:
            p.stop()

    def test_reads_from_the_data_directory(self):
        os.makedirs(self.data, exist_ok=True)
        write(os.path.join(self.data, '.env'), f'{self.KEY}=from-data\n')
        self.assertEqual(paths.load_env(), os.path.join(self.data, '.env'))
        self.assertEqual(os.environ[self.KEY], 'from-data')

    def test_data_directory_wins_over_a_leftover_project_copy(self):
        os.makedirs(self.data, exist_ok=True)
        write(os.path.join(self.data, '.env'), f'{self.KEY}=from-data\n')
        write(os.path.join(self.project, '.env'), f'{self.KEY}=from-project\n')
        paths.load_env()
        self.assertEqual(os.environ[self.KEY], 'from-data')

    def test_falls_back_to_the_project_copy_with_a_warning(self):
        write(os.path.join(self.project, '.env'), f'{self.KEY}=from-project\n')
        with mock.patch('sys.stderr') as err:
            found = paths.load_env()
        self.assertEqual(found, os.path.join(self.project, '.env'))
        self.assertEqual(os.environ[self.KEY], 'from-project')
        self.assertTrue(err.write.called, 'the fallback must say why it is unsafe')

    def test_nothing_anywhere_returns_none(self):
        self.assertIsNone(paths.load_env())


class PrototypeScript(unittest.TestCase):
    def test_mian_does_not_print_the_secret(self):
        """Regression: mian.py printed client_id, secret and env to the terminal."""
        data = tempfile.mkdtemp(prefix='paths_mian_')
        write(os.path.join(data, '.env'),
              'PLAID_CLIENT_ID=cid\nPLAID_SECRET=TOPSECRETMARKER123\nPLAID_ENV=sandbox\n')
        env = dict(os.environ, MONEY_GUILD_DATA_DIR=data)
        for k in ('PLAID_SECRET', 'PLAID_CLIENT_ID', 'PLAID_ENV'):
            env.pop(k, None)
        out = subprocess.run([sys.executable, os.path.join(PROJECT, 'mian.py')],
                             cwd=PROJECT, env=env, capture_output=True, text=True,
                             timeout=60)
        self.assertNotIn('TOPSECRETMARKER123', out.stdout + out.stderr)
        self.assertIn('secret set: True', out.stdout)


if __name__ == '__main__':
    unittest.main()
