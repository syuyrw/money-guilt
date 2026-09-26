"""Tests for check_private_data and the git hooks that run it.

Run with:  python -m unittest test_private_data -v

Everything happens in throwaway repositories and a throwaway database. Your
real repository, database and Keychain are never read.
"""
import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

import check_private_data as cpd  # noqa: E402

TX_ID = "kBq7nXe3WQfVZ4mrPLdA9hUTYo1sJg"      # shaped like a Plaid transaction ID
ACCT_ID = "Zr5VwLxP2eNqMbK8dYtGcA7HjSoU4f"
TOKEN = "access-sandbox-TEST-FIXTURE-NOT-A-REAL-TOKEN"
HOOKS = os.path.join(PROJECT, "hooks")

GIT_ENV = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PRIVATE_DATA_NO_KEYCHAIN": "1"}


def git(repo, *args, check=True, env=None):
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                            text=True, env=env or GIT_ENV)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args}: {result.stderr}")
    return result


def make_database(directory):
    path = os.path.join(directory, "test.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE transactions (id TEXT PRIMARY KEY, account_id TEXT,"
                 " name TEXT, amount REAL)")
    conn.execute("INSERT INTO accounts VALUES (?, 'Checking')", (ACCT_ID,))
    conn.execute("INSERT INTO transactions VALUES (?, ?, 'Blue Heron Bakery', 18.75)",
                 (TX_ID, ACCT_ID))
    conn.execute("INSERT INTO transactions VALUES ('short', ?, 'Cafe', 5.00)", (ACCT_ID,))
    conn.commit()
    conn.close()
    return path


def make_repo(directory):
    repo = os.path.join(directory, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "master")
    return repo


def write(repo, name, text):
    full = os.path.join(repo, name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(text)


class ReferenceData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = make_database(self.tmp)

    def test_loads_transaction_and_account_ids(self):
        ref = cpd.Reference.load(self.db, use_keychain=False)
        self.assertEqual(ref.ids, {TX_ID, ACCT_ID})

    def test_ignores_values_too_short_to_be_an_id(self):
        ref = cpd.Reference.load(self.db, use_keychain=False)
        self.assertNotIn("short", ref.ids)

    def test_loads_merchant_and_amount_pairs(self):
        ref = cpd.Reference.load(self.db, use_keychain=False)
        self.assertEqual(ref.pairs["blue heron bakery"], {"18.75"})

    def test_a_missing_database_is_not_an_error(self):
        ref = cpd.Reference.load(os.path.join(self.tmp, "absent.db"), use_keychain=False)
        self.assertEqual((ref.ids, ref.pairs), (set(), {}))

    def test_a_database_without_the_tables_is_not_an_error(self):
        empty = os.path.join(self.tmp, "empty.db")
        sqlite3.connect(empty).close()
        ref = cpd.Reference.load(empty, use_keychain=False)
        self.assertEqual(ref.ids, set())

    def test_it_only_ever_reads_the_database(self):
        def contents():
            with open(self.db, "rb") as fh:
                return fh.read()
        before = contents()
        cpd.Reference.load(self.db, use_keychain=False)
        self.assertEqual(contents(), before)


class Recognising(unittest.TestCase):
    def setUp(self):
        self.ref = cpd.Reference({TX_ID, ACCT_ID}, TOKEN,
                                 {"blue heron bakery": {"18.75"}})

    def test_an_id_inside_ordinary_text(self):
        self.assertTrue(self.ref.find_in_line(f'row = {{"id": "{TX_ID}"}}'))
        self.assertTrue(self.ref.find_in_line(f"account {ACCT_ID}, checking"))

    def test_the_access_token(self):
        self.assertTrue(self.ref.find_in_line(f"token = '{TOKEN}'"))

    def test_a_merchant_with_its_exact_amount(self):
        self.assertTrue(self.ref.find_in_line("2026-03-02  Blue Heron Bakery  $18.75"))
        self.assertTrue(self.ref.find_in_line("BLUE HERON BAKERY,18.75"))

    def test_the_merchant_alone_or_the_amount_alone_is_fine(self):
        self.assertEqual(self.ref.find_in_line("I like Blue Heron Bakery"), [])
        self.assertEqual(self.ref.find_in_line("total was $18.75"), [])
        self.assertEqual(self.ref.find_in_line("Blue Heron Bakery $19.75"), [])

    def test_ordinary_code_and_text_is_left_alone(self):
        for line in ("def get_all_transactions(days=30):",
                     "self.assertEqual(spend['total'], 22.49)",
                     "kBq7nXe3WQfVZ4mrPLdA9hUTYo1sJ",       # one character off
                     "a" * 40, ""):
            self.assertEqual(self.ref.find_in_line(line), [], line)

    def test_a_reference_with_nothing_loaded_finds_nothing(self):
        self.assertEqual(cpd.Reference().find_in_line(f"{TX_ID} {TOKEN}"), [])

    def test_findings_never_repeat_the_value(self):
        for line in (TX_ID, TOKEN, "Blue Heron Bakery 18.75"):
            for kind in self.ref.find_in_line(line):
                for secret in (TX_ID, TOKEN, "Blue Heron Bakery", "18.75"):
                    self.assertNotIn(secret, kind)


class FileNames(unittest.TestCase):
    def test_data_and_credential_files_are_recognised(self):
        for name in ("money_guilt.db", "x.sqlite", "x.sqlite3", "export.csv",
                     "bank.ofx", "bank.qfx", "sheet.xlsx", "sub/dir/data.tsv",
                     ".env", "sub/.env", "access_token.txt", "merchant_overrides.json",
                     "telemetry.json", "transactions.json", "transactions_2026.json",
                     "key.pem", "id.key", "money_guilt.db-wal", "data.DB", "A.CSV"):
            self.assertTrue(cpd.FORBIDDEN_NAME.search(name), name)

    def test_ordinary_source_files_are_not(self):
        for name in ("widget.py", "stats.py", "settings_dialog.py", "styles.css",
                     "README.md", "PRIVACY.md", ".env.example", ".claude/settings.json",
                     "test_app.py", "hooks/pre-commit", "database.py",
                     "csv_notes.md", "dbutils.py"):
            self.assertFalse(cpd.FORBIDDEN_NAME.search(name), name)


class Scanning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = make_repo(self.tmp)
        self.ref = cpd.Reference({TX_ID, ACCT_ID}, TOKEN, {"blue heron bakery": {"18.75"}})

    def commit(self, message="c"):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message, "--no-verify")
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def test_staged_id_is_found_with_file_and_line(self):
        write(self.repo, "a.py", f"x = 1\ny = '{TX_ID}'\n")
        git(self.repo, "add", "a.py")
        found = cpd.scan_staged(self.ref, self.repo)
        self.assertEqual(len(found), 1)
        self.assertIn("a.py:2", found[0])
        self.assertNotIn(TX_ID, found[0])

    def test_staged_clean_change_passes(self):
        write(self.repo, "a.py", "def f():\n    return 1\n")
        git(self.repo, "add", "a.py")
        self.assertEqual(cpd.scan_staged(self.ref, self.repo), [])

    def test_staged_data_file_is_found_by_name_even_if_its_content_is_harmless(self):
        write(self.repo, "notes.csv", "a,b\n1,2\n")
        git(self.repo, "add", "notes.csv")
        self.assertEqual(len(cpd.scan_staged(self.ref, self.repo)), 1)

    def test_only_added_lines_count_not_context_or_removals(self):
        write(self.repo, "a.py", f"keep = '{TX_ID}'\n")
        self.commit("has it")
        write(self.repo, "a.py", "keep = 'gone'\n")     # removes it
        git(self.repo, "add", "a.py")
        self.assertEqual(cpd.scan_staged(self.ref, self.repo), [],
                         "deleting a leak must not be blocked")

    def test_deleting_a_data_file_is_allowed(self):
        write(self.repo, "old.csv", "x\n")
        self.commit("add")
        git(self.repo, "rm", "-q", "old.csv")
        self.assertEqual(cpd.scan_staged(self.ref, self.repo), [])

    def test_a_pushed_range_catches_a_leak_that_was_later_removed(self):
        write(self.repo, "a.py", "ok = 1\n")
        base = self.commit("base")
        write(self.repo, "a.py", f"leak = '{TX_ID}'\n")
        leaky = self.commit("oops")
        write(self.repo, "a.py", "ok = 2\n")
        tip = self.commit("fixed")
        found = cpd.scan_commits(self.ref, [f"{base}..{tip}"], self.repo)
        self.assertEqual(len(found), 1, "the tip is clean but history still carries it")
        self.assertIn(leaky[:8], found[0])
        self.assertEqual(cpd.scan_commits(self.ref, [f"{leaky}..{tip}"], self.repo), [],
                         "a range that excludes the leaky commit is fine")

    def test_history_scan_covers_every_commit(self):
        write(self.repo, "a.py", "ok = 1\n")
        self.commit("one")
        write(self.repo, "b.py", f"t = '{TOKEN}'\n")
        self.commit("two")
        self.assertEqual(len(cpd.scan_commits(self.ref, ["--all"], self.repo)), 1)

    def test_tracked_scan_reads_current_files(self):
        write(self.repo, "a.py", "ok = 1\n")
        write(self.repo, "b.md", "| 2026-03-02 | Blue Heron Bakery | 18.75 |\n")
        self.commit("docs")
        found = cpd.scan_tracked(self.ref, self.repo)
        self.assertEqual(len(found), 1)
        self.assertIn("b.md:1", found[0])

    def test_a_non_repository_is_an_error_not_a_pass(self):
        with self.assertRaises(cpd.CheckError):
            cpd.scan_staged(self.ref, self.tmp)


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = make_repo(self.tmp)
        self.db = make_database(self.tmp)

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cpd.main([*args, "--db", self.db, "--no-keychain"], repo=self.repo)
        return code, out.getvalue() + err.getvalue()

    def test_exit_codes_and_output(self):
        write(self.repo, "a.py", "ok = 1\n")
        git(self.repo, "add", "a.py")
        self.assertEqual(self.run_main("--staged")[0], 0)

        write(self.repo, "b.py", f"x = '{TX_ID}'  # Blue Heron Bakery 18.75\n")
        git(self.repo, "add", "b.py")
        code, text = self.run_main("--staged")
        self.assertEqual(code, 1)
        self.assertIn("b.py:1", text)
        for private in (TX_ID, "Blue Heron Bakery", "18.75"):
            self.assertNotIn(private, text, "the output must not repeat what it found")

    def test_it_fails_closed_when_git_cannot_be_read(self):
        outside = tempfile.mkdtemp()
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            code = cpd.main(["--staged", "--db", self.db, "--no-keychain"], repo=outside)
        self.assertEqual(code, 2)
        self.assertIn("could not check", out.getvalue())


class NewBranchFlag(unittest.TestCase):
    def test_exclude_remotes_scans_only_what_no_remote_has(self):
        tmp = tempfile.mkdtemp()
        repo = make_repo(tmp)
        db = make_database(tmp)
        remote = os.path.join(tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", remote], check=True)
        git(repo, "remote", "add", "origin", remote)
        write(repo, "a.py", f"old = '{TX_ID}'\n")           # already on the remote
        git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "old", "--no-verify")
        git(repo, "push", "-q", "origin", "master", "--no-verify")
        write(repo, "b.py", "new = 1\n")
        git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "new", "--no-verify")
        tip = git(repo, "rev-parse", "HEAD").stdout.strip()
        args = ["--range", tip, "--exclude-remotes", "--db", db, "--no-keychain"]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cpd.main(args, repo=repo), 0,
                             "the leak is already remote; only the new commit is checked")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cpd.main(["--range", tip, "--db", db, "--no-keychain"],
                                      repo=repo), 1, "without the flag, all history counts")


class Hooks(unittest.TestCase):
    """The real hooks, run by real git, in throwaway repositories."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = make_repo(self.tmp)
        db_dir = os.path.join(self.tmp, "data")
        os.makedirs(db_dir)
        os.replace(make_database(self.tmp), os.path.join(db_dir, "money_guilt.db"))
        self.env = {**GIT_ENV, "MONEY_GUILD_DATA_DIR": db_dir}
        git(self.repo, "config", "core.hooksPath", HOOKS)

    def commit(self, message="m"):
        git(self.repo, "add", "-A")
        return git(self.repo, "commit", "-q", "-m", message, check=False, env=self.env)

    def count(self):
        return int(git(self.repo, "rev-list", "--all", "--count").stdout.strip() or 0)

    def test_pre_commit_refuses_a_transaction_id(self):
        write(self.repo, "a.py", f"x = '{TX_ID}'\n")
        result = self.commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.count(), 0, "no commit may be created")
        self.assertNotIn(TX_ID, result.stdout + result.stderr)

    def test_pre_commit_refuses_a_data_file(self):
        write(self.repo, "export.csv", "a,b\n")
        self.assertNotEqual(self.commit().returncode, 0)

    def test_pre_commit_still_blocks_secrets(self):
        # Built here, not written out, so this file itself never contains a
        # token-shaped string that the hook would rightly refuse to commit.
        fake_secret = "PLAID_SECRET=" + "abcdef0123456789" * 2
        fake_token = "access-sandbox-" + "0123456789abcdef" * 2
        write(self.repo, ".env", fake_secret + "\n")
        self.assertNotEqual(self.commit().returncode, 0)
        os.remove(os.path.join(self.repo, ".env"))
        write(self.repo, "a.py", f'TOKEN = "{fake_token}"\n')
        self.assertNotEqual(self.commit().returncode, 0)

    def test_pre_commit_allows_ordinary_work(self):
        write(self.repo, "a.py", "def f():\n    return 1\n")
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.count(), 1)

    def _remote(self):
        remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", remote], check=True)
        git(self.repo, "remote", "add", "origin", remote)
        return remote

    def _remote_commits(self, remote):
        out = subprocess.run(["git", "-C", remote, "rev-list", "--all", "--count"],
                             capture_output=True, text=True).stdout.strip()
        return int(out or 0)

    def test_pre_push_blocks_a_leak_hidden_in_an_earlier_commit(self):
        remote = self._remote()
        write(self.repo, "a.py", "ok = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base", "--no-verify")
        write(self.repo, "a.py", f"leak = '{TX_ID}'\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "oops", "--no-verify")   # slipped past pre-commit
        write(self.repo, "a.py", "ok = 2\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "fixed", "--no-verify")  # the tip looks clean
        result = git(self.repo, "push", "origin", "master", check=False, env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nothing was sent", result.stderr)
        self.assertEqual(self._remote_commits(remote), 0, "the remote must receive nothing")
        self.assertNotIn(TX_ID, result.stdout + result.stderr)

    def test_pre_push_blocks_an_earlier_leaky_commit_when_updating_an_existing_branch(self):
        """The usual path: the branch is already on the remote."""
        remote = self._remote()
        write(self.repo, "a.py", "ok = 1\n")
        self.assertEqual(self.commit("base").returncode, 0)
        self.assertEqual(git(self.repo, "push", "origin", "master", env=self.env).returncode, 0)
        write(self.repo, "a.py", f"leak = '{TX_ID}'\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "oops", "--no-verify")
        write(self.repo, "a.py", "ok = 2\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "fixed", "--no-verify")   # the tip is clean
        result = git(self.repo, "push", "origin", "master", check=False, env=self.env)
        self.assertNotEqual(result.returncode, 0, "the middle commit must be caught")
        self.assertEqual(self._remote_commits(remote), 1, "the remote must not advance")

    def test_pre_push_allows_a_clean_push_then_checks_only_new_commits(self):
        remote = self._remote()
        write(self.repo, "a.py", "ok = 1\n")
        self.assertEqual(self.commit("one").returncode, 0)
        first = git(self.repo, "push", "origin", "master", check=False, env=self.env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._remote_commits(remote), 1)
        write(self.repo, "a.py", "ok = 2\n")
        self.assertEqual(self.commit("two").returncode, 0)
        second = git(self.repo, "push", "origin", "master", check=False, env=self.env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self._remote_commits(remote), 2)


class ProjectHygiene(unittest.TestCase):
    def test_the_gitignore_covers_data_files(self):
        with open(os.path.join(PROJECT, ".gitignore")) as fh:
            ignored = fh.read().split()
        for pattern in (".env", "access_token.txt", "*.db", "merchant_overrides.json",
                        "telemetry.json", "*.csv", "*.sqlite", "*.ofx", "*.xlsx",
                        "transactions*.json"):
            self.assertIn(pattern, ignored)

    def test_the_hooks_are_executable_and_in_the_repo(self):
        for name in ("pre-commit", "pre-push"):
            path = os.path.join(HOOKS, name)
            self.assertTrue(os.access(path, os.X_OK), name)

    def test_nothing_the_project_tracks_looks_like_data(self):
        tracked = subprocess.run(["git", "-C", PROJECT, "ls-files", "-z"],
                                 capture_output=True, text=True).stdout.split("\0")
        for name in filter(None, tracked):
            self.assertFalse(cpd.FORBIDDEN_NAME.search(name), name)


if __name__ == "__main__":
    unittest.main()
