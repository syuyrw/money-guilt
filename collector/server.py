"""Collector: keeps each install's latest wasted total and adds them up.

    COLLECTOR_ADMIN_KEY=<long random string> python3 collector/server.py

POST /report   from the widgets. Stores the latest total per install_id.
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
import sqlite3
import time

from flask import Flask, jsonify, request

DB_PATH = os.getenv("COLLECTOR_DB", "collector.db")
ADMIN_KEY = os.getenv("COLLECTOR_ADMIN_KEY", "")
MAX_TOTAL = 1_000_000_000
INSTALL_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024  # a report is a few dozen bytes


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS installs (
        install_id TEXT PRIMARY KEY, total_wasted REAL NOT NULL,
        wasted_count INTEGER NOT NULL, updated_at REAL NOT NULL)""")
    return conn


@app.route("/report", methods=["POST"])
def report():
    if not request.is_json:
        return jsonify(error="Expected JSON"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Bad report"), 400

    install_id, total, count = (data.get("install_id"), data.get("total_wasted"),
                                data.get("wasted_count"))
    if not (isinstance(install_id, str) and INSTALL_ID_RE.match(install_id)):
        return jsonify(error="Bad install_id"), 400
    if isinstance(total, bool) or not isinstance(total, (int, float)) \
            or not 0 <= total <= MAX_TOTAL:
        return jsonify(error="Bad total_wasted"), 400
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 10_000_000:
        return jsonify(error="Bad wasted_count"), 400

    with _db() as conn:
        conn.execute("""INSERT INTO installs VALUES (?, ?, ?, ?)
            ON CONFLICT(install_id) DO UPDATE SET total_wasted = excluded.total_wasted,
            wasted_count = excluded.wasted_count, updated_at = excluded.updated_at""",
                     (install_id, round(total, 2), count, time.time()))
    return jsonify(ok=True)


@app.route("/total")
def total():
    supplied = request.headers.get("X-Admin-Key", "")
    if not ADMIN_KEY or not hmac.compare_digest(supplied, ADMIN_KEY):
        return jsonify(error="Forbidden"), 403
    with _db() as conn:
        row = conn.execute("SELECT SUM(total_wasted), SUM(wasted_count), COUNT(*) "
                           "FROM installs").fetchone()
    return jsonify(total_wasted=round(row[0] or 0, 2),
                   wasted_count=row[1] or 0, installs=row[2])


if __name__ == "__main__":
    if not ADMIN_KEY:
        raise SystemExit("Set COLLECTOR_ADMIN_KEY first")
    os.umask(0o077)
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5002")))
