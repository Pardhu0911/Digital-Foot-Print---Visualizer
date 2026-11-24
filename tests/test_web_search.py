"""Offline checks for the preserved public-web evidence pipeline."""
import json
import os
import unittest
from threading import Event, Lock
from unittest.mock import Mock, patch

import requests

from backend.web_search import build_queries, search_identity, search_identity_stream, search_query


def _response(status=200, *, results=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"results": results}).encode()
    return response


class PublicWebTests(unittest.TestCase):
    def setUp(self):
        key = patch.dict(os.environ, {"TAVILY_API_KEY": "unit-test-credential"})
        key.start()
        self.addCleanup(key.stop)
        provider = patch("backend.web_search.requests.post")
        self.post = provider.start()
        self.addCleanup(provider.stop)
        self.provider = Mock(return_value=[])
        self.post.side_effect = lambda url, **kwargs: _response(results=self.provider(kwargs["json"]["query"]))

    def test_missing_api_key_counts_as_failure_without_a_request(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}), patch("streamlit.secrets", {}):
            result = search_identity(username="jane123")
        self.post.assert_not_called()
        self.assertEqual(result["queries_completed"], 1)
        self.assertEqual(result["no_result_queries"], 0)
        self.assertEqual(result["failed_queries"], 1)
        self.assertEqual(result["searches"][0]["status"], "missing_api_key")
        self.assertIn("TAVILY_API_KEY", result["searches"][0]["error"])

    def test_reads_tavily_key_from_streamlit_secrets(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}), patch("streamlit.secrets", {"TAVILY_API_KEY": "test-secret"}):
            result = search_query('"Jane Doe"')
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer test-secret")

    def test_username_only_runs_public_web_query_and_returns_candidate_pages(self):
        self.provider.return_value = [
            {"title": "Jane's project", "url": "https://example.com/jane123", "content": "Public work by jane123"},
        ]
        result = search_identity(username="  jane123  ")
        self.assertEqual(result["queries_run"], 1)
        self.assertEqual(result["queries_with_results"], 1)
        self.assertEqual(result["unique_result_count"], 1)
        self.assertEqual(result["searches"][0]["query_type"], "username")
        self.assertEqual(result["results"][0]["query_types"], ["username"])
        self.assertEqual(self.post.call_args.args, ("https://api.tavily.com/search",))
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["query"], '"jane123"')
        self.assertEqual(payload["search_depth"], "basic")
        self.assertFalse(payload["include_answer"])
        self.assertFalse(payload["include_raw_content"])
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer unit-test-credential")
        self.assertEqual(result["results"][0]["search_engine"], "tavily")
        self.assertEqual(result["results"][0]["snippet"], "Public work by jane123")

    def test_combined_inputs_search_each_identifier_and_cross_reference_handle(self):
        self.provider.return_value = []
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
        self.assertEqual(self.provider.call_count, expected)

    def test_legacy_positional_arguments_keep_email_phone_location_and_result_limit(self):
        self.provider.return_value = []
        positional = build_queries("Jane Doe", "jane@example.com", "513-555-0123", "Ohio")
        keyword = build_queries(full_name="Jane Doe", email="jane@example.com", phone="513-555-0123", location="Ohio")
        self.assertEqual(positional, keyword)
        result = search_identity("Jane Doe", "jane@example.com", "513-555-0123", "Ohio", 4)
        self.assertEqual(result["queries_run"], len(positional))
        self.assertTrue(all(row["query_type"] not in {"username", "name_username", "username_location"}
                            for row in result["searches"]))
        self.assertTrue(all(call.kwargs["json"]["max_results"] == 4 for call in self.post.call_args_list))

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

    def test_results_merge_duplicate_pages_but_keep_query_support_and_best_snippet(self):
        responses = {
            '"Jane Doe"': [{"title": " Jane Doe ", "url": "https://www.example.com/jane", "content": "Short"}],
            '"Jane Doe" -site:linkedin.com': [
                {"title": "Jane profile", "url": "https://www.example.com/jane", "content": "A more informative Jane Doe profile"},
                {"title": "Public document", "url": "https://documents.example.org/jane.pdf", "content": "Jane Doe document"},
            ],
        }
        self.provider.side_effect = lambda query, **kwargs: responses.get(query, [])
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
        for call in self.post.call_args_list:
            self.assertEqual(call.kwargs["json"]["search_depth"], "basic")
            self.assertEqual(call.kwargs["json"]["max_results"], 4)

    def test_one_failed_query_does_not_discard_other_evidence(self):
        def provider(query, **kwargs):
            if query == '"Jane Doe"':
                raise requests.HTTPError(response=_response(429))
            if query == '"Jane Doe" -site:linkedin.com':
                return [{"title": "Jane", "url": "https://example.com/jane", "content": "Jane Doe"}]
            return []

        self.provider.side_effect = provider
        result = search_identity("Jane Doe")
        self.assertEqual(result["failed_queries"], 1)
        self.assertEqual(result["no_result_queries"], result["queries_run"] - 2)
        self.assertEqual(result["unique_result_count"], 1)
        self.assertEqual(result["searches"][0]["status"], "rate_limited")
        self.assertEqual(result["searches"][1]["status"], "success")
        self.assertTrue(all(row["status"] == "no_results" for row in result["searches"][2:]))

    def test_timeouts_and_generic_errors_are_unknown_not_negative_results(self):
        for failure, expected_status in (
            (requests.Timeout("Connection timed out"), "timeout"),
            (requests.ConnectionError("Unexpected provider response"), "provider_error"),
        ):
            with self.subTest(failure=failure):
                self.provider.side_effect = failure
                result = search_query('"Jane Doe"')
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["result_count"], 0)
                self.assertIn("error", result)

    def test_empty_provider_rows_and_items_without_urls_are_no_results(self):
        for payload in ([], [{"title": "No link", "content": "Text only"}]):
            with self.subTest(payload=payload):
                self.provider.return_value = payload
                result = search_query('"Jane Doe"')
                self.assertEqual(result["status"], "no_results")
                self.assertEqual(result["results"], [])
                self.assertNotIn("error", result)

    def test_query_log_counts_provider_rows_before_filtering(self):
        self.provider.return_value = [
            {"title": "Jane", "url": "https://example.com/jane123"},
            {"title": "Other", "url": "https://example.com/other"},
            {"title": "Missing link"},
        ]
        with patch("builtins.print") as logged:
            result = search_identity(username="jane123")
        log = json.loads(logged.call_args_list[1].args[0].removeprefix("public_web_query "))
        self.assertEqual(log["query_type"], "username")
        self.assertEqual(log["query"], '"jane123"')
        self.assertEqual(log["backend"], "tavily")
        self.assertEqual(log["raw_result_count"], 3)
        self.assertEqual(log["accepted_result_count"], 1)
        self.assertEqual(log["filtered_result_count"], 2)
        self.assertEqual(log["status"], "success")
        self.assertGreaterEqual(log["elapsed_seconds"], 0)
        self.assertIsNone(log["exception_class"])
        self.assertIn("backend=tavily search_depth=basic", logged.call_args_list[0].args[0])
        self.assertEqual(result["filtered_result_count"], 1)
        self.assertNotIn("elapsed_seconds", result["searches"][0])

    def test_all_empty_results_emit_log_only_and_preserve_no_results(self):
        with patch("builtins.print") as logged:
            result = search_identity(full_name="Jane Doe")
        self.assertEqual(result["no_result_queries"], result["queries_total"])
        self.assertEqual(result["failed_queries"], 0)
        self.assertEqual(result["warnings"], [])
        lines = [entry.args[0] for entry in logged.call_args_list]
        self.assertEqual(sum(line == "All public-web queries returned zero Tavily results." for line in lines), 1)
        query = json.loads(lines[1].removeprefix("public_web_query "))
        self.assertEqual(query["raw_result_count"], 0)
        self.assertEqual(query["status"], "no_results")
        self.assertIsNone(query["exception_class"])
        self.assertIsNone(query["exception_message"])

    def test_rate_limit_and_provider_error_do_not_log_provider_body(self):
        self.post.side_effect = None
        for code, expected in ((429, "rate_limited"), (401, "provider_error"), (503, "provider_error")):
            with self.subTest(code=code):
                self.post.return_value = _response(code, results=["unit-test-credential"])
                with patch("builtins.print") as logged:
                    result = search_identity(username="jane123")
                self.assertEqual(result["failed_queries"], 1)
                self.assertEqual(result["no_result_queries"], 0)
                log = json.loads(logged.call_args.args[0].removeprefix("public_web_query "))
                self.assertEqual(log["status"], expected)
                self.assertEqual(log["exception_class"], "HTTPError")
                self.assertEqual(log["exception_message"], f"Tavily returned HTTP {code}.")
                self.assertNotIn("unit-test-credential", str(logged.call_args_list))

    def test_malformed_success_response_counts_as_provider_error(self):
        self.provider.return_value = None
        result = search_query('"Jane Doe"')
        self.assertEqual(result["status"], "provider_error")
        self.assertIn("results list", result["error"])

    def test_configured_key_is_redacted_from_transport_error_logs(self):
        self.provider.side_effect = requests.ConnectionError("Auth header Bearer unit-test-credential")
        with patch("builtins.print") as logged:
            result = search_identity(username="jane123")
        self.assertNotIn("unit-test-credential", str(logged.call_args_list))
        self.assertEqual(result["failed_queries"], 1)
        self.assertIn("[redacted]", result["searches"][0]["error"])

    def test_invalid_phone_warns_and_is_never_submitted_as_a_query(self):
        self.provider.return_value = []
        result = search_identity(full_name="Jane Doe", phone="Cincinnati")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["queries_run"], len(build_queries("Jane Doe")))
        self.assertTrue(all(item["query_type"] != "phone" for item in result["searches"]))
        self.assertTrue(all("Cincinnati" not in call.args[0]
                            for call in self.provider.call_args_list))

    def test_unrelated_provider_pages_are_filtered_but_raw_diagnostics_remain(self):
        unrelated = {"title": "Aishwarya Nigam profile", "url": "https://example.com/aishwarya-nigam", "content": "A different person's biography"}
        self.provider.return_value = [unrelated]
        result = search_identity("Example Person")
        self.assertEqual(result["unique_result_count"], 0)
        self.assertEqual(result["filtered_result_count"], 1)
        self.assertEqual(result["searches"][0]["results"][0]["title"], unrelated["title"])
        self.assertEqual(result["searches"][0]["filtered_result_count"], 1)
        self.assertEqual(len(result["searches"][0]["filtered_results"]), 1)

    def test_name_support_allows_middle_names_punctuation_case_and_decoded_urls(self):
        self.provider.return_value = [
            {"title": "JANE, Alexandra DOE", "url": "https://example.com/one", "content": "Biography"},
            {"title": "Portfolio", "url": "https://example.com/Jane%20Q.%20Doe", "content": "Work"},
            {"title": "Janette Doer", "url": "https://example.com/unrelated", "content": "Different name"},
        ]
        result = search_identity("Jane Doe")
        self.assertEqual(result["unique_result_count"], 2)
        self.assertEqual(result["filtered_result_count"], 1)

    def test_single_token_name_requires_whole_token_evidence(self):
        self.provider.return_value = [
            {"title": "Prince biography", "url": "https://example.com/one", "content": "Musician"},
            {"title": "Princeton alumni", "url": "https://example.com/two", "content": "University"},
        ]
        result = search_identity("Prince")
        self.assertEqual(result["unique_result_count"], 1)
        self.assertEqual(result["results"][0]["title"], "Prince biography")

    def test_exact_email_phone_or_username_support_can_surface_a_page(self):
        cases = [
            ({"email": "jane@example.com"}, "Contact JANE@example.com", "Contact jane@example.com.evil"),
            ({"phone": "513-555-0123"}, "Call +1 (513) 555-0123", "Call 513-555-01234"),
            ({"username": "jane123"}, "By jane123", "By jane1234"),
        ]
        for identifiers, supported, unrelated in cases:
            with self.subTest(identifiers=identifiers):
                self.provider.return_value = [
                    {"title": "Contact", "url": "https://example.com/one", "content": supported},
                    {"title": "Other", "url": "https://example.com/two", "content": unrelated},
                ]
                result = search_identity(**identifiers)
                self.assertEqual(result["unique_result_count"], 1)
                self.assertEqual(result["results"][0]["url"], "https://example.com/one")

    def test_stream_delivers_initial_progress_and_early_result_while_slow_query_runs(self):
        slow_started, release_slow, fast_completed = Event(), Event(), Event()
        active_lock = Lock()
        active, max_active = 0, 0

        def query(query, max_results, *, api_key):
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

        def query(query, max_results, *, api_key):
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
