"""Where the Plaid access token lives: the OS keychain, not a file.

On macOS this is the login Keychain. A plain-text access_token.txt could be
read by any process running as the user, ended up world-readable, and was
one `git add -f` from a public repository. There is deliberately no
plaintext fallback: if no keychain is available this raises instead of
quietly writing the token to disk.
"""
import os
import stat
import sys

# Makes "from data import paths" below work even if this file is run
# directly (python3 banking/secure_store.py), where Python would otherwise
# only know about the banking/ folder, not the project root above it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyring  # third-party library: a cross-platform wrapper around the
                 # OS's credential store (Keychain on macOS, Credential
                 # Manager on Windows, etc.)
from keyring.errors import KeyringError

from data import paths

# `keyring` organizes stored secrets by a "service" name and an "account"
# name within that service - together they're the lookup key for this one
# secret, similar to a dictionary key.
SERVICE = "money_guilt"
ACCOUNT = "plaid_access_token"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# An old, plain-text location this app used to store the token in, before
# the Keychain was used. Kept only so migrate_legacy_file() below can find
# and clean up any leftover file from an older install of the app.
LEGACY_TOKEN_FILE = os.path.join(BASE_DIR, "access_token.txt")

# Files that hold credentials or personal financial data.
SENSITIVE_FILES = paths.private_paths()


class SecureStoreError(RuntimeError):
    """Raised instead of silently doing something unsafe - e.g. this is
    raised rather than ever falling back to writing a token to plain text."""
    pass


def _check(token):
    """Reject anything that isn't a real, non-blank string before it's
    ever handed to the keychain."""
    if not isinstance(token, str) or not token.strip():
        raise SecureStoreError("Refusing to store an empty access token.")


def set_access_token(token):
    _check(token)
    try:
        keyring.set_password(SERVICE, ACCOUNT, token)
    except KeyringError as exc:
        # `raise ... from exc` keeps the original error attached as the
        # "cause" of this new one, so anyone debugging later still sees the
        # real underlying keychain failure, not just this message on its own.
        raise SecureStoreError(
            f"Could not save the access token to the system keychain: {exc}") from exc


def delete_access_token():
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except KeyringError:
        # There being nothing to delete (or the keychain being briefly
        # unavailable) isn't treated as an error here - the end state
        # ("no token stored") is the same either way.
        pass


def migrate_legacy_file(path=None):
    """Move a token out of access_token.txt and into the keychain.

    The file is removed only after the token has been read back from the
    keychain and matches, so a failed write cannot lose the token.
    Returns the token, or None if there was no file.
    """
    path = path or LEGACY_TOKEN_FILE
    if not os.path.exists(path):
        return None

    with open(path) as fh:
        token = fh.read().strip()
    if not token:
        return None

    set_access_token(token)
    # Read the token back out of the keychain (rather than trusting that the
    # write above "must have worked") before deleting the only other copy.
    # This is the same "verify, then delete the original" pattern paths.py
    # uses for moving files: never destroy the one known-good copy of
    # something until a second copy is confirmed to actually be there.
    try:
        stored = keyring.get_password(SERVICE, ACCOUNT)
    except KeyringError as exc:
        raise SecureStoreError(f"Could not verify the keychain copy: {exc}") from exc
    if stored != token:
        raise SecureStoreError(
            "The keychain did not return the token that was just saved; "
            f"leaving {os.path.basename(path)} in place.")

    os.remove(path)
    return token


def get_access_token():
    """The access token, or None if the account has not been linked.

    PLAID_ACCESS_TOKEN in the environment wins, for one-off scripts. A legacy
    access_token.txt is migrated into the keychain the first time it is seen.
    """
    # An explicit environment variable always wins, mainly so a developer
    # running a one-off debugging script can point at a specific token
    # without touching the real keychain entry.
    override = os.getenv("PLAID_ACCESS_TOKEN")
    if override:
        return override

    try:
        token = keyring.get_password(SERVICE, ACCOUNT)
    except KeyringError as exc:
        raise SecureStoreError(
            f"Could not read the access token from the system keychain: {exc}") from exc
    if token:
        return token

    # Nothing in the keychain yet - check whether there's an old plain-text
    # file to migrate from (returns None itself if there isn't one).
    return migrate_legacy_file()


def mask(token):
    """A prefix that is safe to show; never the whole token."""
    return (token[:16] + "...") if token else "(none)"


def harden_file(path):
    """Restrict a file to its owner (0600). A missing file is ignored."""
    try:
        # stat.S_IMODE strips out the bits that say "this is a regular
        # file" etc., leaving just the permission bits (e.g. 0o644) to
        # compare against.
        current = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return False
    if current != 0o600:  # owner read/write only
        os.chmod(path, 0o600)
        return True  # it had to be changed
    return False  # it was already correct


def harden_data_files(paths=None):
    """Owner-only permissions on credentials and financial data.

    Returns the paths that had to be changed.
    """
    # Runs harden_file() on every sensitive path, keeping only the ones
    # that actually needed fixing (harden_file returns True for those).
    return [p for p in (paths or SENSITIVE_FILES) if harden_file(p)]


if __name__ == "__main__":
    # Lets you run `python3 banking/secure_store.py` directly as a quick,
    # standalone check/fix of file permissions, outside of the main app.
    changed = harden_data_files()
    print("Tightened:" if changed else "Already owner-only.")
    for path in changed:
        print("  " + os.path.basename(path))
