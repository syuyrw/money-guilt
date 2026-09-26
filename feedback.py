"""Send Feedback.

The in-app form posts to the collector, which emails the developer; the SMTP
login lives on that server, never in this app or repo. With no collector set,
the menu falls back to a mail draft in the user's own mail app. Neither path
includes any spending data.
"""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import quote

import paths
import telemetry

FEEDBACK_EMAIL = "jordan@moneyguilt.com"
SUBJECT = "Money Guilt feedback"
BODY = ("What's on your mind? (Bug, idea, or anything else.)\n\n\n\n"
        "-- Please don't include account numbers or other private details. --")
MAX_MESSAGE = 2000
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def feedback_url():
    """mailto: link, used when no collector is configured"""
    return f"mailto:{FEEDBACK_EMAIL}?subject={quote(SUBJECT)}&body={quote(BODY)}"


def form_available():
    paths.load_env()
    return telemetry.collector_url() is not None


def validate(message, reply_to):
    """Return an error message, or None if the form can be sent"""
    if not (message or "").strip():
        return "Please write a message first."
    if len(message) > MAX_MESSAGE:
        return f"Please keep it under {MAX_MESSAGE} characters."
    if reply_to and (len(reply_to) > 254 or not EMAIL_RE.match(reply_to)):
        return "That email address doesn't look right. Leave it blank to stay anonymous."
    return None


def _post(url, payload):
    request = urllib.request.Request(
        url + "/feedback", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - scheme checked in collector_url()
        return 200 <= response.status < 300


def send(message, reply_to="", post=_post):
    """Send the form. Returns (ok, error message for the user)."""
    error = validate(message, reply_to)
    if error:
        return False, error
    url = telemetry.collector_url()
    if url is None:
        return False, "Feedback isn't set up on this copy of Money Guilt."
    payload = {"message": message.strip()}
    if reply_to:
        payload["reply_to"] = reply_to.strip()
    try:
        if post(url, payload):
            return True, None
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return False, "Couldn't send it. Check your connection and try again."
