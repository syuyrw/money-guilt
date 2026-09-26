"""Where the Plaid access token lives: the OS keychain, not a file.

On macOS this is the login Keychain. A plain-text access_token.txt could be
read by any process running as the user, ended up world-readable, and was
one `git add -f` from a public repository. There is deliberately no
plaintext fallback: if no keychain is available this raises instead of
quietly writing the token to disk.
"""
import os
import stat

import keyring
from keyring.errors import KeyringError

import paths

SERVICE = "money_guilt"
ACCOUNT = "plaid_access_token"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_TOKEN_FILE = os.path.join(BASE_DIR, "access_token.txt")

# Files that hold credentials or personal financial data.
SENSITIVE_FILES = paths.private_paths()


class SecureStoreError(RuntimeError):
    pass


def _check(token):
    if not isinstance(token, str) or not token.strip():
        raise SecureStoreError("Refusing to store an empty access token.")


def set_access_token(token):
    _check(token)
    try:
        keyring.set_password(SERVICE, ACCOUNT, token)
    except KeyringError as exc:
        raise SecureStoreError(
            f"Could not save the access token to the system keychain: {exc}") from exc


def delete_access_token():
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except KeyringError:
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

    return migrate_legacy_file()


def mask(token):
    """A prefix that is safe to show; never the whole token."""
    return (token[:16] + "...") if token else "(none)"


def harden_file(path):
    """Restrict a file to its owner (0600). A missing file is ignored."""
    try:
        current = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return False
    if current != 0o600:
        os.chmod(path, 0o600)
        return True
    return False


def harden_data_files(paths=None):
    """Owner-only permissions on credentials and financial data.

    Returns the paths that had to be changed.
    """
    return [p for p in (paths or SENSITIVE_FILES) if harden_file(p)]


if __name__ == "__main__":
    changed = harden_data_files()
    print("Tightened:" if changed else "Already owner-only.")
    for path in changed:
        print("  " + os.path.basename(path))
