"""Optional, opt-in reporting of the total wasted dollars to a collector.

What is sent, and nothing else:
    install_id    a random ID made on first use; not tied to a name or account
    total_wasted  the running total of wasted dollars on this install
    version       this report format's version

No merchants, vendors, transaction names, dates, individual amounts, counts,
balances or Plaid data ever leave the machine. The collector keeps the latest
total per install_id and adds those up, so a transaction whose dollars were
already reported is never counted twice.

On by default, and easy to turn off: the tray menu's "Share Anonymous Wasted
Total" item, or python3 privacy/telemetry.py disable. The first launch shows
a notice saying what is shared. Nothing is sent unless a collector is set:
    MONEY_GUILT_COLLECTOR_URL=https://... in the private .env (paths.env_path())

Deleting: Settings > "Delete My Reported Data", or python3 privacy/telemetry.py
delete. That asks the collector to erase this install's row, turns sharing
off, and forgets the install ID, so anything shared later is not linked to
the deleted history. If the collector can't be reached the request is
remembered and retried automatically until it goes through; nothing is
shared meanwhile.

    python3 privacy/telemetry.py status | enable | disable | send | delete
"""
import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import paths

logger = logging.getLogger(__name__)

REPORT_VERSION = 1
CONFIG_NAME = "telemetry.json"
TIMEOUT_SECONDS = 5


def _config_path():
    return os.path.join(paths.data_dir(), CONFIG_NAME)


def load_config():
    try:
        with open(_config_path()) as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        config = {}
    return config if isinstance(config, dict) else {}


def save_config(config):
    path = _config_path()
    partial = path + ".part"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(config, fh)
    os.replace(partial, path)


def is_enabled():
    """On unless the user turned it off"""
    return load_config().get("enabled") is not False


def set_enabled(enabled):
    config = load_config()
    config["enabled"] = bool(enabled)
    save_config(config)


def needs_notice():
    """True until the user has been told what is shared"""
    return load_config().get("notice_shown") is not True


def mark_notice_shown():
    config = load_config()
    config["notice_shown"] = True
    save_config(config)


def collector_url():
    """The collector's address, or None. Must be https (http only for localhost)."""
    url = (os.getenv("MONEY_GUILT_COLLECTOR_URL") or "").strip().rstrip("/")
    # urlparse() breaks a URL string into its pieces (scheme, hostname,
    # path, ...) without actually connecting to anything.
    parsed = urlparse(url)
    # Plain http is refused except when talking to this same machine
    # (localhost/127.0.0.1, e.g. for local testing) - sending a wasted
    # total over an unencrypted connection to a real server would let
    # anyone on the network see it in transit.
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return url
    return None


def build_report(install_id, wasted):
    return {
        "install_id": install_id,
        "total_wasted": round(float(wasted["total"]), 2),
        "version": REPORT_VERSION,
    }


def _post(url, report):
    # urllib.request is Python's built-in HTTP client (no extra library
    # needed for a simple POST like this).
    request = urllib.request.Request(
        url + "/report", data=json.dumps(report).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    # The `# nosec B310` comment tells the bandit security scanner not to
    # flag this urlopen() call - urlopen() can in general open any URL
    # scheme, including unsafe ones like "file://", but collector_url()
    # above has already guaranteed this URL is https (or localhost http),
    # so that particular risk doesn't apply here.
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - scheme checked in collector_url()
        return 200 <= response.status < 300


def _post_delete(url, install_id):
    """Ask the collector to erase this install's stored total."""
    request = urllib.request.Request(
        url + "/delete", data=json.dumps({"install_id": install_id}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - scheme checked in collector_url()
        return 200 <= response.status < 300


def status():
    """What the settings window needs to describe the current state."""
    paths.load_env()
    config = load_config()
    return {
        "enabled": config.get("enabled") is not False,
        "collector_configured": collector_url() is not None,
        "has_shared": bool(config.get("last_sent")),
        "delete_pending": config.get("pending_delete") is True,
    }


def _forget_install(config):
    """Drop everything that ties this machine to what it reported."""
    for key in ("install_id", "last_sent", "pending_delete"):
        config.pop(key, None)
    save_config(config)


def _finish_delete(post):
    """Carry out (or queue) the deletion of whatever this install reported."""
    config = load_config()
    install_id = config.get("install_id")
    if not install_id:
        # No install_id means nothing was ever sent under this install, so
        # there's nothing on any server to go delete.
        _forget_install(config)
        return "nothing"

    url = collector_url()
    if not url:
        if config.get("last_sent"):
            # It did report once, but there is no address to ask now.
            config["pending_delete"] = True
            save_config(config)
            return "pending"
        _forget_install(config)     # never reported, so nothing is stored anywhere
        return "nothing"

    try:
        done = post(url, install_id)
    except (OSError, ValueError, urllib.error.URLError):
        # Covers network failures (OSError/URLError) and a response that
        # can't be parsed as expected (ValueError) - all treated the same
        # way: the deletion didn't go through, try again later.
        done = False
    except Exception:
        # logger.exception() (rather than .error()) also records the full
        # traceback, which is worth having for a failure mode nobody
        # anticipated specifically.
        logger.exception("Unexpected error deleting the reported total")
        done = False

    if done:
        _forget_install(load_config())
        return "deleted"
    config["pending_delete"] = True
    save_config(config)
    return "pending"


def delete_reported_data(post=None):
    """Erase what this install reported, and stop sharing.

    Sharing is switched off first and stays off. Otherwise, with sharing on by
    default, the next launch would send the total again and undo the deletion.

    Returns "deleted", "nothing" (nothing had been shared), or "pending" (the
    collector couldn't be reached; it will be retried automatically).
    """
    paths.load_env()
    config = load_config()
    config["enabled"] = False
    save_config(config)
    return _finish_delete(post or _post_delete)


def retry_pending_delete(post=None):
    """Finish a deletion that couldn't be delivered earlier. True if resolved."""
    if load_config().get("pending_delete") is not True:
        return False
    paths.load_env()    # the collector's address comes from the private .env
    return _finish_delete(post or _post_delete) in ("deleted", "nothing")


def retry_pending_delete_in_background():
    """Retry a queued deletion without making the widget wait on the network.

    Does nothing at all unless one is actually pending, so it is safe to call
    on a timer.
    """
    if load_config().get("pending_delete") is True:
        threading.Thread(target=retry_pending_delete, daemon=True).start()


def report_now(get_wasted=None, post=None, post_delete=None):
    """Send the current total if reporting is on and the total changed.

    Returns True if a report was sent. Never raises: a missing network or a
    down collector must not affect the widget.
    """
    try:
        paths.load_env()
        config = load_config()
        if config.get("pending_delete") is True:
            # A deletion that hasn't gone through comes first, and nothing is
            # reported until it has.
            retry_pending_delete(post_delete)   # None means the real network call
            config = load_config()  # re-read: retry_pending_delete() may have changed it
            if config.get("pending_delete") is True:
                return False  # still couldn't delete - don't report either
        url = collector_url()
        if config.get("enabled") is False or not url:
            return False
        if not config.get("install_id"):
            # First time reporting on this install: mint a random,
            # anonymous ID (a UUID - a long random identifier, not tied
            # to any personal info) to tell repeated reports from this
            # same install apart from everyone else's, without it meaning
            # anything on its own.
            config["install_id"] = str(uuid.uuid4())
            save_config(config)

        if get_wasted is None:
            # Imported here, not at the top of the file, so a caller (like
            # a test) can pass in its own get_wasted function instead and
            # this module never has to import the real database unless
            # it's actually needed.
            from data.database import get_total_wasted as get_wasted
        report = build_report(config["install_id"], get_wasted())

        # Nothing new to say - the total hasn't changed since the last
        # successful report, so sending it again would be a wasted
        # network call with no new information.
        if config.get("last_sent") == [report["total_wasted"]]:
            return False

        if (post or _post)(url, report):
            config["last_sent"] = [report["total_wasted"]]
            save_config(config)
            return True
    except (OSError, ValueError, urllib.error.URLError):
        # Any ordinary network failure - logged, but swallowed rather than
        # raised, because reporting is a nice-to-have, not a required part
        # of the app. See the function's own docstring above.
        logger.warning("Could not report the wasted total")
    except Exception:
        logger.exception("Unexpected error reporting the wasted total")
    return False


def report_in_background():
    """Report on a background thread so the widget never waits on the network."""
    threading.Thread(target=report_now, daemon=True).start()


def _status():
    config = load_config()
    print("Reporting:  ", "ON" if is_enabled() else "off")
    print("Collector:  ", collector_url() or "not set (MONEY_GUILT_COLLECTOR_URL)")
    print("Install ID: ", config.get("install_id") or "none yet")
    if config.get("pending_delete"):
        print("Deletion:    pending, will retry")


if __name__ == "__main__":
    paths.load_env()
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "enable":
        set_enabled(True)
    elif command == "disable":
        set_enabled(False)
    elif command == "delete":
        print({"deleted": "deleted from the collector",
               "nothing": "nothing had been shared",
               "pending": "could not reach the collector; will retry"}[delete_reported_data()])
    elif command == "send":
        print("sent" if report_now() else "nothing sent")
    elif command != "status":
        sys.exit(__doc__)
    _status()
