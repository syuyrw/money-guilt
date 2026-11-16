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

    security/check_private_data.py --staged            what is about to be committed
    security/check_private_data.py --range A..B        commits about to be pushed
    security/check_private_data.py --range TIP --exclude-remotes
                                                        a new branch: what no remote has
    security/check_private_data.py --tracked           every file now tracked
    security/check_private_data.py --history           every commit on every branch

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

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from data import paths  # noqa: E402

GIT = shutil.which("git") or "git"  # the git executable's full path, if found

# Files that are, or hold, data or credentials. This one regex has three
# alternatives (separated by "|", read "OR"): an exact filename match like
# ".env", a file extension like ".db", or a "transactions...json" pattern.
# "(^|/)" before a name means "at the very start of the path, or right
# after a slash" - i.e. this is the file's own name, not matching a
# filename that merely happens to appear as a substring of a longer one.
FORBIDDEN_NAME = re.compile(
    r"(^|/)(\.env|access_token\.txt|merchant_overrides\.json|telemetry\.json|plaid_sync\.json)$"
    r"|\.(db|db-journal|db-wal|db-shm|sqlite3?|csv|tsv|ofx|qfx|xlsx?|pem|key)$"
    r"|(^|/)transactions?[^/]*\.json$",
    re.IGNORECASE)

# Any run of 12+ letters/digits/underscores/hyphens - the general shape a
# database ID or an access token has. This is deliberately broad; it's
# narrowed down afterwards by actually checking it against real IDs/tokens
# from this machine (see Reference.find_in_line below), not used as a
# standalone signal on its own.
CANDIDATE = re.compile(r"[A-Za-z0-9_\-]{12,}")   # shaped like an ID or a token
DECIMAL = re.compile(r"\d+\.\d{2}")              # a dollar-and-cents amount, e.g. "12.34"
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
        """Reads the real transaction/account IDs, the Plaid token, and
        every (merchant, amount) pair straight out of the local database
        and Keychain, so this check has something real to compare commits
        against. This is the only place in the whole script that actually
        touches private data - everything from here on only ever reports
        *that* something matched, never *what*."""
        ids, pairs = set(), {}
        db_path = db_path or paths.db_path()
        if os.path.exists(db_path):
            try:
                # "mode=ro" opens the database read-only - this script
                # must never be able to accidentally change anything in
                # the real database it's scanning.
                conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
                try:
                    for table, column in (("transactions", "id"),
                                          ("transactions", "account_id"),
                                          ("accounts", "id")):
                        try:
                            # `ids |=` is set union-and-assign: adds every
                            # ID found into the running `ids` set. A table
                            # that doesn't exist yet (sqlite3.Error) is
                            # just skipped, not treated as a hard failure.
                            ids |= {str(r[0]) for r in
                                    conn.execute(f"SELECT {column} FROM {table}")}  # nosec B608 - fixed names above
                        except sqlite3.Error:
                            pass
                    try:
                        for name, amount in conn.execute(
                                "SELECT name, amount FROM transactions"):
                            if name and len(name) >= MIN_NAME_LENGTH and amount:
                                # Builds {merchant: {"12.34", "9.99", ...}} -
                                # every exact amount ever seen for that
                                # merchant, so find_in_line() below can spot
                                # a real merchant+amount pair, not just a
                                # merchant name that happens to appear
                                # somewhere alongside some unrelated number.
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
            from banking import secure_store
            return keyring.get_password(secure_store.SERVICE, secure_store.ACCOUNT)
        except Exception:    # no keyring, no keychain, or access refused
            return None

    def find_in_line(self, line):
        """The kinds of real data in this line, without saying which."""
        kinds = []
        # candidates & self.ids is set intersection: true only if at least
        # one "ID-shaped" string found in this line is also a real ID from
        # the database - this is what keeps a random 12+ character word in
        # a comment from triggering a false alarm.
        candidates = set(CANDIDATE.findall(line))
        if self.ids and candidates & self.ids:
            kinds.append("a transaction or account ID from your local database")
        if self.token and self.token in line:
            kinds.append("your Plaid access token")
        if self.pairs and DECIMAL.search(line):
            low = line.lower()
            amounts = set(DECIMAL.findall(line))
            for name, seen in self.pairs.items():
                # Both the merchant's name AND one of its exact recorded
                # amounts have to appear on the same line - a merchant
                # name alone (e.g. in a test fixture using a fake amount)
                # isn't enough to flag it.
                if name in low and amounts & seen:
                    kinds.append("a merchant and its exact amount from your local database")
                    break
        return kinds


def _git(repo, *args):
    """Runs a git command in `repo` and returns its raw output, or raises
    CheckError if git itself couldn't run or reported failure."""
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
    """Splits git's "-z" (NUL-separated) output into a clean list of
    filenames. NUL (the \\0 byte) is used instead of a newline because a
    filename could legitimately contain a newline itself, but never a NUL
    byte - this is git's own convention for "give me machine-readable
    output"."""
    return [n for n in output.decode(errors="replace").split("\0") if n]


def _added_lines(diff_text):
    """(path, line number, text) for each added line in a -U0 diff.

    A git diff with -U0 (zero lines of context) shows only the lines that
    actually changed. Each file's section starts with a "+++ b/path" line
    naming it, and each contiguous block of changes starts with an "@@
    -old_start,old_count +new_start,new_count @@" header - the number
    right after the "+" is the line number the new content starts at.
    Every line after that beginning with "+" (but not "+++") is new
    content, numbered consecutively from there.
    """
    path, number = None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            # Strip the "+++ b/" prefix git puts before the path (or just
            # "+++ " for a file that didn't exist in either version, which
            # shouldn't come up in practice here).
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            number = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            yield path, number, line[1:]  # line[1:] drops the leading "+"
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
    """Everything about to be committed right now (git's "staging area")."""
    # --diff-filter=ACMR limits this to Added/Copied/Modified/Renamed files
    # - a file being deleted can't introduce anything new, so there's
    # nothing useful to scan there.
    findings = _scan_names(_names(_git(
        repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")),
        "staged ")
    diff = _git(repo, "diff", "--cached", "-U0", "--no-color",
                "--diff-filter=ACMR").decode(errors="replace")
    return findings + _scan_diff(reference, diff, "staged ")


def scan_commit(reference, sha, repo="."):
    """One specific commit, identified by its SHA (git's hash ID)."""
    where = f"commit {sha[:8]} "  # the first 8 characters are enough to identify it in output
    findings = _scan_names(_names(_git(
        repo, "show", "--name-only", "--format=", "--diff-filter=ACMR", "-z", sha)),
        where)
    diff = _git(repo, "show", "-U0", "--no-color", "--format=",
                "--diff-filter=ACMR", sha).decode(errors="replace")
    return findings + _scan_diff(reference, diff, where)


def scan_commits(reference, rev_args, repo="."):
    """Every commit matched by `rev_args` (git's own revision syntax, e.g.
    a range like "abc123..def456", or "--all" for the whole history)."""
    shas = _git(repo, "rev-list", "--reverse", *rev_args).decode().split()
    findings = []
    for sha in shas:
        findings += scan_commit(reference, sha, repo)
    return findings


def scan_tracked(reference, repo="."):
    """Every file currently tracked by git, scanned as it exists on disk
    right now (not just what changed in some diff)."""
    findings = []
    names = _names(_git(repo, "ls-files", "-z"))
    findings += _scan_names(names, "tracked ")
    for name in names:
        full = os.path.join(repo, name)
        try:
            with open(full, "rb") as fh:
                # errors="ignore" means any byte that isn't valid UTF-8
                # text (e.g. this accidentally being pointed at a binary
                # file) is silently dropped rather than raising - this
                # scan just does its best with whatever text it can read.
                text = fh.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for kind in reference.find_in_line(line):
                findings.append(f"tracked {name}:{number}: {kind}")
    return findings


def main(argv=None, repo=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # A "mutually exclusive group" means exactly one of these four flags
    # must be given - picking the scanning mode to run.
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
        for item in findings[:25]:  # capped, so a huge leak doesn't flood the terminal
            print("  " + item, file=sys.stderr)
        if len(findings) > 25:
            print(f"  ... and {len(findings) - 25} more", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
