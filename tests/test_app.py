"""Streamlit interaction checks with offline source snapshots and real JSON saves."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from streamlit.delta_generator import DeltaGenerator


ROOT = Path(__file__).resolve().parents[1]


def completed_scan(inputs, username, **kwargs):
    """Realistic deterministic evidence; production code never imports this fixture."""
    rows = [
        {"site": "Code Example", "category": "coding", "status": "found", "profile_url": "https://code.example/jane"},
        {"site": "Social Example", "category": "social", "status": "found", "profile_url": "https://social.example/jane"},
        {"site": "Missing Example", "category": "social", "status": "not_found"},
        {"site": "Blocked Example", "category": "gaming", "status": "blocked"},
    ]
    state = {
        "inputs": dict(inputs), "username": username,
        "profiles": {"username": username, "sites_total": 4, "sites_checked": 4,
                     "found_count": 2, "not_found_count": 1, "uncertain_count": 1,
                     "status_counts": {"found": 2, "not_found": 1, "blocked": 1}, "results": rows},
        "web": {"queries_total": 3, "queries_completed": 3, "queries_run": 3, "queries_with_results": 1, "no_result_queries": 2,
                "filtered_result_count": 2,
                "failed_queries": 0, "unique_result_count": 1, "unique_domain_count": 1,
                "warnings": [], "searches": [], "results": [
                    {"title": "Jane's public page", "domain": "example.com", "url": "https://example.com/jane",
                     "snippet": "Jane Doe lives in Cincinnati", "query_types": ["full_name"]}]},
        "web_error": None, "username_error": None, "web_elapsed": 2.1, "username_elapsed": 3.5,
        "web_done": True, "username_done": True, "total_elapsed": 3.6,
        "web_requested": True, "username_requested": bool(username),
    }
    if not username:
        state.update(profiles=None, username_elapsed=None)
    return state


def sample_stream(inputs, username, **kwargs):
    state = completed_scan(inputs, username)
    initial = deepcopy(state)
    initial.update(web_done=False, username_done=not bool(username), web_elapsed=0.0, total_elapsed=0.0)
    initial["web"].update(queries_completed=0, queries_run=0, queries_with_results=0,
                          no_result_queries=0, unique_result_count=0, unique_domain_count=0,
                          filtered_result_count=0, results=[])
    if initial["profiles"] is not None:
        initial["profiles"].update(sites_checked=0, results=[], found_count=0,
                                   not_found_count=0, uncertain_count=0)
    yield initial
    partial = deepcopy(state)
    partial.update(web_done=False, username_done=not bool(username), web_elapsed=1.0, total_elapsed=1.1)
    partial["web"].update(queries_completed=1, queries_run=1, no_result_queries=0,
                          filtered_result_count=0)
    if partial["profiles"] is not None:
        partial["profiles"].update(sites_checked=1, results=partial["profiles"]["results"][:1],
                                   found_count=1, not_found_count=0, uncertain_count=0)
    yield partial
    yield state


class FrontendTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="app-test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.storage_patch = patch("backend.evaluation.OUTPUT_DIRECTORY", self.output)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.stream_patch = patch("backend.analysis.analyze_identity_stream", side_effect=sample_stream)
        self.stream = self.stream_patch.start()
        self.addCleanup(self.stream_patch.stop)

    def app(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10).run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def submit(self, app, name="Jane Doe", username="jane", authorized=True):
        app.text_input(key="input_full_name").set_value(name)
        app.text_input(key="input_username").set_value(username)
        app.checkbox(key="input_authorized").set_value(authorized)
        app.button[0].click().run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def records(self, prefix="evaluation"):
        return list(self.output.glob(f"{prefix}_*.json"))

    def test_empty_identifiers_and_missing_permission_prevent_source_calls(self):
        app = self.app()
        cases = [("", "", True), ("Jane Doe", "jane", False)]
        for name, username, authorized in cases:
            with self.subTest(name=name, username=username, authorized=authorized):
                self.submit(app, name, username, authorized)
                self.assertTrue(app.error)
                self.stream.assert_not_called()
                self.assertFalse(self.records())
        self.submit(app)
        self.stream.assert_called_once()
        self.assertEqual(len(self.records()), 1)

    def test_live_updates_batch_counts_but_render_matches_immediately(self):
        from app import render_live_scan

        states = [
            dict(username="jane", username_done=checked == 10, web_done=True, profiles={
                "sites_total": 10, "sites_checked": checked, "found_count": int(checked >= 2),
            })
            for checked in range(11)
        ]
        with patch("app.monotonic", return_value=1), patch("app.render_username_source") as username, patch(
            "app.render_web_source"
        ) as web:
            self.assertIs(render_live_scan(iter(states), {}), states[-1])
        self.assertEqual([call.args[0]["profiles"]["sites_checked"] for call in username.call_args_list], [0, 2, 7, 10])
        web.assert_called_once()

    def test_either_name_or_username_alone_starts_analysis(self):
        for name, username in (("Jane Doe", ""), ("", "jane")):
            with self.subTest(name=name, username=username):
                self.stream.reset_mock()
                app = self.submit(self.app(), name=name, username=username)
                self.assertFalse(app.error)
                self.stream.assert_called_once()
                self.assertEqual(app.session_state.scan["inputs"]["full_name"], name)
                self.assertEqual(app.session_state.scan["username"], username)
                if not username:
                    self.assertIsNone(app.session_state.scan["profiles"])
                    self.assertIsNone(app.session_state.scan["evaluation"]["username_scan"])
                    visible = " ".join(element.value for element in app.markdown)
                    self.assertNotIn("Username Exposure", [header.value for header in app.header])
                    self.assertNotIn("Sites Checked</div>", visible)
                    self.assertNotIn('class="profile-card"', visible)
                    self.assertIn("Public Web Exposure", [header.value for header in app.header])

    def test_name_only_renders_live_query_progress_and_early_page_then_filtered_notice(self):
        app = self.app()
        rendered = []
        original_markdown = DeltaGenerator.markdown
        original_module_markdown = st.markdown

        def capture_markdown(generator, body, *args, **kwargs):
            if isinstance(body, str):
                rendered.append(body)
            return original_markdown(generator, body, *args, **kwargs)

        def capture_module_markdown(body, *args, **kwargs):
            if isinstance(body, str):
                rendered.append(body)
            return original_module_markdown(body, *args, **kwargs)

        with patch.object(DeltaGenerator, "markdown", new=capture_markdown), patch.object(st, "markdown", new=capture_module_markdown):
            self.submit(app, name="Jane Doe", username="")
        progress = [(index, body) for index, body in enumerate(rendered)
                    if 'web-progress-card' in body and '<section' in body]
        self.assertTrue(progress, "A name-only search must visibly report public-web progress")
        self.assertTrue(any("1 of 3" in body for _, body in progress), progress)
        first_card = next(index for index, body in enumerate(rendered) if '<article class="web-card">' in body)
        summary = next(index for index, body in enumerate(rendered) if '<section class="summary-panel">' in body)
        self.assertLess(first_card, summary, "A public page must appear before the final summary")
        self.assertTrue(any(index < first_card and "1 of 3" in body for index, body in progress))
        self.assertFalse(any("Username Exposure" in body or "Sites Checked</div>" in body
                             for body in rendered if not body.startswith("<style>")))
        visible = " ".join(element.value for group in (app.markdown, app.caption) for element in group)
        self.assertIn("excluded", visible)
        self.assertIn("supplied identifiers", visible)
        self.assertEqual(app.session_state.scan["web"]["queries_completed"], 3)
        self.assertEqual(app.session_state.scan["web"]["unique_result_count"], 1)
        self.assertEqual(app.session_state.scan["web"]["filtered_result_count"], 2)

    def test_email_or_phone_alone_is_accepted_but_location_alone_is_not(self):
        app = self.app()
        app.text_input(key="input_location").set_value("Cincinnati")
        self.submit(app, name="", username="")
        self.assertTrue(app.error)
        self.stream.assert_not_called()
        for key, value in (("input_email", "jane@example.com"), ("input_phone", "513-555-0123")):
            with self.subTest(identifier=key):
                app = self.app()
                app.text_input(key=key).set_value(value)
                self.stream.reset_mock()
                self.submit(app, name="", username="")
                self.assertFalse(app.error)
                self.stream.assert_called_once()

    def test_optional_fields_and_research_diagnostics_stay_collapsed(self):
        app = self.app()
        accuracy = next(expander for expander in app.expander if expander.label == "Improve match accuracy")
        self.assertFalse(accuracy.proto.expanded)
        self.assertFalse(app.json)
        self.assertEqual(len(app.text_input), 5)
        self.submit(app)
        for label in ("Improve match accuracy", "Research Evaluation", "Technical Details"):
            expander = next(expander for expander in app.expander if expander.label == label)
            self.assertFalse(expander.proto.expanded)
        normal_text = " ".join(element.value for element in app.markdown).lower()
        for unsupported in ("password", "hibp", "api key", "risk score"):
            self.assertNotIn(unsupported, normal_text)

    def test_category_filter_does_not_change_counts_or_manual_review_population(self):
        app = self.submit(self.app())
        self.assertEqual(len(app.selectbox), 2)
        app.pills(key="category_filter").select("coding").run()
        self.assertFalse(app.exception, str(app.exception))
        profile_markup = next(element.value for element in app.markdown if '<div class="profile-grid">' in element.value)
        self.assertIn("Code Example", profile_markup)
        self.assertNotIn("Social Example", profile_markup)
        self.assertEqual(len(app.selectbox), 2)
        self.assertEqual(app.session_state.scan["profiles"]["found_count"], 2)
        self.assertEqual(app.session_state.scan["profiles"]["sites_checked"], 4)
        self.assertEqual(len(self.records()), 1)
        self.stream.assert_called_once()

    def test_manual_review_json_links_full_scan_and_reruns_do_not_log_twice(self):
        app = self.submit(self.app())
        evaluation = json.loads(self.records()[0].read_text(encoding="utf-8"))
        app.selectbox(key="review_0").select("Confirmed mine").run()
        app.selectbox(key="review_1").select("Unrelated").run()
        app.pills(key="category_filter").select("coding").run()
        self.assertEqual(app.selectbox(key="review_0").value, "Confirmed mine")
        self.assertEqual(app.selectbox(key="review_1").value, "Unrelated")
        save = next(button for button in app.button if button.label == "Save manual evaluation")
        save.click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(len(self.records()), 1)
        manual = json.loads(self.records("manual_validation")[0].read_text(encoding="utf-8"))
        self.assertEqual(manual["run_id"], evaluation["run_id"])
        self.assertEqual(manual["total_analysis_time_seconds"], 3.6)
        self.assertEqual(manual["scan_execution_time_seconds"], 3.5)
        self.assertEqual(manual["public_web"]["execution_time_seconds"], 2.1)
        self.assertEqual(manual["username_scan"]["sites_checked"], 4)
        self.assertEqual(manual["username_scan"]["possible_profiles"], 2)
        self.assertEqual(manual["confirmed_mine"], 1)
        self.assertEqual(manual["unrelated"], 1)
        self.assertEqual(manual["cannot_determine"], 0)
        self.stream.assert_called_once()

    def test_editing_unsubmitted_inputs_keeps_original_evidence_context(self):
        app = self.submit(self.app())
        original_id = app.session_state.scan["evaluation"]["run_id"]
        app.text_input(key="input_full_name").set_value("Other Person").run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(app.session_state.scan["inputs"]["full_name"], "Jane Doe")
        self.assertEqual(app.session_state.scan["evaluation"]["run_id"], original_id)
        self.assertEqual(len(self.records()), 1)
        self.stream.assert_called_once()

    def test_source_and_storage_failures_leave_results_and_downloads_available(self):
        def failing_web(inputs, username, **kwargs):
            state = completed_scan(inputs, username)
            state.update(web=None, web_error="Search provider unavailable")
            yield state

        self.stream.side_effect = failing_web
        with patch("backend.evaluation.save_evaluation_summary", side_effect=PermissionError("read only")):
            app = self.submit(self.app())
        self.assertTrue(app.warning)
        self.assertIsNone(app.session_state.scan["evaluation_file"])
        self.assertEqual(app.session_state.scan["profiles"]["found_count"], 2)
        self.assertTrue(app.download_button)
        self.assertFalse(app.exception, str(app.exception))


if __name__ == "__main__":
    unittest.main()
