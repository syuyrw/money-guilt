"""Where Money Guilt keeps its private data.

The project folder lives inside ~/Documents, which macOS can sync to iCloud, so
anything left there (the transaction database, the merchant lessons, the .env
holding the Plaid secret) is copied to Apple's servers and to every device on
the Apple ID. ~/Library/Application Support is not synced, and an absolute
path also means the app finds its data no matter where it was launched from.

    python paths.py            show the locations
    python paths.py --migrate  move files out of the project folder
"""
import hashlib
import os
import shutil
import stat
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_NAME = ".env"
DB_NAME = "money_guilt.db"
OVERRIDES_NAME = "merchant_overrides.json"
PRIVATE_FILES = (ENV_NAME, DB_NAME, OVERRIDES_NAME)

# SQLite leaves these beside the database while a write is unfinished.
DB_SIDE_FILES = ("-journal", "-wal", "-shm")


def data_dir():
    """The private data directory, created owner-only on first use.

    MONEY_GUILD_DATA_DIR overrides it, which is how the tests stay away from
    the real one.
    """
    path = os.getenv("MONEY_GUILD_DATA_DIR") or os.path.expanduser(
        "~/Library/Application Support/MoneyGuilt")
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o700)  # makedirs' mode argument is trimmed by the umask
    return path


def db_path():
    return os.path.join(data_dir(), DB_NAME)


def overrides_path():
    return os.path.join(data_dir(), OVERRIDES_NAME)


def env_path():
    return os.path.join(data_dir(), ENV_NAME)


def private_paths():
    return [os.path.join(data_dir(), name) for name in PRIVATE_FILES]


def load_env():
    """Load the Plaid credentials from the private directory.

    A .env still in the project folder is used only as a fallback, and is
    reported, because that copy is in a folder that may be syncing to iCloud.
    """
    from dotenv import load_dotenv

    if os.path.exists(env_path()):
        load_dotenv(env_path())
        return env_path()

    legacy = os.path.join(PROJECT_DIR, ENV_NAME)
    if os.path.exists(legacy):
        print("warning: reading .env from the project folder, which may sync "
              "to iCloud. Run: python paths.py --migrate", file=sys.stderr)
        load_dotenv(legacy)
        return legacy
    return None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_private_files(source_dir=None, dest_dir=None):
    """Move private files out of the project folder.

    Each file is copied, checked byte for byte against the original, and only
    then does the original go. Nothing at the destination is ever overwritten.
    Returns {name: what happened}.
    """
    source_dir = source_dir or PROJECT_DIR
    dest_dir = dest_dir or data_dir()
    report = {}

    for name in PRIVATE_FILES:
        src = os.path.join(source_dir, name)
        dst = os.path.join(dest_dir, name)

        if not os.path.exists(src):
            report[name] = "not in the project folder"
            continue

        if name == DB_NAME and any(os.path.exists(src + s) for s in DB_SIDE_FILES):
            report[name] = "skipped: database looks in use (journal file present)"
            continue

        if os.path.exists(dst):
            if _sha256(src) == _sha256(dst):
                os.remove(src)
                report[name] = "already there; removed the duplicate"
            else:
                report[name] = "CONFLICT: differs from the copy in the data directory; both left"
            continue

        partial = dst + ".part"
        shutil.copyfile(src, partial)
        os.chmod(partial, 0o600)
        if _sha256(src) != _sha256(partial):
            os.remove(partial)
            report[name] = "FAILED: copy did not match; original left in place"
            continue
        os.replace(partial, dst)
        os.remove(src)
        report[name] = "moved"

    return report


if __name__ == "__main__":
    print("Data directory:", data_dir())
    for path in private_paths():
        print("  " + ("present  " if os.path.exists(path) else "missing  ") + path)
    if "--migrate" in sys.argv:
        print("\nMigrating from", PROJECT_DIR)
        for name, result in migrate_private_files().items():
            print(f"  {name:24} {result}")
