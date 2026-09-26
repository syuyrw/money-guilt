"""Tests for plaid_client: the environment guard and disconnecting a bank.

Run with:  python -m unittest test_plaid_client -v

No test contacts Plaid. Dummy credentials are set before the import so the real
.env is never read (load_dotenv doesn't override variables already set).
"""
import json
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("PLAID_CLIENT_ID", "test-client-id")
os.environ.setdefault("PLAID_SECRET", "test-secret")

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

import plaid  # noqa: E402
import plaid_client  # noqa: E402

SANDBOX_URL = "https://sandbox.plaid.com"
PRODUCTION_URL = "https://production.plaid.com"


def make_client(env_value):
    env = {"PLAID_CLIENT_ID": "cid", "PLAID_SECRET": "secret"}
    if env_value is not None:
        env["PLAID_ENV"] = env_value
    with mock.patch.dict(os.environ, env, clear=False):
        if env_value is None:
            os.environ.pop("PLAID_ENV", None)
        return plaid_client.PlaidClient()


def host_of(client):
    return client.client.api_client.configuration.host


class EnvironmentGuard(unittest.TestCase):
    def test_unset_means_sandbox(self):
        client = make_client(None)
        self.assertEqual(client.plaid_env, "sandbox")
        self.assertEqual(host_of(client), SANDBOX_URL)

    def test_sandbox_and_production_go_to_the_right_place(self):
        self.assertEqual(host_of(make_client("sandbox")), SANDBOX_URL)
        self.assertEqual(host_of(make_client("production")), PRODUCTION_URL)

    def test_case_and_stray_spaces_are_tolerated(self):
        for value in ("SANDBOX", " sandbox ", "Sandbox\n"):
            self.assertEqual(host_of(make_client(value)), SANDBOX_URL, repr(value))
        for value in ("PRODUCTION", " Production "):
            self.assertEqual(host_of(make_client(value)), PRODUCTION_URL, repr(value))

    def test_anything_else_is_refused_never_treated_as_production(self):
        """Regression: any value other than exactly "sandbox" meant production."""
        for value in ("prod", "produciton", "sandbx", "development", "dev", "",
                      "   ", "sandbox;production", "sandbox production", "live",
                      "true", "0"):
            with self.assertRaises(ValueError, msg=repr(value)) as raised:
                make_client(value)
            self.assertIn("sandbox", str(raised.exception))
            self.assertIn("production", str(raised.exception))

    def test_missing_credentials_are_still_an_error(self):
        with mock.patch.dict(os.environ, {"PLAID_CLIENT_ID": "", "PLAID_SECRET": ""}):
            with self.assertRaises(ValueError):
                plaid_client.PlaidClient()

    def test_production_is_announced_loudly_and_sandbox_is_not(self):
        with self.assertLogs("plaid_client", level="INFO") as logs:
            make_client("production")
        self.assertTrue(any("PRODUCTION" in line and "WARNING" in line
                            for line in logs.output), logs.output)
        with self.assertLogs("plaid_client", level="INFO") as logs:
            make_client("sandbox")
        self.assertFalse(any("WARNING" in line for line in logs.output))

    def test_the_parser_itself(self):
        self.assertEqual(plaid_client.parse_environment(None), "sandbox")
        self.assertEqual(plaid_client.parse_environment("Production"), "production")
        with self.assertRaises(ValueError):
            plaid_client.parse_environment("prod")


def api_error(code=None, status=400, body=None):
    exc = plaid.ApiException(status=status, reason="error")
    exc.body = body if body is not None else (
        json.dumps({"error_code": code}) if code else None)
    return exc


class RemoveItem(unittest.TestCase):
    def setUp(self):
        self.client = make_client("sandbox")
        self.calls = []

    def stub(self, result=None, error=None):
        def item_remove(request):
            self.calls.append(request.access_token)
            if error:
                raise error
            return result
        self.client.client = mock.Mock(item_remove=item_remove)

    def test_success_sends_the_token_and_returns_true(self):
        self.stub(result={"request_id": "x"})
        self.assertTrue(self.client.remove_item("access-token-value"))
        self.assertEqual(self.calls, ["access-token-value"])

    def test_a_connection_plaid_no_longer_knows_counts_as_removed(self):
        for code in ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"):
            self.stub(error=api_error(code))
            self.assertTrue(self.client.remove_item("t"), code)

    def test_any_other_plaid_error_is_raised_so_the_token_is_kept(self):
        for code in ("INTERNAL_SERVER_ERROR", "RATE_LIMIT_EXCEEDED", "INVALID_API_KEYS", None):
            self.stub(error=api_error(code, status=500))
            with self.assertRaises(plaid.ApiException, msg=str(code)):
                self.client.remove_item("t")

    def test_an_unreadable_error_body_is_raised_not_swallowed(self):
        for body in ("not json", "", "[]", None, b"\xff"):
            self.stub(error=api_error(body=body, status=502))
            with self.assertRaises(plaid.ApiException, msg=repr(body)):
                self.client.remove_item("t")

    def test_network_errors_propagate(self):
        self.stub(error=OSError("no route"))
        with self.assertRaises(OSError):
            self.client.remove_item("t")

    def test_the_token_is_never_logged(self):
        self.stub(error=api_error("INTERNAL_SERVER_ERROR", status=500))
        with self.assertLogs("plaid_client", level="DEBUG") as logs:
            with self.assertRaises(plaid.ApiException):
                self.client.remove_item("access-sandbox-SECRETTOKEN")
        self.assertNotIn("SECRETTOKEN", "\n".join(logs.output))

    def test_error_code_reader(self):
        self.assertEqual(plaid_client.error_code(api_error("ITEM_NOT_FOUND")), "ITEM_NOT_FOUND")
        for bad in (api_error(body="x"), api_error(body=None), ValueError("no body")):
            self.assertIsNone(plaid_client.error_code(bad))


if __name__ == "__main__":
    unittest.main()
