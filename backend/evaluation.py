import json
import time
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, Optional


OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "output" / "evaluation"


def timed_call(function, *args, **kwargs):
    """
    Run a function and return both its result
    and execution time.
    """

    start = time.perf_counter()

    result = function(
        *args,
        **kwargs,
    )

    elapsed = time.perf_counter() - start

    return result, round(elapsed, 2)


def build_evaluation_summary(
    web_results: Optional[Dict[str, Any]] = None,
    web_elapsed: Optional[float] = None,
    username_results: Optional[Dict[str, Any]] = None,
    username_elapsed: Optional[float] = None,
    web_error: Optional[str] = None,
    username_error: Optional[str] = None,
    total_elapsed: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Build a structured evaluation record for one
    Digital Footprint Visualizer analysis.
    """

    summary = {
        "run_id": uuid4().hex,
        "schema_version": 2,
        "total_analysis_time_seconds": total_elapsed,
        "timestamp_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "public_web": None,
        "username_scan": None,
    }

    # ------------------------------------------------------
    # PUBLIC WEB
    # ------------------------------------------------------

    if web_results is not None:

        summary["public_web"] = {
            "execution_time_seconds": web_elapsed,

            "queries_run": web_results.get(
                "queries_run",
                0,
            ),
            "queries_total": web_results.get("queries_total"),
            "queries_completed": web_results.get("queries_completed", web_results.get("queries_run", 0)),
            "filtered_result_count": web_results.get("filtered_result_count", 0),

            "queries_with_results": web_results.get(
                "queries_with_results",
                0,
            ),

            "unique_result_count": web_results.get(
                "unique_result_count",
                0,
            ),

            "unique_domain_count": web_results.get(
                "unique_domain_count",
                0,
            ),

            "failed_queries": web_results.get(
                "failed_queries",
                0,
            ),

            "no_result_queries": web_results.get(
                "no_result_queries",
                0,
            ),

            "error": web_error,
        }

    elif web_error:

        summary["public_web"] = {
            "execution_time_seconds": web_elapsed,
            "error": web_error,
        }

    # ------------------------------------------------------
    # USERNAME
    # ------------------------------------------------------

    if username_results is not None:

        summary["username_scan"] = {
            "execution_time_seconds": username_elapsed,

            "sites_checked": username_results.get(
                "sites_checked",
                0,
            ),
            "sites_total": username_results.get("sites_total"),

            "possible_profiles": username_results.get(
                "found_count",
                0,
            ),

            "not_found": username_results.get(
                "not_found_count",
                0,
            ),

            "could_not_verify": username_results.get(
                "uncertain_count",
                0,
            ),

            "status_counts": username_results.get("status_counts", {}),
            "error": username_error,
        }

    elif username_error:

        summary["username_scan"] = {
            "execution_time_seconds": username_elapsed,
            "error": username_error,
        }

    return summary


def _save_json(
    data: Dict[str, Any],
    prefix: str,
) -> str:
    """
    Save JSON data into output/evaluation.
    """

    OUTPUT_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    filename = (
        OUTPUT_DIRECTORY
        / f"{prefix}_{timestamp}_{uuid4().hex}.json"
    )

    with open(
        filename,
        "x",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            indent=4,
            ensure_ascii=False,
            default=str,
        )

    return str(filename)


def save_evaluation_summary(
    summary: Dict[str, Any],
) -> str:
    """
    Save technical performance results.
    """

    return _save_json(
        summary,
        "evaluation",
    )


def build_manual_validation_summary(
    username: str,
    candidates,
    decisions,
    execution_time=None,
    evaluation_summary=None,
) -> Dict[str, Any]:
    """
    Build manual-validation statistics for
    candidate username profiles.
    """

    reviewed_results = []

    confirmed = 0
    unrelated = 0
    cannot_determine = 0
    not_reviewed = 0

    for index, candidate in enumerate(candidates):

        candidate_id = str(index)

        decision = decisions.get(
            candidate_id,
            "Not reviewed",
        )

        if decision == "Confirmed mine":
            confirmed += 1

        elif decision == "Unrelated":
            unrelated += 1

        elif decision == "Cannot determine":
            cannot_determine += 1

        else:
            not_reviewed += 1

        reviewed_results.append(
            {
                "site": candidate.get(
                    "site",
                    "",
                ),

                "category": candidate.get(
                    "category",
                    "",
                ),

                "profile_url": candidate.get(
                    "profile_url",
                    "",
                ),

                "http_status": candidate.get(
                    "http_status"
                ),

                "automated_status": candidate.get(
                    "status",
                    "",
                ),

                "manual_classification": decision,
            }
        )

    total_candidates = len(candidates)

    reviewed_count = (
        confirmed
        + unrelated
        + cannot_determine
    )

    # Positive predictive value among candidates
    # that could actually be resolved manually.
    resolvable = confirmed + unrelated

    if resolvable > 0:
        confirmed_rate = round(
            confirmed / resolvable * 100,
            2,
        )
    else:
        confirmed_rate = None

    summary = {
        "timestamp_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "username": username,

        "scan_execution_time_seconds": execution_time,

        "candidate_count": total_candidates,

        "reviewed_count": reviewed_count,

        "confirmed_mine": confirmed,

        "unrelated": unrelated,

        "cannot_determine": cannot_determine,

        "not_reviewed": not_reviewed,

        "confirmed_rate_among_resolved_percent":
            confirmed_rate,

        "candidate_reviews": reviewed_results,
    }

    if evaluation_summary is not None:
        summary["run_id"] = evaluation_summary["run_id"]
        summary["total_analysis_time_seconds"] = evaluation_summary.get("total_analysis_time_seconds")
        summary["public_web"] = evaluation_summary.get("public_web")
        summary["username_scan"] = evaluation_summary.get("username_scan")

    return summary


def save_manual_validation_summary(
    summary: Dict[str, Any],
) -> str:
    """
    Save manual candidate validation results.
    """

    return _save_json(
        summary,
        "manual_validation",
    )
