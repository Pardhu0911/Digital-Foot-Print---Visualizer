"""Offline regression tests for concurrent WhatsMyName progress."""
import os
import ssl
import threading
import unittest
from unittest.mock import Mock, PropertyMock, patch

import requests

from backend import whatsmyname
from backend.whatsmyname import check_site, scan_username, scan_username_stream


class UsernameStreamingTests(unittest.TestCase):
    def test_yields_matches_before_other_checks_finish_and_keeps_snapshots(self):
        slow_started = threading.Event()
        release_slow = threading.Event()
        sites = [{"name": "Fast", "cat": "coding"}, {"name": "Slow", "cat": "social"}]

        def check(site, username, *, transport):
            if site["name"] == "Slow":
                slow_started.set()
                if not release_slow.wait(3):
                    raise RuntimeError("Slow worker was not released")
                status = "not_found"
            else:
                if not slow_started.wait(3):
                    raise RuntimeError("Checks did not run concurrently")
                status = "found"
            return {"site": site["name"], "category": site["cat"], "status": status}

        with patch("backend.whatsmyname.load_dataset", return_value={"sites": sites}), patch("backend.whatsmyname.check_site", side_effect=check):
            stream = scan_username_stream(" jane ", workers=2)
            initial = next(stream)
            self.assertEqual(initial["sites_checked"], 0)
            self.assertEqual(initial["sites_total"], 2)
            try:
                first = next(stream)
                self.assertTrue(slow_started.is_set())
                self.assertFalse(release_slow.is_set())
                self.assertEqual(first["sites_checked"], 1)
                self.assertEqual(first["found_count"], 1)
                self.assertEqual(first["results"][0]["site"], "Fast")
            finally:
                release_slow.set()
            final = list(stream)[-1]
            self.assertEqual(final["sites_checked"], 2)
            self.assertEqual(final["not_found_count"], 1)
            self.assertEqual(first["sites_checked"], 1)
            self.assertEqual(len(first["results"]), 1)
            self.assertEqual(initial["results"], [])
            self.assertEqual(final["username"], "jane")

    def test_compatibility_counts_errors_and_sort_order(self):
        statuses = ["not_found", "timeout", "found", "blocked", "rate_limited", "could_not_verify", "error"]
        sites = [{"name": status, "cat": "social"} for status in statuses]
        sites += [{"name": "Invalid", "valid": False}]

        def check(site, username, *, transport):
            if site["name"] == "error":
                raise RuntimeError("Unexpected site failure")
            return {"site": site["name"], "status": site["name"]}

        with patch("backend.whatsmyname.load_dataset", return_value={"sites": sites}), patch("backend.whatsmyname.check_site", side_effect=check):
            summary = scan_username("jane", max_sites=None, workers=3)
        self.assertEqual(summary["sites_checked"], 7)
        self.assertEqual(summary["sites_total"], 7)
        self.assertEqual(summary["found_count"], 1)
        self.assertEqual(summary["not_found_count"], 1)
        self.assertEqual(summary["uncertain_count"], 5)
        self.assertEqual(summary["results"][0]["status"], "found")
        self.assertEqual(summary["status_counts"], dict.fromkeys(statuses, 1))
        self.assertIn("error", next(row for row in summary["results"] if row["status"] == "error"))

    def test_default_scans_all_active_definitions_beyond_one_hundred_in_all_categories(self):
        sites = [{"name": f"Site {index}", "cat": "social"} for index in range(125)]
        sites += [
            {"name": "Previously excluded category", "cat": "xx NSFW xx"},
            {"name": "Disabled definition", "cat": "social", "valid": False},
        ]

        def check(site, username, *, transport):
            return {"site": site["name"], "category": site["cat"], "status": "not_found"}

        with patch("backend.whatsmyname.load_dataset", return_value={"sites": sites}), patch("backend.whatsmyname.check_site", side_effect=check) as check_mock:
            snapshots = list(scan_username_stream("jane"))
            self.assertEqual(snapshots[0]["sites_total"], 126)
            self.assertEqual(len(snapshots), 127)
            self.assertEqual(snapshots[-1]["sites_checked"], 126)
            self.assertEqual(check_mock.call_count, 126)
            checked = {row["site"] for row in snapshots[-1]["results"]}
            self.assertIn("Previously excluded category", checked)
            self.assertNotIn("Disabled definition", checked)
            check_mock.reset_mock()
            final = scan_username("jane")
            self.assertEqual(final["sites_total"], 126)
            self.assertEqual(final["sites_checked"], 126)
            self.assertEqual(check_mock.call_count, 126)
            self.assertEqual(final["not_found_count"], 126)

    def test_site_limit_and_empty_dataset(self):
        with patch("backend.whatsmyname.load_dataset", return_value={"sites": []}):
            snapshots = list(scan_username_stream("jane"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["sites_total"], 0)
            self.assertEqual(scan_username("jane")["sites_checked"], 0)
        with patch("backend.whatsmyname.load_dataset", return_value={"sites": [{"name": "One"}, {"name": "Two"}]}), patch("backend.whatsmyname.check_site", return_value={"site": "One", "status": "found"}) as check:
            self.assertEqual(scan_username("jane", max_sites=1)["sites_total"], 1)
            self.assertEqual(check.call_count, 1)

    def test_empty_username_is_rejected(self):
        with self.assertRaises(ValueError):
            list(scan_username_stream("  "))
        with self.assertRaises(ValueError):
            scan_username("")

    def test_configured_concurrency_is_bounded_and_workers_share_one_transport(self):
        sites = [{"name": f"Site {index}"} for index in range(6)]
        lock, both_started, release = threading.Lock(), threading.Event(), threading.Event()
        active, peak = 0, 0
        transports = set()

        def check(site, username, *, transport):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                transports.add(transport)
                if active == 2:
                    both_started.set()
            try:
                if not release.wait(3):
                    raise RuntimeError("Test worker was not released")
                return {"site": site["name"], "status": "unknown"}
            finally:
                with lock:
                    active -= 1

        with patch.dict(os.environ, {"USERNAME_SCAN_CONCURRENCY": "2"}), patch(
            "backend.whatsmyname.load_dataset", return_value={"sites": sites}
        ), patch("backend.whatsmyname.check_site", side_effect=check):
            end = []
            thread = threading.Thread(target=lambda: end.append(scan_username("jane")))
            thread.start()
            try:
                self.assertTrue(both_started.wait(3))
            finally:
                release.set()
                thread.join(5)
        self.assertEqual(peak, 2)
        self.assertEqual(len(transports), 1)
        self.assertEqual(end[0]["sites_checked"], 6)
        self.assertEqual(end[0]["uncertain_count"], 6)


class SiteRuleTests(unittest.TestCase):
    rule = {
        "name": "Example", "uri_check": "https://example.test/check/{account}",
        "e_code": 200, "e_string": "profile exists", "m_code": 200, "m_string": "user missing",
    }

    def test_shared_tls_context_keeps_certificate_and_hostname_verification(self):
        transport = whatsmyname._ScanTransport(2)
        self.addCleanup(transport.close)
        request = requests.Request("GET", "https://example.test").prepare()
        _, first = transport.build_connection_pool_key_attributes(request, True)
        _, second = transport.build_connection_pool_key_attributes(request, True)
        context = first["ssl_context"]
        self.assertIs(context, second["ssl_context"])
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)
        _, custom = transport.build_connection_pool_key_attributes(request, "custom.pem")
        self.assertNotIn("ssl_context", custom)
        self.assertEqual(custom["ca_certs"], "custom.pem")

    def test_body_rules_preserve_found_missing_ambiguous_and_unknown(self):
        for body, expected in (
            ("profile exists", "found"),
            ("user missing", "not_found"),
            ("profile exists and user missing", "could_not_verify"),
            ("", "could_not_verify"),
        ):
            with self.subTest(body=body):
                response = requests.Response()
                response.status_code = 200
                response.encoding = "utf-8"
                response._content = body.encode()
                response._content_consumed = True
                client = Mock()
                client.request.return_value = response
                self.assertEqual(check_site(self.rule, "jane", transport=client)["status"], expected)

    def test_skips_body_for_status_only_and_unusable_responses(self):
        rule = dict(self.rule, m_code=404, m_string="")
        for code, expected in ((404, "not_found"), (403, "blocked"), (429, "rate_limited"), (502, "could_not_verify")):
            with self.subTest(code=code):
                response = requests.Response()
                response.status_code = code
                response.raw = Mock()
                client = Mock()
                client.request.return_value = response
                with patch.object(requests.Response, "text", new_callable=PropertyMock) as text:
                    self.assertEqual(check_site(rule, "jane", transport=client)["status"], expected)
                text.assert_not_called()
                response.raw.close.assert_called_once()
                client.request.assert_called_once()

    def test_post_preserves_headers_body_and_pretty_url(self):
        rule = dict(self.rule, post_body='{"query":"{account}"}', strip_bad_char="@",
                    headers={"Content-Type": "application/json"}, uri_pretty="https://example.test/{account}")
        client = Mock()
        client.request.side_effect = requests.ReadTimeout()
        result = check_site(rule, " @jane doe ", transport=client)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["http_status"])
        self.assertEqual(result["profile_url"], "https://example.test/jane%20doe")
        client.request.assert_called_once_with(
            "POST", "https://example.test/check/jane%20doe", data='{"query":"jane doe"}',
            headers={"User-Agent": whatsmyname.USER_AGENT, "Content-Type": "application/json"},
            timeout=whatsmyname.DEFAULT_TIMEOUT, allow_redirects=True, stream=True,
        )

    def test_body_read_timeout_is_uncertain_and_closes_response(self):
        response = requests.Response()
        response.status_code = 200
        response.raw = Mock()
        client = Mock()
        client.request.return_value = response
        read_timeout = whatsmyname.ReadTimeoutError(None, "/check/jane", "timed out")
        with patch.object(requests.Response, "text", new_callable=PropertyMock, side_effect=requests.ConnectionError(read_timeout)):
            result = check_site(self.rule, "jane", transport=client)
        self.assertEqual(result["status"], "timeout")
        response.raw.close.assert_called_once()

    def test_dataset_cache_expires_and_reuses_parsed_definitions(self):
        response = Mock()
        response.json.side_effect = [{"sites": [{}], "categories": ["social"]}, {"sites": [], "categories": []}]
        with patch.object(whatsmyname, "_dataset", None), patch.object(whatsmyname, "_dataset_expires", 0), patch(
            "backend.whatsmyname.requests.get", return_value=response
        ) as fetch, patch("backend.whatsmyname.time.monotonic", return_value=100) as clock:
            original = whatsmyname.load_dataset()
            self.assertIs(whatsmyname.load_dataset(), original)
            self.assertEqual(whatsmyname.get_dataset_stats()["active_site_count"], 1)
            clock.return_value += whatsmyname.DATASET_TTL_SECONDS
            self.assertEqual(whatsmyname.load_dataset()["sites"], [])
            self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
