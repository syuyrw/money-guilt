"""Tests for the opt-in wasted-total reporting and its collector."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "collector"))

DATA_DIR = tempfile.mkdtemp()
os.environ["MONEY_GUILD_DATA_DIR"] = DATA_DIR

import telemetry  # noqa: E402

GOOD_ID = "123e4567-e89b-12d3-a456-426614174000"


class Telemetry(unittest.TestCase):
    def setUp(self):
        for name in os.listdir(DATA_DIR):
            os.remove(os.path.join(DATA_DIR, name))
        os.environ["MONEY_GUILT_COLLECTOR_URL"] = "https://collector.example"
        self.sent = []

    def post(self, url, report):
        self.sent.append((url, report))
        return True

    def wasted(self):
        return {"total": 317.194, "count": 9}

    def test_off_by_default_sends_nothing(self):
        self.assertFalse(telemetry.report_now(self.wasted, self.post))
        self.assertEqual(self.sent, [])

    def test_enabled_but_no_collector_sends_nothing(self):
        telemetry.set_enabled(True)
        del os.environ["MONEY_GUILT_COLLECTOR_URL"]
        self.assertFalse(telemetry.report_now(self.wasted, self.post))
        self.assertEqual(self.sent, [])

    def test_report_holds_only_the_total_count_and_random_id(self):
        telemetry.set_enabled(True)
        self.assertTrue(telemetry.report_now(self.wasted, self.post))
        url, report = self.sent[0]
        self.assertEqual(url, "https://collector.example")
        self.assertEqual(set(report), {"install_id", "total_wasted", "wasted_count", "version"})
        self.assertEqual(report["total_wasted"], 317.19)
        self.assertEqual(report["wasted_count"], 9)

    def test_unchanged_total_is_not_sent_twice_but_a_change_is(self):
        telemetry.set_enabled(True)
        telemetry.report_now(self.wasted, self.post)
        self.assertFalse(telemetry.report_now(self.wasted, self.post))
        self.assertTrue(telemetry.report_now(lambda: {"total": 400, "count": 10}, self.post))
        self.assertEqual(len(self.sent), 2)

    def test_failed_send_is_retried_and_never_raises(self):
        telemetry.set_enabled(True)
        def boom(url, report):
            raise OSError("no network")
        self.assertFalse(telemetry.report_now(self.wasted, boom))
        self.assertTrue(telemetry.report_now(self.wasted, self.post))

    def test_install_id_is_stable_and_disable_stops_reports(self):
        telemetry.set_enabled(True)
        first = telemetry.load_config()["install_id"]
        telemetry.set_enabled(False)
        telemetry.set_enabled(True)
        self.assertEqual(telemetry.load_config()["install_id"], first)
        telemetry.set_enabled(False)
        self.assertFalse(telemetry.report_now(self.wasted, self.post))

    def test_collector_url_must_be_https_or_localhost(self):
        cases = {"https://a.example": "https://a.example",
                 "https://a.example/": "https://a.example",
                 "http://localhost:5002": "http://localhost:5002",
                 "http://evil.example": None, "ftp://a.example": None,
                 "file:///etc/passwd": None, "": None}
        for value, expected in cases.items():
            os.environ["MONEY_GUILT_COLLECTOR_URL"] = value
            self.assertEqual(telemetry.collector_url(), expected, value)

    def test_config_file_is_owner_only(self):
        telemetry.set_enabled(True)
        mode = os.stat(os.path.join(DATA_DIR, "telemetry.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)


class Collector(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        server.DB_PATH = os.path.join(tempfile.mkdtemp(), "c.db")
        server.ADMIN_KEY = "secret-key"
        self.client = server.app.test_client()

    def report(self, install_id=GOOD_ID, total=10.5, count=2):
        return self.client.post("/report", json={
            "install_id": install_id, "total_wasted": total, "wasted_count": count})

    def total(self, key="secret-key"):
        return self.client.get("/total", headers={"X-Admin-Key": key})

    def test_totals_add_up_across_installs(self):
        self.report(GOOD_ID, 100, 3)
        self.report("223e4567-e89b-12d3-a456-426614174000", 50.25, 2)
        body = self.total().get_json()
        self.assertEqual(body, {"total_wasted": 150.25, "wasted_count": 5, "installs": 2})

    def test_repeat_reports_replace_instead_of_adding(self):
        self.report(GOOD_ID, 100, 3)
        self.report(GOOD_ID, 120, 4)
        self.assertEqual(self.total().get_json()["total_wasted"], 120)

    def test_total_needs_the_admin_key(self):
        self.report()
        self.assertEqual(self.total("wrong").status_code, 403)
        self.assertEqual(self.client.get("/total").status_code, 403)
        self.server.ADMIN_KEY = ""
        self.assertEqual(self.total("").status_code, 403)

    def test_bad_reports_are_rejected(self):
        for kwargs in ({"install_id": "nope"}, {"total": -1}, {"total": 1e12},
                       {"total": True}, {"total": "5"}, {"count": -1}, {"count": 1.5}):
            self.assertEqual(self.report(**kwargs).status_code, 400, kwargs)
        self.assertEqual(self.client.post("/report", data="x").status_code, 415)
        self.assertEqual(self.client.post("/report", json=[1]).status_code, 400)
        self.assertEqual(self.client.post("/report", json={}).status_code, 400)

    def test_oversized_body_is_refused(self):
        response = self.client.post("/report", data=json.dumps({"x": "a" * 5000}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
