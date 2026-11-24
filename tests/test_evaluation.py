import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.evaluation import (
    build_evaluation_summary, build_manual_validation_summary,
    save_evaluation_summary, save_manual_validation_summary,
)


class EvaluationTests(unittest.TestCase):
    def test_scan_and_review_save_complete_linked_records_without_overwrite(self):
        summary = build_evaluation_summary(
            web_results={"queries_run": 4, "queries_total": 8, "queries_completed": 4,
                         "filtered_result_count": 2, "unique_result_count": 3, "unique_domain_count": 2},
            web_elapsed=3.5,
            username_results={"sites_checked": 100, "sites_total": 100, "found_count": 5, "not_found_count": 57,
                              "uncertain_count": 38, "status_counts": {"found": 5, "not_found": 57, "blocked": 38}},
            username_elapsed=12.4, total_elapsed=12.5,
        )
        candidates = [{"site": str(index), "status": "found"} for index in range(5)]
        review = build_manual_validation_summary("example", candidates,
            {"0": "Confirmed mine", "1": "Unrelated", "2": "Cannot determine"},
            execution_time=12.4, evaluation_summary=summary)
        with TemporaryDirectory() as directory, patch("backend.evaluation.OUTPUT_DIRECTORY", Path(directory)):
            files = [save_evaluation_summary(summary), save_evaluation_summary(summary), save_manual_validation_summary(review)]
            self.assertEqual(len(set(files)), 3)
            loaded = json.loads(Path(files[2]).read_text(encoding="utf-8"))
            self.assertEqual(loaded["run_id"], summary["run_id"])
            self.assertEqual(loaded["total_analysis_time_seconds"], 12.5)
            self.assertEqual(loaded["public_web"]["execution_time_seconds"], 3.5)
            self.assertEqual(loaded["public_web"]["queries_total"], 8)
            self.assertEqual(loaded["public_web"]["queries_completed"], 4)
            self.assertEqual(loaded["public_web"]["filtered_result_count"], 2)
            self.assertEqual(loaded["username_scan"]["sites_checked"], 100)
            self.assertEqual(loaded["username_scan"]["sites_total"], 100)
            self.assertEqual(loaded["username_scan"]["possible_profiles"], 5)
            self.assertEqual(loaded["confirmed_mine"], 1)
            self.assertEqual(loaded["unrelated"], 1)
            self.assertEqual(loaded["cannot_determine"], 1)
            self.assertEqual(loaded["not_reviewed"], 2)
            self.assertEqual(loaded["confirmed_rate_among_resolved_percent"], 50)

    def test_failed_source_retains_its_timing(self):
        record = build_evaluation_summary(web_error="offline", web_elapsed=1.25, total_elapsed=2.5)
        self.assertEqual(record["public_web"]["execution_time_seconds"], 1.25)
        self.assertEqual(record["public_web"]["error"], "offline")
        self.assertIsNone(record["username_scan"])


if __name__ == "__main__":
    unittest.main()
