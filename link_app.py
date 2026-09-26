#!/usr/bin/env python3
"""Flask app for Plaid Link account linking with proper SDK initialization"""

from flask import Flask, render_template, request, jsonify
from plaid_client import PlaidClient
import logging
import os
import re

PORT = 5001
# Only these Host headers are served. A web page can point its own domain at
# 127.0.0.1 (DNS rebinding) and then talk to this server from the browser;
# such requests still carry the attacker's hostname, so refusing unknown
# hosts shuts that route.
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'access_token.txt')
PUBLIC_TOKEN_RE = re.compile(r'^public-(sandbox|development|production)-[0-9a-zA-Z-]{8,128}$')

app = Flask(__name__, static_folder='static', static_url_path='/static')
client = PlaidClient()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.before_request
def reject_unknown_hosts():
    if request.host not in ALLOWED_HOSTS:
        logger.warning("Rejected request with unexpected Host header")
        return "Forbidden", 403


@app.route('/')
def index():
    """Serve the Plaid Link page"""
    try:
        link_token = client.create_link_token()
        # The token itself is never logged.
        logger.info("Link token created")
        return render_template('link.html', link_token=link_token)
    except Exception as e:
        logger.error(f"Error creating link token: {e}")
        return "Could not start account linking. See the server log.", 500


@app.route('/api/exchange_token', methods=['POST'])
def exchange_token():
    """Exchange public token for access token"""
    # A JSON content type can't be sent cross-site without a CORS preflight,
    # so requiring it keeps other websites from posting here from a browser.
    if not request.is_json:
        return jsonify({'error': 'Expected JSON'}), 415

    data = request.get_json(silent=True)
    public_token = data.get('public_token') if isinstance(data, dict) else None

    if not isinstance(public_token, str) or not PUBLIC_TOKEN_RE.match(public_token):
        return jsonify({'error': 'Missing or malformed public_token'}), 400

    try:
        access_token = client.exchange_public_token(public_token)

        # Save access token to file for testing
        with open(TOKEN_FILE, 'w') as f:
            f.write(access_token)

        logger.info("Successfully exchanged token. Access token saved.")

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
    app.run(host='127.0.0.1', port=PORT, debug=os.getenv('FLASK_DEBUG') == '1')
