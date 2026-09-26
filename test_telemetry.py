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

    def test_on_by_default_when_a_collector_is_set(self):
        self.assertTrue(telemetry.is_enabled())
        self.assertTrue(telemetry.report_now(self.wasted, self.post))
        self.assertEqual(len(self.sent), 1)

    def test_opting_out_sends_nothing(self):
        telemetry.set_enabled(False)
        self.assertFalse(telemetry.is_enabled())
        self.assertFalse(telemetry.report_now(self.wasted, self.post))
        self.assertEqual(self.sent, [])

    def test_notice_shows_until_marked(self):
        self.assertTrue(telemetry.needs_notice())
        telemetry.mark_notice_shown()
        self.assertFalse(telemetry.needs_notice())

    def test_no_collector_sends_nothing(self):
        del os.environ["MONEY_GUILT_COLLECTOR_URL"]
        self.assertFalse(telemetry.report_now(self.wasted, self.post))
        self.assertEqual(self.sent, [])

    def test_report_holds_only_the_total_count_and_random_id(self):
        self.assertTrue(telemetry.report_now(self.wasted, self.post))
        url, report = self.sent[0]
        self.assertEqual(url, "https://collector.example")
        self.assertEqual(set(report), {"install_id", "total_wasted", "wasted_count", "version"})
        self.assertEqual(report["total_wasted"], 317.19)
        self.assertEqual(report["wasted_count"], 9)

    def test_unchanged_total_is_not_sent_twice_but_a_change_is(self):
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
        telemetry.report_now(self.wasted, self.post)
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
        telemetry.set_enabled(False)
        mode = os.stat(os.path.join(DATA_DIR, "telemetry.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)


class Deletion(unittest.TestCase):
    def setUp(self):
        for name in os.listdir(DATA_DIR):
            os.remove(os.path.join(DATA_DIR, name))
        os.environ["MONEY_GUILT_COLLECTOR_URL"] = "https://collector.example"
        self.sent = []
        self.deleted = []

    def post(self, url, report):
        self.sent.append(report)
        return True

    def post_delete(self, url, install_id):
        self.deleted.append((url, install_id))
        return True

    def wasted(self):
        return {"total": 317.194, "count": 9}

    def share(self):
        self.assertTrue(telemetry.report_now(self.wasted, self.post))
        return telemetry.load_config()["install_id"]

    def test_deleting_asks_the_collector_for_this_installs_id(self):
        install_id = self.share()
        self.assertEqual(telemetry.delete_reported_data(self.post_delete), "deleted")
        self.assertEqual(self.deleted, [("https://collector.example", install_id)])

    def test_deleting_turns_sharing_off_so_the_next_launch_cannot_undo_it(self):
        self.share()
        telemetry.delete_reported_data(self.post_delete)
        self.assertFalse(telemetry.is_enabled())
        self.assertFalse(telemetry.report_now(self.wasted, self.post))
        self.assertEqual(len(self.sent), 1, "nothing may be sent after a deletion")

    def test_deleting_forgets_the_install_so_later_sharing_is_unlinked(self):
        first = self.share()
        telemetry.delete_reported_data(self.post_delete)
        config = telemetry.load_config()
        for key in ("install_id", "last_sent", "pending_delete"):
            self.assertNotIn(key, config)
        telemetry.set_enabled(True)
        telemetry.report_now(self.wasted, self.post)
        self.assertNotEqual(telemetry.load_config()["install_id"], first)

    def test_deleting_when_nothing_was_shared_says_so(self):
        self.assertEqual(telemetry.delete_reported_data(self.post_delete), "nothing")
        self.assertEqual(self.deleted, [])
        self.assertFalse(telemetry.is_enabled(), "it still opts out")

    def test_an_unreachable_collector_queues_the_deletion(self):
        self.share()
        self.assertEqual(telemetry.delete_reported_data(lambda u, i: False), "pending")
        self.assertTrue(telemetry.status()["delete_pending"])
        self.assertFalse(telemetry.is_enabled())
        self.assertIn("install_id", telemetry.load_config(),
                      "the id is kept so the retry knows what to delete")

    def test_a_network_error_is_queued_not_raised(self):
        import urllib.error
        self.share()

        def down(url, install_id):
            raise urllib.error.URLError("no route")
        self.assertEqual(telemetry.delete_reported_data(down), "pending")

    def test_an_unexpected_error_is_queued_not_raised(self):
        self.share()

        def broken(url, install_id):
            raise RuntimeError("boom")
        self.assertEqual(telemetry.delete_reported_data(broken), "pending")

    def test_a_queued_deletion_is_retried_and_completes(self):
        install_id = self.share()
        telemetry.delete_reported_data(lambda u, i: False)
        self.assertTrue(telemetry.retry_pending_delete(self.post_delete))
        self.assertEqual(self.deleted, [("https://collector.example", install_id)])
        self.assertFalse(telemetry.status()["delete_pending"])
        self.assertNotIn("install_id", telemetry.load_config())

    def test_background_retry_starts_a_thread_only_when_a_deletion_is_pending(self):
        from unittest import mock
        with mock.patch.object(telemetry.threading, "Thread") as thread:
            telemetry.retry_pending_delete_in_background()
            thread.assert_not_called()          # nothing pending: a free no-op
            self.share()
            telemetry.delete_reported_data(lambda u, i: False)
            telemetry.retry_pending_delete_in_background()
            self.assertEqual(thread.call_count, 1)
            self.assertIs(thread.call_args.kwargs["target"], telemetry.retry_pending_delete)
            self.assertTrue(thread.call_args.kwargs["daemon"], "must not hold the app open")

    def test_nothing_to_retry_does_nothing(self):
        self.assertFalse(telemetry.retry_pending_delete(self.post_delete))
        self.assertEqual(self.deleted, [])

    def test_nothing_is_reported_while_a_deletion_is_still_pending(self):
        self.share()
        telemetry.delete_reported_data(lambda u, i: False)
        telemetry.set_enabled(True)          # the user changes their mind
        # A different total, so the only thing that can stop this report is the
        # pending deletion (an unchanged total would be skipped regardless).
        changed = lambda: {"total": 999.0, "count": 12}
        self.assertFalse(telemetry.report_now(changed, self.post,
                                              post_delete=lambda u, i: False))
        self.assertEqual(len(self.sent), 1, "still blocked: the old total is on the server")

    def test_reporting_resumes_after_the_pending_deletion_goes_through(self):
        first = self.share()
        telemetry.delete_reported_data(lambda u, i: False)
        telemetry.set_enabled(True)
        self.assertTrue(telemetry.report_now(self.wasted, self.post,
                                             post_delete=self.post_delete))
        self.assertEqual(self.deleted[0][1], first, "the old data is deleted first")
        self.assertNotEqual(self.sent[-1]["install_id"], first, "then a fresh id is used")

    def test_no_collector_but_it_did_report_once_stays_pending(self):
        self.share()
        os.environ.pop("MONEY_GUILT_COLLECTOR_URL")
        self.assertEqual(telemetry.delete_reported_data(self.post_delete), "pending")

    def test_no_collector_and_never_reported_is_nothing(self):
        config = telemetry.load_config()
        config["install_id"] = "123e4567-e89b-12d3-a456-426614174000"
        telemetry.save_config(config)
        os.environ.pop("MONEY_GUILT_COLLECTOR_URL")
        self.assertEqual(telemetry.delete_reported_data(self.post_delete), "nothing")

    def test_the_delete_request_carries_only_the_install_id(self):
        from unittest import mock
        seen = {}

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["body"] = json.loads(request.data)
            return Response()
        with mock.patch.object(telemetry.urllib.request, "urlopen", fake_urlopen):
            self.assertTrue(telemetry._post_delete("https://collector.example", GOOD_ID))
        self.assertEqual(seen["url"], "https://collector.example/delete")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["body"], {"install_id": GOOD_ID})

    def test_status_describes_the_state(self):
        self.assertEqual(telemetry.status(), {
            "enabled": True, "collector_configured": True,
            "has_shared": False, "delete_pending": False})
        self.share()
        self.assertTrue(telemetry.status()["has_shared"])
        os.environ.pop("MONEY_GUILT_COLLECTOR_URL")
        self.assertFalse(telemetry.status()["collector_configured"])

    def test_the_config_stays_owner_only_through_a_deletion(self):
        self.share()
        telemetry.delete_reported_data(lambda u, i: False)
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


    def delete(self, install_id=GOOD_ID):
        return self.client.post("/delete", json={"install_id": install_id})

    def test_delete_removes_that_installs_total(self):
        self.report(GOOD_ID, 100, 3)
        other = "223e4567-e89b-12d3-a456-426614174000"
        self.report(other, 50, 2)
        response = self.delete(GOOD_ID)
        self.assertEqual((response.status_code, response.get_json()),
                         (200, {"ok": True, "deleted": 1}))
        self.assertEqual(self.total().get_json(),
                         {"total_wasted": 50, "wasted_count": 2, "installs": 1},
                         "the other install must be untouched")

    def test_delete_of_an_unknown_id_succeeds_and_is_repeatable(self):
        self.assertEqual(self.delete().get_json(), {"ok": True, "deleted": 0})
        self.report()
        self.assertEqual(self.delete().get_json()["deleted"], 1)
        self.assertEqual(self.delete().get_json()["deleted"], 0)

    def test_delete_rejects_malformed_requests(self):
        for body in ({}, {"install_id": "nope"}, {"install_id": 5},
                     {"install_id": None}, {"install_id": GOOD_ID + "x"},
                     {"install_id": "%"}, {"install_id": "' OR 1=1 --"}):
            self.assertEqual(self.client.post("/delete", json=body).status_code,
                             400, body)
        self.assertEqual(self.client.post("/delete", json=[GOOD_ID]).status_code, 400)
        self.assertEqual(self.client.post("/delete", data="x").status_code, 415)

    def test_a_wildcard_cannot_delete_everyone(self):
        self.report(GOOD_ID, 10, 1)
        self.report("223e4567-e89b-12d3-a456-426614174000", 20, 1)
        self.client.post("/delete", json={"install_id": "%"})
        self.client.post("/delete", json={"install_id": "%%%%%%%%-%%%%-%%%%-%%%%-%%%%%%%%%%%%"})
        self.assertEqual(self.total().get_json()["installs"], 2)

    def test_delete_only_allows_post(self):
        self.assertEqual(self.client.get("/delete").status_code, 405)

    def test_oversized_delete_is_refused(self):
        response = self.client.post("/delete", data=json.dumps({"x": "a" * 5000}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 413)


class Feedback(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        server.DB_PATH = os.path.join(tempfile.mkdtemp(), "f.db")
        server._recent.clear()
        self.sent_mail = []
        server.send_email = lambda message, reply_to: self.sent_mail.append((message, reply_to)) or True
        self.client = server.app.test_client()

    def post(self, **body):
        return self.client.post("/feedback", json=body)

    def test_feedback_is_saved_and_emailed(self):
        self.assertEqual(self.post(message="  Love it  ", reply_to="a@b.co").status_code, 200)
        self.assertEqual(self.sent_mail, [("Love it", "a@b.co")])

    def test_reply_address_is_optional(self):
        self.assertEqual(self.post(message="hi").status_code, 200)
        self.assertEqual(self.sent_mail, [("hi", "")])

    def test_bad_feedback_is_rejected(self):
        for body in ({}, {"message": ""}, {"message": "  "}, {"message": 5},
                     {"message": "x" * 2001}, {"message": "hi", "reply_to": "nope"},
                     {"message": "hi", "reply_to": "a@b.co\nBcc: x@y.co"},
                     {"message": "hi", "reply_to": 5}):
            self.assertEqual(self.post(**body).status_code, 400, body)
        self.assertEqual(self.sent_mail, [])

    def test_it_is_rate_limited(self):
        codes = [self.post(message="hi").status_code for _ in range(7)]
        self.assertEqual(codes, [200] * 5 + [429] * 2)

    def test_an_email_failure_still_saves_the_message(self):
        self.server.send_email = lambda message, reply_to: False
        self.assertEqual(self.post(message="hi").status_code, 200)
        with self.server._db() as conn:
            row = conn.execute("SELECT message, emailed FROM feedback").fetchone()
        self.assertEqual(row, ("hi", 0))

    def test_no_smtp_settings_means_no_email_attempt(self):
        import importlib
        importlib.reload(self.server)
        for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
            os.environ.pop(name, None)
        self.assertFalse(self.server.send_email("hi", ""))

    def test_reports_stay_small_but_feedback_may_be_larger(self):
        big = json.dumps({"message": "a" * 1500})
        self.assertEqual(self.client.post("/feedback", data=big,
                         content_type="application/json").status_code, 200)
        self.assertEqual(self.client.post("/report", data=big,
                         content_type="application/json").status_code, 413)


class FeedbackClient(unittest.TestCase):
    def setUp(self):
        os.environ["MONEY_GUILT_COLLECTOR_URL"] = "https://collector.example"

    def test_sends_message_and_optional_reply_address(self):
        import feedback
        seen = []
        ok = feedback.send("  hello ", "a@b.co", post=lambda url, p: seen.append((url, p)) or True)
        self.assertEqual(ok, (True, None))
        self.assertEqual(seen, [("https://collector.example", {"message": "hello", "reply_to": "a@b.co"})])
        seen.clear()
        feedback.send("hello", "", post=lambda url, p: seen.append(p) or True)
        self.assertEqual(seen, [{"message": "hello"}])

    def test_validation_and_failures_give_a_message_not_an_exception(self):
        import feedback
        self.assertFalse(feedback.send("", "")[0])
        self.assertFalse(feedback.send("hi", "not an email")[0])
        def boom(url, payload):
            raise OSError("down")
        ok, error = feedback.send("hi", "", post=boom)
        self.assertFalse(ok)
        self.assertIn("connection", error)
        del os.environ["MONEY_GUILT_COLLECTOR_URL"]
        self.assertFalse(feedback.send("hi", "", post=lambda u, p: True)[0])
        self.assertFalse(feedback.form_available())


if __name__ == "__main__":
    unittest.main()
