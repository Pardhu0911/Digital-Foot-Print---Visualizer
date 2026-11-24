"""Offline checks for the preserved public-web evidence pipeline."""
import json
import unittest
from threading import Event, Lock
from unittest.mock import Mock, patch

from ddgs.ddgs import DDGS
from ddgs.results import TextResult

from backend.web_search import build_queries, search_identity, search_identity_stream, search_query


class PublicWebTests(unittest.TestCase):
    def test_auto_backend_can_return_another_providers_results(self):
        unavailable = Mock(provider="unavailable")
        unavailable.search.side_effect = TimeoutError("timed out")
        available = Mock(provider="available")
        available.search.return_value = [
            TextResult(title="Public project by jane123", href="https://example.com/jane123", body="jane123"),
        ]
        empty = Mock(provider="empty")
        empty.search.return_value = []
        with patch.object(DDGS, "_get_engines", return_value=[unavailable, available, empty]) as engines:
            result = search_identity(username="jane123")
        engines.assert_called_once_with("text", "auto")
        unavailable.search.assert_called_once()
        available.search.assert_called_once()
        self.assertEqual(result["unique_result_count"], 1)
        self.assertEqual(result["failed_queries"], 0)

    @patch("backend.web_search.DDGS")
    def test_username_only_runs_public_web_query_and_returns_candidate_pages(self, ddgs):
        ddgs.return_value.text.return_value = [
            {"title": "Jane's project", "href": "https://example.com/jane123", "body": "Public work by jane123"},
        ]
        result = search_identity(username="  jane123  ")
        self.assertEqual(result["queries_run"], 1)
        self.assertEqual(result["queries_with_results"], 1)
        self.assertEqual(result["unique_result_count"], 1)
        self.assertEqual(result["searches"][0]["query_type"], "username")
        self.assertEqual(result["results"][0]["query_types"], ["username"])
        self.assertEqual(ddgs.return_value.text.call_args.args, ('"jane123"',))
        self.assertEqual(ddgs.return_value.text.call_args.kwargs["backend"], "auto")
        self.assertEqual(result["results"][0]["search_engine"], "DDGS (auto)")

    @patch("backend.web_search.DDGS")
    def test_combined_inputs_search_each_identifier_and_cross_reference_handle(self, ddgs):
        ddgs.return_value.text.return_value = []
        result = search_identity(
            full_name="Jane Doe", username="jane123", location="Cincinnati, Ohio",
            email="jane@example.com", phone="513-555-0123",
        )
        queries = {row["query_type"]: row["query"] for row in result["searches"]}
        self.assertEqual(queries["full_name"], '"Jane Doe"')
        self.assertEqual(queries["name_location"], '"Jane Doe" "Cincinnati, Ohio"')
        self.assertEqual(queries["username"], '"jane123"')
        self.assertEqual(queries["name_username"], '"Jane Doe" "jane123"')
        self.assertEqual(queries["username_location"], '"jane123" "Cincinnati, Ohio"')
        self.assertEqual(queries["email"], '"jane@example.com"')
        self.assertIn("phone", queries)
        expected = len(build_queries("Jane Doe", "jane@example.com", "513-555-0123", "Cincinnati, Ohio", "jane123"))
        self.assertEqual(result["queries_run"], expected)
        self.assertEqual(ddgs.return_value.text.call_count, expected)

    @patch("backend.web_search.DDGS")
    def test_legacy_positional_arguments_keep_email_phone_location_and_result_limit(self, ddgs):
        ddgs.return_value.text.return_value = []
        positional = build_queries("Jane Doe", "jane@example.com", "513-555-0123", "Ohio")
        keyword = build_queries(full_name="Jane Doe", email="jane@example.com", phone="513-555-0123", location="Ohio")
        self.assertEqual(positional, keyword)
        result = search_identity("Jane Doe", "jane@example.com", "513-555-0123", "Ohio", 4)
        self.assertEqual(result["queries_run"], len(positional))
        self.assertTrue(all(row["query_type"] not in {"username", "name_username", "username_location"}
                            for row in result["searches"]))
        self.assertTrue(all(call.kwargs["max_results"] == 4 for call in ddgs.return_value.text.call_args_list))

    def test_optional_identifiers_enrich_queries_without_replacing_name_searches(self):
        queries = build_queries(
            full_name="  Jane Doe  ",
            location=" Cincinnati, Ohio ",
            email=" jane@example.com ",
            phone="+1 (513) 555-0123",
        )
        by_type = {}
        for item in queries:
            by_type.setdefault(item["type"], []).append(item["query"])
        self.assertEqual(by_type["full_name"], ['"Jane Doe"'])
        self.assertEqual(by_type["name_location"], ['"Jane Doe" "Cincinnati, Ohio"'])
        self.assertEqual(by_type["email"], ['"jane@example.com"'])
        self.assertIn('"513-555-0123"', by_type["phone"])
        self.assertIn('"(513) 555-0123"', by_type["phone"])
        self.assertIn("full_name_non_linkedin", by_type)
        self.assertIn("name_pdf", by_type)
        self.assertEqual(len({item["query"] for item in queries}), len(queries))

    def test_name_plan_covers_unquoted_and_focused_public_profile_queries(self):
        queries = build_queries(full_name="Jane Doe")
        kinds = {row["type"] for row in queries}
        self.assertTrue({"full_name", "full_name_non_linkedin", "name_pdf", "full_name_unquoted",
                         "name_linkedin", "name_github", "name_profiles", "name_social"}.issubset(kinds))
        self.assertGreaterEqual(len(queries), 7)
        self.assertLessEqual(len(queries), 9)

    @patch("backend.web_search.DDGS")
    def test_results_merge_duplicate_pages_but_keep_query_support_and_best_snippet(self, ddgs):
        responses = {
            '"Jane Doe"': [{"title": " Jane Doe ", "href": "https://www.example.com/jane", "body": "Short"}],
            '"Jane Doe" -site:linkedin.com': [
                {"title": "Jane profile", "href": "https://www.example.com/jane", "body": "A more informative Jane Doe profile"},
                {"title": "Public document", "href": "https://documents.example.org/jane.pdf", "body": "Jane Doe document"},
            ],
        }
        ddgs.return_value.text.side_effect = lambda query, **kwargs: responses.get(query, [])
        result = search_identity("Jane Doe", max_results_per_query=4)
        self.assertEqual(result["queries_run"], len(build_queries("Jane Doe")))
        self.assertEqual(result["queries_with_results"], 2)
        self.assertEqual(result["no_result_queries"], result["queries_run"] - 2)
        self.assertEqual(result["failed_queries"], 0)
        self.assertEqual(result["unique_result_count"], 2)
        self.assertEqual(result["unique_domain_count"], 2)
        merged = next(item for item in result["results"] if item["domain"] == "example.com")
        self.assertEqual(merged["snippet"], "A more informative Jane Doe profile")
        self.assertEqual(set(merged["query_types"]), {"full_name", "full_name_non_linkedin"})
        self.assertEqual(len(merged["queries"]), 2)
        for call in ddgs.return_value.text.call_args_list:
            self.assertEqual(call.kwargs["backend"], "auto")
            self.assertEqual(call.kwargs["max_results"], 4)

    @patch("backend.web_search.DDGS")
    def test_one_failed_query_does_not_discard_other_evidence(self, ddgs):
        def provider(query, **kwargs):
            if query == '"Jane Doe"':
                raise RuntimeError("429 Too many requests")
            if query == '"Jane Doe" -site:linkedin.com':
                return [{"title": "Jane", "href": "https://example.com/jane", "body": "Jane Doe"}]
            raise RuntimeError("No results found")

        ddgs.return_value.text.side_effect = provider
        result = search_identity("Jane Doe")
        self.assertEqual(result["failed_queries"], 1)
        self.assertEqual(result["no_result_queries"], result["queries_run"] - 2)
        self.assertEqual(result["unique_result_count"], 1)
        self.assertEqual(result["searches"][0]["status"], "rate_limited")
        self.assertEqual(result["searches"][1]["status"], "success")
        self.assertTrue(all(row["status"] == "no_results" for row in result["searches"][2:]))

    @patch("backend.web_search.DDGS")
    def test_timeouts_and_generic_errors_are_unknown_not_negative_results(self, ddgs):
        for message, expected_status in (
            ("Connection timed out", "timeout"),
            ("Unexpected provider response", "error"),
        ):
            with self.subTest(message=message):
                ddgs.return_value.text.side_effect = RuntimeError(message)
                result = search_query('"Jane Doe"')
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["result_count"], 0)
                self.assertIn("error", result)

    @patch("backend.web_search.DDGS")
    def test_empty_provider_output_and_items_without_urls_are_no_results(self, ddgs):
        for payload in (None, [], [{"title": "No link", "body": "Text only"}]):
            with self.subTest(payload=payload):
                ddgs.return_value.text.return_value = payload
                result = search_query('"Jane Doe"')
                self.assertEqual(result["status"], "no_results")
                self.assertEqual(result["results"], [])
                self.assertNotIn("error", result)

    @patch("backend.web_search.DDGS")
    def test_query_log_counts_provider_rows_before_filtering(self, ddgs):
        ddgs.return_value.text.return_value = [
            {"title": "Jane", "href": "https://example.com/jane123"},
            {"title": "Other", "href": "https://example.com/other"},
            {"title": "Missing link"},
        ]
        with patch("builtins.print") as logged:
            result = search_identity(username="jane123")
        log = json.loads(logged.call_args_list[1].args[0].removeprefix("public_web_query "))
        self.assertEqual(log["query_type"], "username")
        self.assertEqual(log["query"], '"jane123"')
        self.assertEqual(log["backend"], "auto")
        self.assertEqual(log["raw_result_count"], 3)
        self.assertEqual(log["accepted_result_count"], 1)
        self.assertEqual(log["filtered_result_count"], 2)
        self.assertEqual(log["status"], "success")
        self.assertGreaterEqual(log["elapsed_seconds"], 0)
        self.assertIsNone(log["exception_class"])
        self.assertIn("ddgs_version=", logged.call_args_list[0].args[0])
        self.assertEqual(result["filtered_result_count"], 1)
        self.assertNotIn("elapsed_seconds", result["searches"][0])

    @patch("backend.web_search.DDGS")
    def test_all_empty_results_emit_log_only_and_preserve_no_results(self, ddgs):
        ddgs.return_value.text.side_effect = RuntimeError("No results found.")
        with patch("builtins.print") as logged:
            result = search_identity(full_name="Jane Doe")
        self.assertEqual(result["no_result_queries"], result["queries_total"])
        self.assertEqual(result["failed_queries"], 0)
        self.assertEqual(result["warnings"], [])
        lines = [entry.args[0] for entry in logged.call_args_list]
        self.assertEqual(sum(line.startswith("All public-web queries returned zero") for line in lines), 1)
        query = json.loads(lines[1].removeprefix("public_web_query "))
        self.assertEqual(query["raw_result_count"], 0)
        self.assertEqual(query["status"], "no_results")
        self.assertEqual(query["exception_class"], "RuntimeError")
        self.assertEqual(query["exception_message"], "No results found.")

    @patch("backend.web_search.DDGS")
    def test_explicit_rate_limit_with_no_results_in_message_is_a_failure(self, ddgs):
        ddgs.return_value.text.side_effect = RuntimeError("429: no results due to rate limiting")
        with patch("builtins.print") as logged:
            result = search_identity(username="jane123")
        self.assertEqual(result["failed_queries"], 1)
        self.assertEqual(result["no_result_queries"], 0)
        log = json.loads(logged.call_args.args[0].removeprefix("public_web_query "))
        self.assertEqual(log["status"], "rate_limited")
        self.assertEqual(log["exception_class"], "RuntimeError")

    @patch("backend.web_search.DDGS")
    def test_invalid_phone_warns_and_is_never_submitted_as_a_query(self, ddgs):
        ddgs.return_value.text.return_value = []
        result = search_identity(full_name="Jane Doe", phone="Cincinnati")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["queries_run"], len(build_queries("Jane Doe")))
        self.assertTrue(all(item["query_type"] != "phone" for item in result["searches"]))
        self.assertTrue(all("Cincinnati" not in call.args[0]
                            for call in ddgs.return_value.text.call_args_list))

    @patch("backend.web_search.DDGS")
    def test_unrelated_provider_pages_are_filtered_but_raw_diagnostics_remain(self, ddgs):
        unrelated = {"title": "Aishwarya Nigam profile", "href": "https://example.com/aishwarya-nigam", "body": "A different person's biography"}
        ddgs.return_value.text.return_value = [unrelated]
        result = search_identity("Example Person")
        self.assertEqual(result["unique_result_count"], 0)
        self.assertEqual(result["filtered_result_count"], 1)
        self.assertEqual(result["searches"][0]["results"][0]["title"], unrelated["title"])
        self.assertEqual(result["searches"][0]["filtered_result_count"], 1)
        self.assertEqual(len(result["searches"][0]["filtered_results"]), 1)

    @patch("backend.web_search.DDGS")
    def test_name_support_allows_middle_names_punctuation_case_and_decoded_urls(self, ddgs):
        ddgs.return_value.text.return_value = [
            {"title": "JANE, Alexandra DOE", "href": "https://example.com/one", "body": "Biography"},
            {"title": "Portfolio", "href": "https://example.com/Jane%20Q.%20Doe", "body": "Work"},
            {"title": "Janette Doer", "href": "https://example.com/unrelated", "body": "Different name"},
        ]
        result = search_identity("Jane Doe")
        self.assertEqual(result["unique_result_count"], 2)
        self.assertEqual(result["filtered_result_count"], 1)

    @patch("backend.web_search.DDGS")
    def test_single_token_name_requires_whole_token_evidence(self, ddgs):
        ddgs.return_value.text.return_value = [
            {"title": "Prince biography", "href": "https://example.com/one", "body": "Musician"},
            {"title": "Princeton alumni", "href": "https://example.com/two", "body": "University"},
        ]
        result = search_identity("Prince")
        self.assertEqual(result["unique_result_count"], 1)
        self.assertEqual(result["results"][0]["title"], "Prince biography")

    @patch("backend.web_search.DDGS")
    def test_exact_email_phone_or_username_support_can_surface_a_page(self, ddgs):
        cases = [
            ({"email": "jane@example.com"}, "Contact JANE@example.com", "Contact jane@example.com.evil"),
            ({"phone": "513-555-0123"}, "Call +1 (513) 555-0123", "Call 513-555-01234"),
            ({"username": "jane123"}, "By jane123", "By jane1234"),
        ]
        for identifiers, supported, unrelated in cases:
            with self.subTest(identifiers=identifiers):
                ddgs.return_value.text.return_value = [
                    {"title": "Contact", "href": "https://example.com/one", "body": supported},
                    {"title": "Other", "href": "https://example.com/two", "body": unrelated},
                ]
                result = search_identity(**identifiers)
                self.assertEqual(result["unique_result_count"], 1)
                self.assertEqual(result["results"][0]["url"], "https://example.com/one")

    def test_stream_delivers_initial_progress_and_early_result_while_slow_query_runs(self):
        slow_started, release_slow, fast_completed = Event(), Event(), Event()
        active_lock = Lock()
        active, max_active = 0, 0

        def query(query, max_results):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                if query == "slow":
                    slow_started.set()
                    if not release_slow.wait(3):
                        raise RuntimeError("test timed out")
                else:
                    if not slow_started.wait(1):
                        raise RuntimeError("queries did not overlap")
                    fast_completed.set()
                return {"status": "success", "query": query, "result_count": 1, "results": [
                    {"title": "Jane Doe", "url": f"https://example.com/{query}", "domain": "example.com", "snippet": "Public biography"}]}
            finally:
                with active_lock:
                    active -= 1

        plan = [{"type": "slow", "query": "slow"}, {"type": "fast", "query": "fast"}]
        with patch("backend.web_search.build_queries", return_value=plan), patch("backend.web_search.search_query", side_effect=query):
            stream = search_identity_stream("Jane Doe")
            try:
                initial = next(stream)
                self.assertEqual(initial["queries_total"], 2)
                self.assertEqual(initial["queries_completed"], 0)
                self.assertEqual(initial["results"], [])
                early = next(stream)
                self.assertTrue(fast_completed.is_set())
                self.assertFalse(release_slow.is_set())
                self.assertEqual(early["queries_completed"], 1)
                self.assertEqual(early["unique_result_count"], 1)
                self.assertEqual(early["results"][0]["url"], "https://example.com/fast")
                self.assertGreaterEqual(max_active, 2)
                release_slow.set()
                final = list(stream)[-1]
                self.assertEqual(final["queries_completed"], 2)
                self.assertEqual(final["queries_run"], 2)
                self.assertEqual(final["unique_result_count"], 2)
                final["results"][0]["query_types"].append("changed")
                final["searches"][0]["results"][0]["title"] = "changed"
                self.assertEqual(initial["results"], [])
                self.assertEqual(early["results"][0]["query_types"], ["fast"])
                self.assertEqual(early["searches"][0]["results"][0]["title"], "Jane Doe")
            finally:
                release_slow.set()
                stream.close()

    def test_excluded_page_count_deduplicates_noise_and_removes_later_supported_urls(self):
        release_supported = Event()
        noise = {"title": "Unrelated Person", "url": "https://example.com/noise",
                 "domain": "example.com", "snippet": "Unrelated biography"}
        upgrade = {**noise, "url": "https://example.com/upgrade"}

        def query(query, max_results):
            if query == "supported":
                if not release_supported.wait(3):
                    raise RuntimeError("test timed out")
                rows = [{**upgrade, "title": "Jane Doe profile", "snippet": "Jane Doe's public work"}]
            elif query == "first":
                rows = [noise, upgrade]
            else:
                rows = [noise]
            return {"status": "success", "result_count": len(rows), "results": rows}

        plan = [{"type": name, "query": name} for name in ("first", "duplicate", "supported")]
        with patch("backend.web_search.build_queries", return_value=plan), patch("backend.web_search.search_query", side_effect=query):
            stream = search_identity_stream("Jane Doe")
            try:
                next(stream)
                progress = next(stream)
                while progress["queries_completed"] < 2:
                    progress = next(stream)
                self.assertEqual(progress["filtered_result_count"], 2)
                self.assertEqual(sum(row["filtered_result_count"] for row in progress["searches"]), 3)
                release_supported.set()
                final = list(stream)[-1]
                self.assertEqual(final["filtered_result_count"], 1)
                self.assertEqual(final["unique_result_count"], 1)
                self.assertEqual(final["results"][0]["url"], upgrade["url"])
                self.assertEqual(sum(row["filtered_result_count"] for row in final["searches"]), 3)
                self.assertEqual(progress["filtered_result_count"], 2)
            finally:
                release_supported.set()
                stream.close()


if __name__ == "__main__":
    unittest.main()
