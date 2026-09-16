#!/usr/bin/env python3
"""Flask app for Plaid Link account linking with proper SDK initialization"""

from flask import Flask, render_template, request, jsonify
from plaid_client import PlaidClient
import logging
import json

app = Flask(__name__)
client = PlaidClient()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.route('/')
def index():
    """Serve the Plaid Link page"""
    try:
        link_token = client.create_link_token()
        logger.info(f"Link token created: {link_token}")
        return render_template('link.html', link_token=link_token)
    except Exception as e:
        logger.error(f"Error creating link token: {e}")
        return f"Error: {e}", 500


@app.route('/api/exchange_token', methods=['POST'])
def exchange_token():
    """Exchange public token for access token"""
    data = request.json
    public_token = data.get('public_token')

    if not public_token:
        return jsonify({'error': 'No public_token provided'}), 400

    try:
        access_token = client.exchange_public_token(public_token)

        # Save access token to file for testing
        with open('access_token.txt', 'w') as f:
            f.write(access_token)

        logger.info(f"Successfully exchanged token. Access token saved.")

        return jsonify({
            'success': True,
            'access_token': access_token,
            'message': 'Account linked successfully!'
        })
    except Exception as e:
        logger.error(f"Error exchanging token: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/link_token', methods=['GET'])
def get_link_token():
    """Get a fresh link token"""
    try:
        link_token = client.create_link_token()
        return jsonify({'link_token': link_token})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


if __name__ == '__main__':
    app.run(debug=True, port=5001)
