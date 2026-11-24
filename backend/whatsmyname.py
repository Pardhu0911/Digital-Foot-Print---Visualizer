from bisect import insort_right
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing, nullcontext
import os
from threading import Lock, local
import time
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from requests.utils import DEFAULT_CA_BUNDLE_PATH
from urllib3.exceptions import ReadTimeoutError
from urllib3.util.ssl_ import create_urllib3_context


WMN_DATA_URL = (
    "https://raw.githubusercontent.com/"
    "WebBreacher/WhatsMyName/main/wmn-data.json"
)

USER_AGENT = "Digital-Footprint-Visualizer-UC-Capstone/1.0"
DEFAULT_TIMEOUT = (4, 8)  # Connect and read timeouts in seconds.
DEFAULT_WORKERS = 40
DATASET_TTL_SECONDS = 24 * 60 * 60

_RESULT_PRIORITY = {
    "found": 0, "could_not_verify": 1, "blocked": 2,
    "rate_limited": 3, "timeout": 4, "error": 5,
    "not_found": 6, "skipped": 7,
}

_dataset_lock = Lock()
_dataset = None
_dataset_expires = 0.0


def load_dataset():
    """Share the downloaded site definitions and categories for 24 hours."""
    global _dataset, _dataset_expires
    with _dataset_lock:
        if _dataset is None or time.monotonic() >= _dataset_expires:
            response = requests.get(WMN_DATA_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
            response.raise_for_status()
            _dataset = response.json()
            _dataset_expires = time.monotonic() + DATASET_TTL_SECONDS
        return _dataset


def get_dataset_stats():
    """Return basic information about the current WMN dataset."""
    data = load_dataset()
    sites = data.get("sites", [])
    return {
        "category_count": len(data.get("categories", [])),
        "site_count": len(sites),
        "active_site_count": sum(bool(site.get("valid", True)) for site in sites),
    }


class _ScanTransport(HTTPAdapter):
    """Share one scan's connection pool and isolate mutable Session state per thread."""

    def __init__(self, workers):
        super().__init__(pool_connections=workers * 2, pool_maxsize=workers, max_retries=0)
        # Requests otherwise parses the same trust bundle for each HTTPS connection.
        self._tls_context = create_urllib3_context()
        self._tls_context.load_verify_locations(cafile=DEFAULT_CA_BUNDLE_PATH)
        self._worker = local()
        self._proxy_lock = Lock()

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host, options = super().build_connection_pool_key_attributes(request, verify, cert)
        if host["scheme"] == "https" and verify is True and cert is None:
            options["ssl_context"] = self._tls_context
        return host, options

    def cert_verify(self, conn, url, verify, cert):
        # The default HTTPS pool already verifies certificates with the loaded bundle.
        if url.lower().startswith("https://") and verify is True and cert is None:
            return
        super().cert_verify(conn, url, verify, cert)

    def proxy_manager_for(self, proxy, **kwargs):
        # Requests lazily initializes proxy pools. Lock the lookup only.
        with self._proxy_lock:
            return super().proxy_manager_for(proxy, **kwargs)

    def request(self, method, url, **kwargs):
        session = getattr(self._worker, "session", None)
        if session is None:
            session = requests.Session()
            session.close()  # Dispose of unused adapters before mounting the scan pool.
            session.mount("http://", self)
            session.mount("https://", self)
            self._worker.session = session
        # Cookies are useful within a redirect chain, but each site check starts fresh.
        session.cookies.clear()
        return session.request(method, url, **kwargs)


def _clean_username(username, site):
    cleaned = username.strip()
    for character in site.get("strip_bad_char", ""):
        cleaned = cleaned.replace(character, "")
    return cleaned


def _fill_url(template, username):
    return template.replace("{account}", quote(username, safe=""))


def _classify_response(response, site):
    code = response.status_code
    if code == 429:
        return "rate_limited"
    if code in {401, 403}:
        return "blocked"

    exists_code = code == site.get("e_code")
    missing_code = code == site.get("m_code")
    exists_marker = site.get("e_string")
    missing_marker = site.get("m_string")

    # A body is needed only when an applicable rule contains a marker. Read it
    # fully in that case, since absence and conflicting markers affect the verdict.
    text = response.text if (exists_code and exists_marker) or (missing_code and missing_marker) else ""
    exists_match = exists_code and (not exists_marker or exists_marker in text)
    missing_match = missing_code and (not missing_marker or missing_marker in text)
    if exists_match and not missing_match:
        return "found"
    if missing_match and not exists_match:
        return "not_found"
    return "could_not_verify"


def check_site(site, username, timeout=DEFAULT_TIMEOUT, *, transport=None):
    """Test one username against one WhatsMyName site definition."""
    result = {
        "site": site.get("name", "Unknown"),
        "category": site.get("cat", "unknown"),
        "http_status": None,
        "profile_url": None,
        "elapsed_ms": None,
    }
    if site.get("valid", True) is False:
        return dict(result, status="skipped")
    if not site.get("uri_check"):
        return dict(result, status="error")

    username = _clean_username(username, site)
    check_url = _fill_url(site["uri_check"], username)
    pretty_template = site.get("uri_pretty")
    post_body = site.get("post_body")
    result.update(
        source="whatsmyname",
        profile_url=_fill_url(pretty_template, username) if pretty_template else (None if post_body else check_url),
        protection=site.get("protection", []),
    )
    headers = {"User-Agent": USER_AGENT}
    headers.update(site.get("headers") or {})
    method = "POST" if post_body else "GET"
    body = post_body.replace("{account}", username) if post_body else None
    started = time.perf_counter()
    try:
        with (nullcontext(transport) if transport is not None else requests.Session()) as client:
            with client.request(method, check_url, data=body, headers=headers, timeout=timeout,
                                allow_redirects=True, stream=True) as response:
                status = _classify_response(response, site)
                result.update(status=status, http_status=response.status_code)
    except requests.Timeout:
        result["status"] = "timeout"
    except requests.RequestException as error:
        # Requests wraps timeouts while reading a streamed body in ConnectionError.
        if isinstance(error, requests.ConnectionError) and error.args and isinstance(error.args[0], ReadTimeoutError):
            result["status"] = "timeout"
        else:
            result.update(status="error", error=str(error))

    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def _result_sort_key(result):
    return _RESULT_PRIORITY.get(result.get("status"), 99), result.get("site") or ""


def _snapshot(username, results, sites_total, status_counts):
    checked = len(results)
    found = status_counts["found"]
    not_found = status_counts["not_found"]
    return {
        "source": "whatsmyname",
        "username": username,
        "sites_total": sites_total,
        "sites_checked": checked,
        "found_count": found,
        "not_found_count": not_found,
        "uncertain_count": checked - found - not_found,
        "status_counts": dict(status_counts),
        "results": results.copy(),
    }


def scan_username_stream(username, max_sites=None, workers=None):
    """Yield an initial snapshot and one snapshot per completed active site check."""
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty.")
    if workers is None:
        try:
            workers = int(os.environ.get("USERNAME_SCAN_CONCURRENCY", DEFAULT_WORKERS))
        except ValueError as error:
            raise ValueError("USERNAME_SCAN_CONCURRENCY must be a positive integer.") from error
    if workers < 1:
        raise ValueError("Username scan concurrency must be positive.")

    dataset = load_dataset()
    sites = [site for site in dataset.get("sites", []) if site.get("valid", True)]
    sites = sites[:max_sites] if max_sites is not None else sites
    results = []
    status_counts = Counter()
    yield _snapshot(username, results, len(sites), status_counts)
    if not sites:
        return

    # Most site-check time is spent waiting for network I/O. Bound parallel
    # requests with the executor and keep the shared pool alive until workers exit.
    workers = min(workers, len(sites))
    with closing(_ScanTransport(workers)) as transport, ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(check_site, site, username, transport=transport): site for site in sites}
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
            insort_right(results, result, key=_result_sort_key)
            status_counts[result.get("status", "unknown")] += 1
            yield _snapshot(username, results, len(sites), status_counts)


def scan_username(username, max_sites=None, workers=None):
    """Return the final snapshot for all active sites, or an explicit site limit."""
    for snapshot in scan_username_stream(username, max_sites=max_sites, workers=workers):
        pass
    return snapshot
