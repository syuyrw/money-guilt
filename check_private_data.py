#!/usr/bin/env python3
"""Keep transaction data out of the repository.

Transactions live only in the local database (paths.db_path()). This finds
anything in a commit that could only have come from it:

  * identifiers that exist only in real data: the transaction and account IDs
    in the local database, and the Plaid access token;
  * a merchant name and its exact amount on the same line, which is what a
    pasted transaction looks like;
  * files whose names say they hold data: databases, spreadsheets, exports.

It reports where it found something and never what, so its own output is safe
to paste anywhere.

    check_private_data.py --staged            what is about to be committed
    check_private_data.py --range A..B        commits about to be pushed
    check_private_data.py --range TIP --exclude-remotes
                                              a new branch: what no remote has
    check_private_data.py --tracked           every file now tracked
    check_private_data.py --history           every commit on every branch

Exit status: 0 clean, 1 something found, 2 could not check (treated as a
failure, so a hook never waves a push through because git misbehaved).
"""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

import paths  # noqa: E402

GIT = shutil.which("git") or "git"

# Files that are, or hold, data or credentials.
FORBIDDEN_NAME = re.compile(
    r"(^|/)(\.env|access_token\.txt|merchant_overrides\.json|telemetry\.json)$"
    r"|\.(db|db-journal|db-wal|db-shm|sqlite3?|csv|tsv|ofx|qfx|xlsx?|pem|key)$"
    r"|(^|/)transactions?[^/]*\.json$",
    re.IGNORECASE)

CANDIDATE = re.compile(r"[A-Za-z0-9_\-]{12,}")   # shaped like an ID or a token
DECIMAL = re.compile(r"\d+\.\d{2}")
MIN_ID_LENGTH = 12       # shorter values are too likely to be ordinary words
MIN_NAME_LENGTH = 4


class CheckError(RuntimeError):
    pass


class Reference:
    """What real data looks like on this machine."""

    def __init__(self, ids=(), token=None, pairs=None):
        self.ids = {i for i in ids if i and len(i) >= MIN_ID_LENGTH}
        self.token = token if token and len(token) >= MIN_ID_LENGTH else None
        # merchant name (lower case) -> the amounts, as text, it appears with
        self.pairs = pairs or {}

    @classmethod
    def load(cls, db_path=None, use_keychain=True):
        ids, pairs = set(), {}
        db_path = db_path or paths.db_path()
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
                try:
                    for table, column in (("transactions", "id"),
                                          ("transactions", "account_id"),
                                          ("accounts", "id")):
                        try:
                            ids |= {str(r[0]) for r in
                                    conn.execute(f"SELECT {column} FROM {table}")}  # nosec B608 - fixed names above
                        except sqlite3.Error:
                            pass
                    try:
                        for name, amount in conn.execute(
                                "SELECT name, amount FROM transactions"):
                            if name and len(name) >= MIN_NAME_LENGTH and amount:
                                pairs.setdefault(name.lower(), set()).add(
                                    f"{abs(float(amount)):.2f}")
                    except (sqlite3.Error, ValueError):
                        pass
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
        return cls(ids, cls._keychain_token() if use_keychain else None, pairs)

    @staticmethod
    def _keychain_token():
        """Read the token without migrating or touching anything else."""
        try:
            import keyring
            import secure_store
            return keyring.get_password(secure_store.SERVICE, secure_store.ACCOUNT)
        except Exception:    # no keyring, no keychain, or access refused
            return None

    def find_in_line(self, line):
        """The kinds of real data in this line, without saying which."""
        kinds = []
        candidates = set(CANDIDATE.findall(line))
        if self.ids and candidates & self.ids:
            kinds.append("a transaction or account ID from your local database")
        if self.token and self.token in line:
            kinds.append("your Plaid access token")
        if self.pairs and DECIMAL.search(line):
            low = line.lower()
            amounts = set(DECIMAL.findall(line))
            for name, seen in self.pairs.items():
                if name in low and amounts & seen:
                    kinds.append("a merchant and its exact amount from your local database")
                    break
        return kinds


def _git(repo, *args):
    try:
        result = subprocess.run([GIT, "-C", repo, *args], capture_output=True,
                                check=False)
    except OSError as exc:
        raise CheckError(f"could not run git: {exc}")
    if result.returncode != 0:
        raise CheckError(f"git {' '.join(args[:2])} failed: "
                         f"{result.stderr.decode(errors='replace').strip()[:200]}")
    return result.stdout


def _names(output):
    return [n for n in output.decode(errors="replace").split("\0") if n]


def _added_lines(diff_text):
    """(path, line number, text) for each added line in a -U0 diff."""
    path, number = None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            number = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            yield path, number, line[1:]
            number += 1


def _scan_diff(reference, diff_text, where):
    findings = []
    for path, number, text in _added_lines(diff_text):
        for kind in reference.find_in_line(text):
            findings.append(f"{where}{path}:{number}: {kind}")
    return findings


def _scan_names(names, where):
    return [f"{where}{name}: a file that looks like data or credentials"
            for name in names if FORBIDDEN_NAME.search(name)]


def scan_staged(reference, repo="."):
    findings = _scan_names(_names(_git(
        repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")),
        "staged ")
    diff = _git(repo, "diff", "--cached", "-U0", "--no-color",
                "--diff-filter=ACMR").decode(errors="replace")
    return findings + _scan_diff(reference, diff, "staged ")


def scan_commit(reference, sha, repo="."):
    where = f"commit {sha[:8]} "
    findings = _scan_names(_names(_git(
        repo, "show", "--name-only", "--format=", "--diff-filter=ACMR", "-z", sha)),
        where)
    diff = _git(repo, "show", "-U0", "--no-color", "--format=",
                "--diff-filter=ACMR", sha).decode(errors="replace")
    return findings + _scan_diff(reference, diff, where)


def scan_commits(reference, rev_args, repo="."):
    shas = _git(repo, "rev-list", "--reverse", *rev_args).decode().split()
    findings = []
    for sha in shas:
        findings += scan_commit(reference, sha, repo)
    return findings


def scan_tracked(reference, repo="."):
    findings = []
    names = _names(_git(repo, "ls-files", "-z"))
    findings += _scan_names(names, "tracked ")
    for name in names:
        full = os.path.join(repo, name)
        try:
            with open(full, "rb") as fh:
                text = fh.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for kind in reference.find_in_line(line):
                findings.append(f"tracked {name}:{number}: {kind}")
    return findings


def main(argv=None, repo=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--range", nargs="+", metavar="REV")
    mode.add_argument("--tracked", action="store_true")
    mode.add_argument("--history", action="store_true")
    parser.add_argument("--exclude-remotes", action="store_true",
                        help="with --range TIP: only commits no remote already has")
    parser.add_argument("--db", help="database to take identifiers from")
    parser.add_argument("--no-keychain", action="store_true",
                        help="don't look for the access token in the Keychain")
    args = parser.parse_args(argv)
    repo = repo or os.getcwd()

    use_keychain = not (args.no_keychain or os.getenv("PRIVATE_DATA_NO_KEYCHAIN"))
    try:
        reference = Reference.load(args.db, use_keychain)
        if args.staged:
            findings = scan_staged(reference, repo)
        elif args.range:
            revs = list(args.range)
            if args.exclude_remotes:
                revs += ["--not", "--remotes"]
            findings = scan_commits(reference, revs, repo)
        elif args.tracked:
            findings = scan_tracked(reference, repo)
        else:
            findings = scan_commits(reference, ["--all"], repo)
    except CheckError as exc:
        print(f"check_private_data: could not check: {exc}", file=sys.stderr)
        return 2

    if findings:
        print(f"check_private_data: BLOCKED, {len(findings)} problem(s). "
              "Transaction data must stay in the local database only. "
              "Nothing private is printed here.", file=sys.stderr)
        for item in findings[:25]:
            print("  " + item, file=sys.stderr)
        if len(findings) > 25:
            print(f"  ... and {len(findings) - 25} more", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
