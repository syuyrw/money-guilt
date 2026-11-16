#!/usr/bin/env python3
"""Flask app for Plaid Link account linking with proper SDK initialization

This is a tiny, local-only web server (Flask is a lightweight Python web
framework). Its only job is to show the Plaid Link widget in a browser so
you can log into your bank, and then hand the resulting token off to
secure_store.py to be saved. It's never meant to be exposed to the
internet - see ALLOWED_HOSTS below.
"""

import logging
import os
import re
import sys

# So this still works when run directly (python3 banking/link_app.py),
# where Python would otherwise only put banking/ itself on sys.path, not
# the project root the `banking` package below needs to be importable from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify
from banking.plaid_client import PlaidClient
from banking import secure_store

PORT = 5001
# Only these Host headers are served. A web page can point its own domain at
# 127.0.0.1 (DNS rebinding) and then talk to this server from the browser;
# such requests still carry the attacker's hostname, so refusing unknown
# hosts shuts that route.
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
# A regular expression describing exactly what a legitimate Plaid public
# token looks like (e.g. "public-sandbox-abc123..."). Anything that doesn't
# match this shape is rejected before it's ever passed along to Plaid.
PUBLIC_TOKEN_RE = re.compile(r'^public-(sandbox|development|production)-[0-9a-zA-Z-]{8,128}$')

# `Flask(__name__, ...)` creates the web app itself. static_folder/
# static_url_path tell it where to find plain files like link.css -
# Flask serves anything in that folder automatically at /static/....
app = Flask(__name__, static_folder='static', static_url_path='/static')
# One shared Plaid client for the whole server's lifetime - it holds the
# credentials needed to talk to Plaid, read once at startup.
client = PlaidClient()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# @app.before_request registers this function to run before every single
# request this server handles, no matter which route it's for.
@app.before_request
def reject_unknown_hosts():
    if request.host not in ALLOWED_HOSTS:
        logger.warning("Rejected request with unexpected Host header")
        return "Forbidden", 403
    # Returning nothing here (implicitly None) tells Flask "let the request
    # continue on to its normal route handler".


# @app.route maps a URL path to the function below it. Visiting
# http://127.0.0.1:5001/ in a browser runs this function.
@app.route('/')
def index():
    """Serve the Plaid Link page"""
    try:
        link_token = client.create_link_token()
        # The token itself is never logged.
        logger.info("Link token created")
        # render_template fills in templates/link.html with this value
        # (accessible there as {{ link_token }}) and returns the finished
        # HTML page as the response.
        return render_template('link.html', link_token=link_token)
    except Exception as e:
        logger.error(f"Error creating link token: {e}")
        # The browser only ever sees this generic message - the real
        # exception detail stays in the server's own log, never in the
        # response, so it can't leak anything sensitive to the page.
        return "Could not start account linking. See the server log.", 500


@app.route('/api/exchange_token', methods=['POST'])
def exchange_token():
    """Exchange public token for access token"""
    # A JSON content type can't be sent cross-site without a CORS preflight,
    # so requiring it keeps other websites from posting here from a browser.
    if not request.is_json:
        return jsonify({'error': 'Expected JSON'}), 415

    # `silent=True` means a malformed JSON body returns None here instead
    # of raising an exception.
    data = request.get_json(silent=True)
    public_token = data.get('public_token') if isinstance(data, dict) else None

    if not isinstance(public_token, str) or not PUBLIC_TOKEN_RE.match(public_token):
        return jsonify({'error': 'Missing or malformed public_token'}), 400

    try:
        access_token = client.exchange_public_token(public_token)

        # Kept in the system keychain rather than a file on disk.
        secure_store.set_access_token(access_token)
        secure_store.harden_data_files()

        logger.info("Successfully exchanged token. Access token saved to the keychain.")

        # The access token is a live credential for the linked account and
        # must stay server-side. It is intentionally left out of this
        # response so it can't end up in browser console logs or dev tools.
        return jsonify({
            'success': True,
            'message': 'Account linked successfully!'
        })
    except Exception as e:
        logger.error(f"Error exchanging token: {e}")
        # Exception text can carry request details; keep it in the log.
        return jsonify({'error': 'Could not link the account. See the server log.'}), 400


@app.route('/api/link_token', methods=['GET'])
def get_link_token():
    """Get a fresh link token"""
    try:
        link_token = client.create_link_token()
        return jsonify({'link_token': link_token})
    except Exception as e:
        logger.error(f"Error creating link token: {e}")
        return jsonify({'error': 'Could not create a link token. See the server log.'}), 400


if __name__ == '__main__':
    # Werkzeug's debugger allows code execution from the browser, so it stays
    # off unless asked for: FLASK_DEBUG=1 python link_app.py
    #
    # host='127.0.0.1' means this only accepts connections from this same
    # machine - it is never reachable from the network or the internet.
    app.run(host='127.0.0.1', port=PORT, debug=os.getenv('FLASK_DEBUG') == '1')
