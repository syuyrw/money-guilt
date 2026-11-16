"""Where Money Guilt keeps its private data.

The project folder lives inside ~/Documents, which macOS can sync to iCloud, so
anything left there (the transaction database, the merchant lessons, the .env
holding the Plaid secret) is copied to Apple's servers and to every device on
the Apple ID. ~/Library/Application Support is not synced, and an absolute
path also means the app finds its data no matter where it was launched from.

    python data/paths.py            show the locations
    python data/paths.py --migrate  move files out of the project folder
"""
import hashlib
import os
import shutil
import stat
import sys

# This file lives one level inside the project folder (data/paths.py), so the
# project root a legacy .env might be sitting in is one level up from here.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    # `or` here means: use the env var if it's set and non-empty, otherwise
    # fall back to the real macOS path. os.path.expanduser turns "~" into
    # the actual home directory (e.g. /Users/yourname).
    path = os.getenv("MONEY_GUILD_DATA_DIR") or os.path.expanduser(
        "~/Library/Application Support/MoneyGuilt")
    # exist_ok=True means "don't raise an error if the folder is already
    # there" - this function can safely be called every time the app starts.
    os.makedirs(path, exist_ok=True)
    # 0o700 (octal) = read/write/execute for the owner only, nothing for
    # anyone else. This is re-applied on every call because makedirs()'s own
    # `mode` argument gets filtered through the OS's umask and can't be
    # trusted to actually produce 0o700 on its own.
    os.chmod(path, 0o700)  # makedirs' mode argument is trimmed by the umask
    return path


# Three tiny helper functions that just glue a filename onto the data
# directory. Having them means the rest of the codebase never has to know
# or care *where* these files live - it just calls db_path(), etc.
def db_path():
    return os.path.join(data_dir(), DB_NAME)


def overrides_path():
    return os.path.join(data_dir(), OVERRIDES_NAME)


def env_path():
    return os.path.join(data_dir(), ENV_NAME)


def private_paths():
    # A list comprehension: builds a new list by running the expression
    # (the os.path.join call) once for every name in PRIVATE_FILES.
    return [os.path.join(data_dir(), name) for name in PRIVATE_FILES]


def load_env():
    """Load the Plaid credentials from the private directory.

    A .env still in the project folder is used only as a fallback, and is
    reported, because that copy is in a folder that may be syncing to iCloud.
    """
    # Imported here, rather than at the top of the file, so that importing
    # this module doesn't require python-dotenv to be installed unless this
    # specific function actually gets called.
    from dotenv import load_dotenv

    # First choice: the real, private .env. load_dotenv() reads KEY=VALUE
    # lines from the file and sets them as environment variables (so code
    # elsewhere can just call os.getenv("PLAID_SECRET")).
    if os.path.exists(env_path()):
        load_dotenv(env_path())
        return env_path()

    # Fallback: an old .env that might still be sitting in the project
    # folder from before this app started keeping private files elsewhere.
    legacy = os.path.join(PROJECT_DIR, ENV_NAME)
    if os.path.exists(legacy):
        print("warning: reading .env from the project folder, which may sync "
              "to iCloud. Run: python data/paths.py --migrate", file=sys.stderr)
        load_dotenv(legacy)
        return legacy
    return None  # no .env found anywhere


def _sha256(path):
    """A file's SHA-256 hash - a fingerprint used to confirm two files are
    byte-for-byte identical without comparing their entire contents directly."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        # Reads the file in 1 MiB chunks (1 << 20 = 1,048,576 bytes) rather
        # than loading it all into memory at once, which matters for a
        # database file that could be large. iter(callable, sentinel) keeps
        # calling fh.read(...) until it returns the sentinel value b"" (an
        # empty bytes object, meaning end of file).
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
    report = {}  # one entry per filename, describing what happened to it

    for name in PRIVATE_FILES:
        src = os.path.join(source_dir, name)
        dst = os.path.join(dest_dir, name)

        if not os.path.exists(src):
            report[name] = "not in the project folder"
            continue  # nothing to do for this file; move on to the next one

        # A database mid-write leaves a "-journal"/"-wal"/"-shm" file next to
        # it; moving the .db file while one of those exists could corrupt it.
        if name == DB_NAME and any(os.path.exists(src + s) for s in DB_SIDE_FILES):
            report[name] = "skipped: database looks in use (journal file present)"
            continue

        if os.path.exists(dst):
            # Something is already at the destination. If it's an identical
            # copy, the source is just a leftover duplicate - safe to delete.
            # If it's different, this is a real conflict: never silently
            # overwrite someone's data, so both copies are left in place.
            if _sha256(src) == _sha256(dst):
                os.remove(src)
                report[name] = "already there; removed the duplicate"
            else:
                report[name] = "CONFLICT: differs from the copy in the data directory; both left"
            continue

        # The actual move happens in three careful steps, so a crash or a
        # bad copy never leaves you with *neither* a working original nor a
        # working copy:
        #   1. Copy to a temporary ".part" name (never the real destination
        #      name yet, so a half-written file can't be mistaken for the
        #      real thing).
        partial = dst + ".part"
        shutil.copyfile(src, partial)
        os.chmod(partial, 0o600)  # owner read/write only - it holds a secret or a database
        #   2. Verify the copy is byte-for-byte identical to the original.
        if _sha256(src) != _sha256(partial):
            os.remove(partial)
            report[name] = "FAILED: copy did not match; original left in place"
            continue
        #   3. Only now, with a verified-good copy sitting at the real
        #      destination path, delete the original.
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
