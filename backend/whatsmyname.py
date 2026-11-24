from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from urllib.parse import quote
import time

import requests


WMN_DATA_URL = (
    "https://raw.githubusercontent.com/"
    "WebBreacher/WhatsMyName/main/wmn-data.json"
)

USER_AGENT = "Digital-Footprint-Visualizer-UC-Capstone/1.0"

DEFAULT_TIMEOUT = 8
DEFAULT_WORKERS = 20


@lru_cache(maxsize=1)
def load_dataset():
    """
    Download the current official WhatsMyName dataset.
    Cached after the first successful download.
    """

    response = requests.get(
        WMN_DATA_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def get_dataset_stats():
    """
    Return basic information about the current WMN dataset.
    """

    data = load_dataset()

    categories = data.get("categories", [])
    sites = data.get("sites", [])

    active_sites = [
        site
        for site in sites
        if site.get("valid", True)
    ]

    return {
        "category_count": len(categories),
        "site_count": len(sites),
        "active_site_count": len(active_sites),
    }


def _clean_username(username, site):
    """
    Apply site-specific username character removal.
    """

    cleaned = username.strip()

    bad_chars = site.get("strip_bad_char", "")

    for character in bad_chars:
        cleaned = cleaned.replace(character, "")

    return cleaned


def _fill_url(template, username):
    """
    Replace the {account} placeholder with a URL-safe username.
    """

    encoded_username = quote(username, safe="")

    return template.replace(
        "{account}",
        encoded_username,
    )


def _fill_body(template, username):
    """
    Replace the {account} placeholder in POST data.
    """

    return template.replace(
        "{account}",
        username,
    )


def _string_matches(response_text, expected_string):
    """
    An empty WMN string means the HTTP status code
    itself is sufficient for that rule.
    """

    if expected_string is None:
        return True

    if expected_string == "":
        return True

    return expected_string in response_text


def check_site(site, username, timeout=DEFAULT_TIMEOUT):
    """
    Test one username against one WhatsMyName site definition.
    """

    site_name = site.get("name", "Unknown")
    category = site.get("cat", "unknown")

    if site.get("valid", True) is False:
        return {
            "site": site_name,
            "category": category,
            "status": "skipped",
            "http_status": None,
            "profile_url": None,
            "elapsed_ms": None,
        }

    cleaned_username = _clean_username(
        username,
        site,
    )

    uri_check = site.get("uri_check")

    if not uri_check:
        return {
            "site": site_name,
            "category": category,
            "status": "error",
            "http_status": None,
            "profile_url": None,
            "elapsed_ms": None,
        }

    check_url = _fill_url(
        uri_check,
        cleaned_username,
    )

    pretty_template = site.get("uri_pretty")

    if pretty_template:
        profile_url = _fill_url(
            pretty_template,
            cleaned_username,
        )
    elif not site.get("post_body"):
        profile_url = check_url
    else:
        profile_url = None

    headers = {
        "User-Agent": USER_AGENT,
    }

    site_headers = site.get("headers", {})

    if site_headers:
        headers.update(site_headers)

    started = time.perf_counter()

    try:
        if site.get("post_body"):

            body = _fill_body(
                site["post_body"],
                cleaned_username,
            )

            response = requests.post(
                check_url,
                data=body,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )

        else:
            response = requests.get(
                check_url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )

        elapsed_ms = round(
            (time.perf_counter() - started) * 1000
        )

        response_text = response.text

        exists_code_match = (
            response.status_code
            == site.get("e_code")
        )

        exists_string_match = _string_matches(
            response_text,
            site.get("e_string"),
        )

        missing_code_match = (
            response.status_code
            == site.get("m_code")
        )

        missing_string_match = _string_matches(
            response_text,
            site.get("m_string"),
        )

        exists_match = (
            exists_code_match
            and exists_string_match
        )

        missing_match = (
            missing_code_match
            and missing_string_match
        )

        if response.status_code == 429:
            status = "rate_limited"

        elif response.status_code in {
            401,
            403,
        }:
            status = "blocked"

        elif exists_match and not missing_match:
            status = "found"

        elif missing_match and not exists_match:
            status = "not_found"

        else:
            status = "could_not_verify"

        return {
            "source": "whatsmyname",
            "site": site_name,
            "category": category,
            "status": status,
            "http_status": response.status_code,
            "profile_url": profile_url,
            "elapsed_ms": elapsed_ms,
            "protection": site.get(
                "protection",
                [],
            ),
        }

    except requests.Timeout:

        elapsed_ms = round(
            (time.perf_counter() - started) * 1000
        )

        return {
            "source": "whatsmyname",
            "site": site_name,
            "category": category,
            "status": "timeout",
            "http_status": None,
            "profile_url": profile_url,
            "elapsed_ms": elapsed_ms,
            "protection": site.get(
                "protection",
                [],
            ),
        }

    except requests.RequestException as error:

        elapsed_ms = round(
            (time.perf_counter() - started) * 1000
        )

        return {
            "source": "whatsmyname",
            "site": site_name,
            "category": category,
            "status": "error",
            "http_status": None,
            "profile_url": profile_url,
            "elapsed_ms": elapsed_ms,
            "error": str(error),
            "protection": site.get(
                "protection",
                [],
            ),
        }


def _summarize_username_results(username, results, sites_total):
    """Create an independent progress snapshot using the existing outcome rules."""
    priority = {
        "found": 0, "could_not_verify": 1, "blocked": 2,
        "rate_limited": 3, "timeout": 4, "error": 5,
        "not_found": 6, "skipped": 7,
    }
    ordered_results = sorted(
        results,
        key=lambda item: (priority.get(item.get("status"), 99), item.get("site", "")),
    )
    status_counts = {}
    for result in ordered_results:
        status = result.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "source": "whatsmyname",
        "username": username,
        "sites_total": sites_total,
        "sites_checked": len(ordered_results),
        "found_count": status_counts.get("found", 0),
        "not_found_count": status_counts.get("not_found", 0),
        "uncertain_count": sum(status_counts.get(status, 0) for status in (
            "could_not_verify", "blocked", "rate_limited", "timeout", "error",
        )),
        "status_counts": status_counts,
        "results": ordered_results,
    }


def scan_username_stream(username, max_sites=None, workers=DEFAULT_WORKERS):
    """Yield an initial snapshot and then one snapshot per completed site check.

    Check every active site definition across all categories by default.
    An explicit max_sites can restrict the dataset for integration smoke checks.
    Each snapshot owns its result list, so consumers can retain earlier progress.
    """
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty.")
    dataset = load_dataset()
    usable_sites = [
        site for site in dataset.get("sites", [])
        if site.get("valid", True)
    ]
    selected_sites = usable_sites[:max_sites] if max_sites is not None else usable_sites
    results = []
    yield _summarize_username_results(username, results, len(selected_sites))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(check_site, site, username): site
            for site in selected_sites
        }
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:
                site = futures[future]
                result = {
                    "source": "whatsmyname",
                    "site": site.get("name", "Unknown"),
                    "category": site.get("cat", "unknown"),
                    "status": "error",
                    "error": str(error),
                }
            results.append(result)
            yield _summarize_username_results(username, results, len(selected_sites))


def scan_username(username, max_sites=None, workers=DEFAULT_WORKERS):
    """Return the final summary for all active sites, or an explicit site limit."""
    final_snapshot = None
    for snapshot in scan_username_stream(username, max_sites=max_sites, workers=workers):
        final_snapshot = snapshot
    return final_snapshot
