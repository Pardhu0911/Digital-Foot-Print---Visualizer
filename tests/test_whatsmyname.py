"""Offline regression tests for concurrent WhatsMyName progress."""
import threading
import unittest
from unittest.mock import patch

from backend.whatsmyname import scan_username, scan_username_stream


class UsernameStreamingTests(unittest.TestCase):
    def test_yields_matches_before_other_checks_finish_and_keeps_snapshots(self):
        slow_started = threading.Event()
        release_slow = threading.Event()
        sites = [{"name": "Fast", "cat": "coding"}, {"name": "Slow", "cat": "social"}]

        def check(site, username):
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

        def check(site, username):
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

        def check(site, username):
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


if __name__ == "__main__":
    unittest.main()
