"""Browser-only controlled sources. Never imported by the production app.

Run only through tests/browser_check.py. Example content is test data.
"""
from pathlib import Path
import runpy
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend import analysis, evaluation


def preview_web_stream(**inputs):
    """Expose a real wait between partial query snapshots for browser assertions."""
    state = dict(
        unique_result_count=0, unique_domain_count=0, queries_total=3,
        queries_completed=0, queries_run=0, queries_with_results=0,
        no_result_queries=0, failed_queries=0, filtered_result_count=0,
        results=[], searches=[], warnings=[],
    )
    yield dict(state)
    time.sleep(1)
    page = dict(title="Example Person | Public portfolio", domain="example.com",
                url="https://example.com", snippet="Example Person shares a public portfolio in Example City.",
                query_types=["full_name", "name_location"])
    state.update(unique_result_count=1, unique_domain_count=1, queries_completed=1,
                 queries_run=1, queries_with_results=1, results=[page])
    yield dict(state)
    time.sleep(4)
    state.update(queries_completed=3, queries_run=3, no_result_queries=2,
                 filtered_result_count=1)
    yield dict(state)


def preview_profiles(username, **kwargs):
    rows = [
        dict(site="Example Coding", category="coding", status="found", profile_url="https://example.com/code"),
        dict(site="Example Social", category="social", status="found", profile_url="https://example.com/social"),
        dict(site="Example Forum", category="social", status="blocked", profile_url=None),
    ]
    for index in range(4):
        if index:
            time.sleep(1 if index == 1 else 3)
        yield dict(sites_checked=index, sites_total=3, found_count=min(index, 2),
                   not_found_count=0, uncertain_count=max(0, index - 2),
                   status_counts={}, results=rows[:index])


analysis.search_public_web_stream = preview_web_stream
analysis.scan_username_stream = preview_profiles
evaluation.OUTPUT_DIRECTORY = ROOT / "output" / "ui" / "fixture-evaluation"
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
