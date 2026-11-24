"""Offline concurrency and source-isolation regression checks."""

from threading import Event
import unittest
from unittest.mock import patch

from backend.analysis import analyze_identity_stream, validate_identifiers


INPUTS = dict(full_name="Example Person", location="", email="", phone="")


class AnalysisTests(unittest.TestCase):
    def test_all_provider_queries_failed_preserves_username_results(self):
        web = {"queries_total": 2, "queries_completed": 2, "failed_queries": 2, "results": []}
        profiles = {"sites_total": 1, "sites_checked": 1, "found_count": 1}
        with patch("backend.analysis.search_public_web_stream", return_value=iter([web])), patch(
            "backend.analysis.scan_username_stream", return_value=iter([profiles])
        ):
            final = list(analyze_identity_stream(INPUTS, "example"))[-1]
        self.assertEqual(final["web_error"], "All public-web provider queries failed.")
        self.assertEqual(final["web"], web)
        self.assertEqual(final["profiles"], profiles)
        self.assertTrue(final["web_done"] and final["username_done"])
        self.assertIsNone(final["username_error"])

    def test_sources_overlap_and_deliver_match_before_either_finishes(self):
        web_started, web_release, username_release = Event(), Event(), Event()

        def web(**inputs):
            web_started.set()
            if not web_release.wait(3):
                raise RuntimeError("test timed out")
            yield {"results": [], "queries_run": 1}

        def profiles(*args, **kwargs):
            if not web_started.wait(1):
                raise RuntimeError("web was not concurrent")
            yield {"sites_checked": 1, "sites_total": 2, "found_count": 1,
                   "results": [{"site": "Early", "status": "found"}]}
            if not username_release.wait(3):
                raise RuntimeError("test timed out")
            yield {"sites_checked": 2, "sites_total": 2, "found_count": 1,
                   "results": [{"site": "Early", "status": "found"}]}

        with patch("backend.analysis.search_public_web_stream", side_effect=web), patch("backend.analysis.scan_username_stream", side_effect=profiles):
            stream = analyze_identity_stream(INPUTS, "example")
            try:
                next(stream)
                early = next(stream)
                self.assertEqual(early["profiles"]["found_count"], 1)
                self.assertFalse(early["web_done"])
                self.assertFalse(early["username_done"])
                self.assertEqual(early["profiles"]["sites_checked"], 1)
                username_release.set()
                web_release.set()
                final = list(stream)[-1]
                self.assertTrue(final["web_done"] and final["username_done"])
                self.assertEqual(final["profiles"]["sites_checked"], 2)
                self.assertGreaterEqual(final["total_elapsed"], final["username_elapsed"])
            finally:
                username_release.set()
                web_release.set()
                stream.close()

    def test_web_failure_does_not_discard_username_results(self):
        with patch("backend.analysis.search_public_web_stream", side_effect=RuntimeError("offline")), patch(
            "backend.analysis.scan_username_stream", return_value=iter([{"sites_checked": 1, "found_count": 1}])
        ):
            final = list(analyze_identity_stream(INPUTS, "example"))[-1]
        self.assertEqual(final["web_error"], "offline")
        self.assertEqual(final["profiles"]["found_count"], 1)
        self.assertIsNotNone(final["web_elapsed"])
        self.assertIsNone(final["username_error"])

    def test_username_failure_keeps_partial_results_and_web(self):
        def broken(*args, **kwargs):
            yield {"sites_checked": 1, "found_count": 1}
            raise RuntimeError("dataset interruption")

        with patch("backend.analysis.search_public_web_stream", return_value=iter([{"unique_result_count": 2}])), patch(
            "backend.analysis.scan_username_stream", side_effect=broken
        ):
            final = list(analyze_identity_stream(INPUTS, "example"))[-1]
        self.assertEqual(final["profiles"]["sites_checked"], 1)
        self.assertEqual(final["username_error"], "dataset interruption")
        self.assertEqual(final["web"]["unique_result_count"], 2)
        self.assertIsNotNone(final["username_elapsed"])

    def test_one_identifier_is_enough_but_permission_is_still_required(self):
        self.assertEqual(validate_identifiers("", "example", authorized=True), [])
        self.assertEqual(validate_identifiers("Example Person", "", authorized=True), [])
        self.assertEqual(validate_identifiers("Example", "", authorized=True), [])
        self.assertEqual(validate_identifiers("", "", email="person@example.com", authorized=True), [])
        self.assertEqual(validate_identifiers("", "", phone="513-555-0123", authorized=True), [])
        self.assertTrue(validate_identifiers("   ", "", authorized=True))
        self.assertTrue(validate_identifiers("Example Person", "example", authorized=False))
        self.assertEqual(validate_identifiers("Example Person", "example", authorized=True), [])
        self.assertEqual(validate_identifiers("Example Person", "example", phone="+1 513-555-0123", authorized=True), [])
        self.assertTrue(validate_identifiers("Example Person", "example", phone="city", authorized=True))

    def test_username_only_searches_web_and_all_sites_by_default(self):
        inputs = dict(full_name="", email="", phone="", location="Example City")
        with patch("backend.analysis.search_public_web_stream", return_value=iter([{"results": []}])) as web, patch(
            "backend.analysis.scan_username_stream", return_value=iter([{"sites_checked": 126, "sites_total": 126}])
        ) as profiles:
            final = list(analyze_identity_stream(inputs, "example"))[-1]
        web.assert_called_once_with(**inputs, username="example")
        profiles.assert_called_once_with("example", max_sites=None, workers=None)
        self.assertTrue(final["web_requested"])
        self.assertTrue(final["username_requested"])
        self.assertEqual(final["profiles"]["sites_checked"], 126)

    def test_name_email_or_phone_only_never_starts_username_scan(self):
        for identifier, value in (("full_name", "Example Person"), ("email", "person@example.com"), ("phone", "513-555-0123")):
            with self.subTest(identifier=identifier):
                inputs = dict(full_name="", email="", phone="", location="")
                inputs[identifier] = value
                with patch("backend.analysis.search_public_web_stream", return_value=iter([{"results": []}])) as web, patch(
                    "backend.analysis.scan_username_stream"
                ) as profiles:
                    final = list(analyze_identity_stream(inputs))[-1]
                web.assert_called_once_with(**inputs, username="")
                profiles.assert_not_called()
                self.assertFalse(final["username_requested"])
                self.assertIsNone(final["profiles"])
                self.assertIsNone(final["username_error"])
                self.assertIsNone(final["username_elapsed"])
                self.assertTrue(final["username_done"] and final["web_done"])

    def test_name_only_yields_public_pages_before_web_source_finishes(self):
        release = Event()

        def web(**inputs):
            yield {"queries_total": 2, "queries_completed": 0, "results": []}
            yield {"queries_total": 2, "queries_completed": 1, "unique_result_count": 1,
                   "results": [{"title": "Example Person", "url": "https://example.com"}]}
            if not release.wait(3):
                raise RuntimeError("test timed out")
            yield {"queries_total": 2, "queries_completed": 2, "unique_result_count": 1,
                   "results": [{"title": "Example Person", "url": "https://example.com"}]}

        with patch("backend.analysis.search_public_web_stream", side_effect=web), patch("backend.analysis.scan_username_stream") as profiles:
            stream = analyze_identity_stream(INPUTS)
            try:
                next(stream)
                initial = next(stream)
                self.assertEqual(initial["web"]["queries_completed"], 0)
                partial = next(stream)
                self.assertEqual(partial["web"]["queries_completed"], 1)
                self.assertEqual(len(partial["web"]["results"]), 1)
                self.assertFalse(partial["web_done"])
                self.assertTrue(partial["username_done"])
                self.assertIsNotNone(partial["web_elapsed"])
                release.set()
                final = list(stream)[-1]
                self.assertTrue(final["web_done"])
                self.assertEqual(final["web"]["queries_completed"], 2)
                profiles.assert_not_called()
            finally:
                release.set()
                stream.close()

    def test_partial_web_results_survive_a_later_source_error(self):
        def web(**inputs):
            yield {"unique_result_count": 1, "queries_completed": 1, "queries_total": 3}
            raise RuntimeError("stream interrupted")

        with patch("backend.analysis.search_public_web_stream", side_effect=web):
            final = list(analyze_identity_stream(INPUTS))[-1]
        self.assertEqual(final["web"]["unique_result_count"], 1)
        self.assertEqual(final["web_error"], "stream interrupted")
        self.assertTrue(final["web_done"])


if __name__ == "__main__":
    unittest.main()
