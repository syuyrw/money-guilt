"""Security tests for the Plaid link server.

Run with:  python -m unittest test_security -v

Uses a fake Plaid client and a temp token file, so it never contacts Plaid and
never touches the real access_token.txt.
"""
import os
import re
import sys
import tempfile
import unittest

# Dummy credentials go in before the import so the real .env is never used
# (load_dotenv does not override variables that are already set).
os.environ['PLAID_CLIENT_ID'] = 'test-client-id'
os.environ['PLAID_SECRET'] = 'test-secret'
os.environ['PLAID_ENV'] = 'sandbox'

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

import link_app  # noqa: E402

LINK_TOKEN = 'link-sandbox-SECRET-LINK-TOKEN-0123456789'
ACCESS_TOKEN = 'access-sandbox-SECRET-ACCESS-TOKEN-9876543210'
PUBLIC_TOKEN = 'public-sandbox-abcdef12-3456-7890'
LOCAL = 'http://localhost:5001'


class FakePlaid:
    def __init__(self):
        self.exchanges = []
        self.link_error = None
        self.exchange_error = None
        self.link_token = LINK_TOKEN

    def create_link_token(self, *a, **k):
        if self.link_error:
            raise self.link_error
        return self.link_token

    def exchange_public_token(self, public_token):
        self.exchanges.append(public_token)
        if self.exchange_error:
            raise self.exchange_error
        return ACCESS_TOKEN


class LinkServerSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='link_sec_')
        self.token_file = os.path.join(self.tmp, 'access_token.txt')
        self.fake = FakePlaid()
        self._orig = (link_app.client, link_app.TOKEN_FILE)
        link_app.client = self.fake
        link_app.TOKEN_FILE = self.token_file
        link_app.app.config['TESTING'] = True
        self.http = link_app.app.test_client()

    def tearDown(self):
        link_app.client, link_app.TOKEN_FILE = self._orig

    def post(self, body=None, **kwargs):
        return self.http.post('/api/exchange_token', base_url=LOCAL,
                              json=body, **kwargs)

    # ---- tokens stay out of logs and responses
    def test_link_token_is_not_logged(self):
        with self.assertLogs('link_app', level='DEBUG') as logs:
            self.http.get('/', base_url=LOCAL)
        self.assertNotIn(LINK_TOKEN, '\n'.join(logs.output))

    def test_access_token_is_not_logged_or_returned(self):
        with self.assertLogs('link_app', level='DEBUG') as logs:
            res = self.post({'public_token': PUBLIC_TOKEN})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(ACCESS_TOKEN, '\n'.join(logs.output))
        self.assertNotIn(ACCESS_TOKEN, res.get_data(as_text=True))
        self.assertNotIn('access_token', res.get_json())

    def test_access_token_is_still_saved_server_side(self):
        self.post({'public_token': PUBLIC_TOKEN})
        with open(self.token_file) as fh:
            self.assertEqual(fh.read(), ACCESS_TOKEN)

    def test_exchange_errors_hide_details_from_the_browser(self):
        self.fake.exchange_error = RuntimeError('request_id=SECRETDETAIL123')
        with self.assertLogs('link_app', level='ERROR') as logs:
            res = self.post({'public_token': PUBLIC_TOKEN})
        self.assertEqual(res.status_code, 400)
        self.assertNotIn('SECRETDETAIL123', res.get_data(as_text=True))
        self.assertIn('SECRETDETAIL123', '\n'.join(logs.output),
                      'details belong in the server log')

    def test_page_errors_hide_details_from_the_browser(self):
        self.fake.link_error = RuntimeError('client_id=LEAKED')
        res = self.http.get('/', base_url=LOCAL)
        self.assertEqual(res.status_code, 500)
        self.assertNotIn('LEAKED', res.get_data(as_text=True))

    def test_link_token_route_errors_are_generic(self):
        self.fake.link_error = RuntimeError('client_id=LEAKED')
        res = self.http.get('/api/link_token', base_url=LOCAL)
        self.assertEqual(res.status_code, 400)
        self.assertNotIn('LEAKED', res.get_data(as_text=True))

    # ---- input validation
    def test_rejects_non_json(self):
        res = self.http.post('/api/exchange_token', base_url=LOCAL,
                             data='public_token=' + PUBLIC_TOKEN,
                             content_type='application/x-www-form-urlencoded')
        self.assertEqual(res.status_code, 415)
        self.assertEqual(self.fake.exchanges, [])

    def test_rejects_bad_bodies_without_calling_plaid(self):
        bad = [[], 'text', {}, {'public_token': None},
               {'public_token': 12345}, {'public_token': ''},
               {'public_token': 'public-sandbox-x'},
               {'public_token': '../../etc/passwd'},
               {'public_token': 'access-sandbox-abcdef12-3456'},
               {'public_token': PUBLIC_TOKEN + '; DROP TABLE x'},
               {'public_token': 'public-sandbox-' + 'a' * 500}]
        for body in bad:
            res = self.post(body)
            self.assertEqual(res.status_code, 400, body)
        self.assertEqual(self.fake.exchanges, [])

    def test_empty_request_is_refused(self):
        res = self.http.post('/api/exchange_token', base_url=LOCAL)
        self.assertEqual(res.status_code, 415)
        self.assertEqual(self.fake.exchanges, [])

    def test_malformed_json_does_not_crash(self):
        res = self.http.post('/api/exchange_token', base_url=LOCAL,
                             data='{not json', content_type='application/json')
        self.assertEqual(res.status_code, 400)

    def test_valid_token_is_accepted(self):
        for env in ('sandbox', 'development', 'production'):
            res = self.post({'public_token': f'public-{env}-abcdef12-3456'})
            self.assertEqual(res.status_code, 200, env)

    # ---- DNS rebinding
    def test_foreign_host_is_refused_everywhere(self):
        for method, path in [('get', '/'), ('get', '/api/link_token'),
                             ('post', '/api/exchange_token')]:
            kwargs = {'json': {'public_token': PUBLIC_TOKEN}} if method == 'post' else {}
            res = getattr(self.http, method)(
                path, base_url='http://evil.example:5001', **kwargs)
            self.assertEqual(res.status_code, 403, path)
        self.assertEqual(self.fake.exchanges, [])

    def test_wrong_port_is_refused(self):
        res = self.http.get('/', base_url='http://localhost:9999')
        self.assertEqual(res.status_code, 403)

    def test_loopback_hosts_are_served(self):
        for base in ('http://localhost:5001', 'http://127.0.0.1:5001'):
            self.assertEqual(self.http.get('/api/link_token', base_url=base)
                             .status_code, 200, base)

    # ---- output encoding
    def test_link_token_cannot_break_out_of_the_script(self):
        self.fake.link_token = '"};</script><script>alert(1)//'
        html = self.http.get('/', base_url=LOCAL).get_data(as_text=True)
        self.assertNotIn('</script><script>alert(1)', html)
        self.assertIn('\\u003c/script\\u003e', html)

    def test_page_does_not_print_the_token(self):
        with open(os.path.join(PROJECT, 'templates', 'link.html')) as fh:
            src = fh.read()
        self.assertNotRegex(src, r"log\([^)]*\+\s*linkToken")
        self.assertNotIn('JSON.stringify(metadata)', src)

    # ---- server configuration
    def test_debugger_is_not_enabled_in_source(self):
        with open(os.path.join(PROJECT, 'link_app.py')) as fh:
            src = fh.read()
        self.assertNotRegex(src, r'debug\s*=\s*True')
        self.assertRegex(src, r"host\s*=\s*'127\.0\.0\.1'")


if __name__ == '__main__':
    unittest.main()
