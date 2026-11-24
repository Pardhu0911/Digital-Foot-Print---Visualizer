"""Explicit live smoke check using a project-title query and synthetic handle.

Makes external requests to three selected sites and the public search provider.
Does not run during unittest discovery. Saves a clearly marked live-test record.
"""
from pathlib import Path
import argparse
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.analysis import analyze_identity_stream
from backend.evaluation import build_evaluation_summary, save_evaluation_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name-only", action="store_true", help="Check progressive web searches without starting username checks")
    options = parser.parse_args()
    inputs = dict(full_name="Digital Footprint Visualizer", location="", email="", phone="")
    checked = []
    web_checked = []
    early_web_result = False
    handle = "" if options.name_only else "dfvcheck" + uuid4().hex[:16]
    for scan in analyze_identity_stream(inputs, handle, max_sites=3, workers=3):
        web = scan.get("web")
        if web is not None:
            completed = web["queries_completed"]
            if completed not in web_checked:
                web_checked.append(completed)
                print(f"Web progress: {completed}/{web['queries_total']}; {web['unique_result_count']} candidate pages", flush=True)
            if web["unique_result_count"] and completed < web["queries_total"]:
                early_web_result = True
        profiles = scan.get("profiles")
        if profiles is not None:
            count = profiles["sites_checked"]
            if count not in checked:
                checked.append(count)
                print(f"Username progress: {count}/{profiles['sites_total']}", flush=True)
    record = build_evaluation_summary(
        web_results=scan["web"], web_elapsed=scan["web_elapsed"],
        username_results=scan["profiles"], username_elapsed=scan["username_elapsed"],
        web_error=scan["web_error"], username_error=scan["username_error"],
        total_elapsed=scan["total_elapsed"],
    )
    record["record_type"] = "live_name_only_smoke_test" if options.name_only else "live_smoke_test"
    destination = save_evaluation_summary(record)
    print("Evaluation saved:", destination)
    print("Username outcomes:", (scan["profiles"] or {}).get("status_counts"))
    print("Web queries:", (scan["web"] or {}).get("queries_run"))
    print("Web pages:", (scan["web"] or {}).get("unique_result_count"))
    print("Source errors:", scan["username_error"], scan["web_error"])
    if options.name_only:
        assert scan["profiles"] is None and not checked, "Name-only searches must not start username checks"
    else:
        assert checked == [0, 1, 2, 3], "Expected one snapshot per completed site"
    assert web_checked == list(range(scan["web"]["queries_total"] + 1)), "Expected one progress snapshot per completed web query"
    assert scan["username_error"] is None and scan["web_error"] is None
    assert scan["web"]["failed_queries"] == 0, "Live search queries failed"
    print("Candidate page available before all queries completed:", early_web_result)
    print("Live coordinator and evaluation smoke check passed")


if __name__ == "__main__":
    main()
