"""Collector: keeps each install's latest wasted total and adds them up.

    COLLECTOR_ADMIN_KEY=<long random string> python3 collector/server.py

POST /feedback from the widgets' Send Feedback form. Saved, then emailed to
               FEEDBACK_TO (default jordan@moneyguilt.com) over SMTP. Set
               SMTP_HOST, SMTP_PORT (587), SMTP_USER and SMTP_PASSWORD on the
               server; without them, feedback is still saved, just not emailed.
POST /report   from the widgets. Stores the latest total per install_id.
POST /delete   from a widget whose user asked to have its data removed. Deletes
               that install_id's row. Always succeeds, even if the id is
               unknown, so calling it twice is harmless and it never reveals
               whether an id exists.
GET  /total    for the owner. Needs the header "X-Admin-Key: <key>". Returns
               the sum across all installs. Not reachable without the key.

Run it behind HTTPS (a reverse proxy or a host that terminates TLS): the widget
refuses plain http except for localhost. Anyone who can reach /report can post
numbers, so treat the total as a statistic, not an accounting record. Keep the
database file owner-only.
"""
import hmac
import os
import re
import smtplib
import sqlite3
import ssl
import time
from collections import defaultdict, deque
from email.message import EmailMessage

from flask import Flask, jsonify, request

DB_PATH = os.getenv("COLLECTOR_DB", "collector.db")
ADMIN_KEY = os.getenv("COLLECTOR_ADMIN_KEY", "")
MAX_TOTAL = 1_000_000_000
INSTALL_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

FEEDBACK_TO = os.getenv("FEEDBACK_TO", "jordan@moneyguilt.com")
MAX_MESSAGE = 2000
FEEDBACK_PER_HOUR = 5  # per address, so the form can't be used to flood the inbox
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
_recent = defaultdict(deque)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # reports are tiny; feedback is at most a few KB


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS installs (
        install_id TEXT PRIMARY KEY, total_wasted REAL NOT NULL,
        wasted_count INTEGER NOT NULL, updated_at REAL NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY, message TEXT NOT NULL, reply_to TEXT,
        emailed INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)""")
    return conn


@app.before_request
def limit_small_routes():
    # Reports and deletions are a few dozen bytes; only feedback may be larger.
    if request.path != "/feedback" and (request.content_length or 0) > 1024:
        return jsonify(error="Too large"), 413


@app.route("/report", methods=["POST"])
def report():
    if not request.is_json:
        return jsonify(error="Expected JSON"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Bad report"), 400

    # Older widgets also sent a "wasted_count"; it is ignored and never stored.
    install_id, total = data.get("install_id"), data.get("total_wasted")
    if not (isinstance(install_id, str) and INSTALL_ID_RE.match(install_id)):
        return jsonify(error="Bad install_id"), 400
    if isinstance(total, bool) or not isinstance(total, (int, float)) \
            or not 0 <= total <= MAX_TOTAL:
        return jsonify(error="Bad total_wasted"), 400

    with _db() as conn:
        conn.execute("""INSERT INTO installs VALUES (?, ?, ?, ?)
            ON CONFLICT(install_id) DO UPDATE SET total_wasted = excluded.total_wasted,
            wasted_count = excluded.wasted_count, updated_at = excluded.updated_at""",
                     (install_id, round(total, 2), 0, time.time()))
    return jsonify(ok=True)


@app.route("/delete", methods=["POST"])
def delete():
    """Remove one install's stored total.

    The install ID is a random UUID known only to that install, so knowing it
    is the authorisation, as it is for /report. Nothing else is accepted.
    """
    if not request.is_json:
        return jsonify(error="Expected JSON"), 415
    data = request.get_json(silent=True)
    install_id = data.get("install_id") if isinstance(data, dict) else None
    if not (isinstance(install_id, str) and INSTALL_ID_RE.match(install_id)):
        return jsonify(error="Bad install_id"), 400

    with _db() as conn:
        removed = conn.execute("DELETE FROM installs WHERE install_id = ?",
                               (install_id,)).rowcount
    return jsonify(ok=True, deleted=removed)


def send_email(message, reply_to):
    """Email one piece of feedback to the developer. Returns True if sent."""
    host = os.getenv("SMTP_HOST")
    user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    if not (host and user and password):
        return False
    mail = EmailMessage()
    mail["Subject"] = "Money Guilt feedback"
    mail["From"] = user
    mail["To"] = FEEDBACK_TO
    if reply_to:
        mail["Reply-To"] = reply_to  # validated: no whitespace, so no header injection
    mail.set_content(message + ("\n\n-- \nReply to: " + reply_to if reply_to else ""))
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            smtp.send_message(mail)
        return True
    except (OSError, smtplib.SMTPException):
        app.logger.exception("Could not email feedback")
        return False


@app.route("/feedback", methods=["POST"])
def feedback():
    if not request.is_json:
        return jsonify(error="Expected JSON"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Bad feedback"), 400
    message, reply_to = data.get("message"), data.get("reply_to") or ""
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
        return jsonify(error="Bad message"), 400
    if not isinstance(reply_to, str) or (reply_to and (
            len(reply_to) > 254 or not EMAIL_RE.match(reply_to))):
        return jsonify(error="Bad reply_to"), 400

    now = time.time()
    recent = _recent[request.remote_addr]
    while recent and now - recent[0] > 3600:
        recent.popleft()
    if len(recent) >= FEEDBACK_PER_HOUR:
        return jsonify(error="Too many messages, try again later"), 429
    recent.append(now)

    message = message.strip()
    with _db() as conn:
        cursor = conn.execute(
            "INSERT INTO feedback (message, reply_to, created_at) VALUES (?, ?, ?)",
            (message, reply_to or None, now))
        if send_email(message, reply_to):
            conn.execute("UPDATE feedback SET emailed = 1 WHERE id = ?", (cursor.lastrowid,))
    return jsonify(ok=True)


@app.route("/total")
def total():
    supplied = request.headers.get("X-Admin-Key", "")
    if not ADMIN_KEY or not hmac.compare_digest(supplied, ADMIN_KEY):
        return jsonify(error="Forbidden"), 403
    with _db() as conn:
        row = conn.execute("SELECT SUM(total_wasted), COUNT(*) "
                           "FROM installs").fetchone()
    return jsonify(total_wasted=round(row[0] or 0, 2),
                   installs=row[1])


if __name__ == "__main__":
    if not ADMIN_KEY:
        raise SystemExit("Set COLLECTOR_ADMIN_KEY first")
    os.umask(0o077)
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5002")))
