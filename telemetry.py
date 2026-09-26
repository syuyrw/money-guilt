"""Optional, opt-in reporting of the total wasted dollars to a collector.

What is sent, and nothing else:
    install_id    a random ID made on first use; not tied to a name or account
    total_wasted  the running total of wasted dollars on this install
    wasted_count  how many transactions that total covers
    version       this report format's version

No merchants, dates, individual amounts, balances or Plaid data ever leave the
machine. The collector keeps the latest total per install_id, so repeated
reports never double count.

On by default, and easy to turn off: the tray menu's "Share Anonymous Wasted
Total" item, or python3 telemetry.py disable. The first launch shows a notice
saying what is shared. Nothing is sent unless a collector is set:
    MONEY_GUILT_COLLECTOR_URL=https://... in the private .env (paths.env_path())

    python3 telemetry.py status | enable | disable | send
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

import paths

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
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return url
    return None


def build_report(install_id, wasted):
    return {
        "install_id": install_id,
        "total_wasted": round(float(wasted["total"]), 2),
        "wasted_count": int(wasted["count"]),
        "version": REPORT_VERSION,
    }


def _post(url, report):
    request = urllib.request.Request(
        url + "/report", data=json.dumps(report).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - scheme checked in collector_url()
        return 200 <= response.status < 300


def report_now(get_wasted=None, post=_post):
    """Send the current total if reporting is on and the total changed.

    Returns True if a report was sent. Never raises: a missing network or a
    down collector must not affect the widget.
    """
    try:
        paths.load_env()
        config = load_config()
        url = collector_url()
        if config.get("enabled") is False or not url:
            return False
        if not config.get("install_id"):
            config["install_id"] = str(uuid.uuid4())
            save_config(config)

        if get_wasted is None:
            from database import get_total_wasted as get_wasted
        report = build_report(config["install_id"], get_wasted())

        # Nothing new to say
        if config.get("last_sent") == [report["total_wasted"], report["wasted_count"]]:
            return False

        if post(url, report):
            config["last_sent"] = [report["total_wasted"], report["wasted_count"]]
            save_config(config)
            return True
    except (OSError, ValueError, urllib.error.URLError):
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


if __name__ == "__main__":
    paths.load_env()
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "enable":
        set_enabled(True)
    elif command == "disable":
        set_enabled(False)
    elif command == "send":
        print("sent" if report_now() else "nothing sent")
    elif command != "status":
        sys.exit(__doc__)
    _status()
