from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import re
from time import perf_counter
import unicodedata
from urllib.parse import unquote, urlparse

import ddgs
from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException


# ============================================================
# CONFIGURATION
# ============================================================

SEARCH_BACKEND = "auto"
DEFAULT_RESULTS_PER_QUERY = 10
SEARCH_TIMEOUT = 12


# ============================================================
# HELPERS
# ============================================================

def _clean(value):
    """Convert a value to a clean string."""

    if value is None:
        return ""

    return str(value).strip()


def _get_domain(url):
    """Extract a clean domain name from a URL."""

    try:
        domain = urlparse(url).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return ""


def _phone_variants(phone):
    """
    Generate common US phone-number formats.

    Invalid values such as 'Cincinnati' will not be searched.
    """

    phone = _clean(phone)

    if not phone:
        return []

    digits = re.sub(r"\D", "", phone)

    variants = set()

    # Standard US 10-digit phone number
    if len(digits) == 10:

        area = digits[:3]
        prefix = digits[3:6]
        line = digits[6:]

        variants.add(digits)
        variants.add(
            f"{area}-{prefix}-{line}"
        )
        variants.add(
            f"({area}) {prefix}-{line}"
        )

    # US number with country code
    elif (
        len(digits) == 11
        and digits.startswith("1")
    ):

        local = digits[1:]

        area = local[:3]
        prefix = local[3:6]
        line = local[6:]

        variants.add(
            f"+1 {area}-{prefix}-{line}"
        )

        variants.add(
            f"{area}-{prefix}-{line}"
        )

        variants.add(
            f"({area}) {prefix}-{line}"
        )

        variants.add(digits)

    return sorted(variants)


# ============================================================
# QUERY GENERATION
# ============================================================

def build_queries(
    full_name="",
    email="",
    phone="",
    location="",
    username="",
):
    """
    Build targeted public-web search queries.

    Search results are candidate evidence only.
    They do not automatically prove ownership.
    """

    full_name = _clean(full_name)
    email = _clean(email)
    phone = _clean(phone)
    location = _clean(location)
    username = _clean(username)

    queries = []

    # --------------------------------------------------------
    # NAME SEARCHES
    # --------------------------------------------------------

    if full_name:

        # Normal exact-name search
        queries.append({
            "type": "full_name",
            "query": f'"{full_name}"',
        })

        # Search beyond LinkedIn
        queries.append({
            "type": "full_name_non_linkedin",
            "query":
                f'"{full_name}" -site:linkedin.com',
        })

        # Name + location
        if location:

            queries.append({
                "type": "name_location",
                "query":
                    f'"{full_name}" "{location}"',
            })

        # Search public PDF documents
        queries.append({
            "type": "name_pdf",
            "query":
                f'"{full_name}" filetype:pdf',
        })

        # Broaden discovery beyond exact-name search without treating a
        # provider returning a page as evidence that its identity agrees.
        queries.extend([
            {"type": "full_name_unquoted", "query": full_name},
            {"type": "name_linkedin", "query": f'"{full_name}" site:linkedin.com/in'},
            {"type": "name_github", "query": f'"{full_name}" site:github.com'},
            {"type": "name_profiles", "query": f'"{full_name}" (profile OR portfolio OR biography)'},
            {"type": "name_social", "query": f'"{full_name}" (site:facebook.com OR site:instagram.com OR site:x.com OR site:about.me)'},
        ])

    # --------------------------------------------------------
    # USERNAME SEARCHES
    # --------------------------------------------------------

    if username:
        queries.append({
            "type": "username",
            "query": f'"{username}"',
        })

        if full_name:
            queries.append({
                "type": "name_username",
                "query": f'"{full_name}" "{username}"',
            })

        if location:
            queries.append({
                "type": "username_location",
                "query": f'"{username}" "{location}"',
            })

    # --------------------------------------------------------
    # EMAIL SEARCH
    # --------------------------------------------------------

    if email:

        queries.append({
            "type": "email",
            "query": f'"{email}"',
        })

    # --------------------------------------------------------
    # PHONE SEARCH
    # --------------------------------------------------------

    phone_variants = _phone_variants(
        phone
    )

    for variant in phone_variants:

        queries.append({
            "type": "phone",
            "query": f'"{variant}"',
        })

    return queries


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

def _classify_error(error):
    """Recognize definite errors and DDGS's standard no-results response."""
    error_text = str(error).strip().lower()
    if isinstance(error, RatelimitException) or any(
        hint in error_text for hint in ("429", "rate limit", "too many requests")
    ):
        return "rate_limited"
    if isinstance(error, (TimeoutException, TimeoutError)) or any(
        hint in error_text for hint in ("timeout", "timed out")
    ):
        return "timeout"
    if error_text.rstrip(".") in {"no result", "no results", "no results found"}:
        return "no_results"
    return "error"


def _query_failure(error):
    status = _classify_error(error)
    result = {
        "status": status, "result_count": 0, "results": [],
        "exception_class": type(error).__name__, "exception_message": str(error),
    }
    if status != "no_results":
        result["error"] = str(error)
    return result


def search_query(query, max_results=DEFAULT_RESULTS_PER_QUERY):
    """Run one query through DDGS and record the provider response before filtering."""
    started = perf_counter()
    result = {"query": query, "raw_result_count": 0}
    try:
        rows = list(DDGS(timeout=SEARCH_TIMEOUT).text(
            query, region="us-en", safesearch="moderate",
            max_results=max_results, backend=SEARCH_BACKEND,
        ) or [])
        result["raw_result_count"] = len(rows)
        normalized_results = []
        for item in rows:
            url = _clean(item.get("href", ""))
            if not url:
                continue
            normalized_results.append({
                "title": _clean(item.get("title", "")),
                "url": url,
                "domain": _get_domain(url),
                "snippet": _clean(item.get("body", "")),
                "search_engine": f"DDGS ({SEARCH_BACKEND})",
            })
        result.update(
            status="success" if normalized_results else "no_results",
            result_count=len(normalized_results), results=normalized_results,
        )
    except Exception as error:
        result.update(_query_failure(error))
    result["elapsed_seconds"] = round(perf_counter() - started, 3)
    return result


# ============================================================
# COMPLETE IDENTITY SEARCH
# ============================================================

def _normalized_text(value):
    """Compare Unicode names and identifiers without changing source evidence."""
    return unicodedata.normalize("NFKC", unquote(_clean(value))).casefold()


def _name_tokens(value):
    return re.findall(r"[^\W_]+", _normalized_text(value), flags=re.UNICODE)


def _has_identifier_support(result, full_name="", email="", phone="", username=""):
    """Reject provider rows without textual support for a supplied identifier.

    This is a relevance gate, not identity verification. A multi-part name can
    appear with a middle name or punctuation; both its first and last tokens
    must occur as complete tokens. Location alone is never identity support.
    """
    fields = [_normalized_text(result.get(key, "")) for key in ("title", "snippet", "url")]
    name = _name_tokens(full_name)
    required_name = {name[0], name[-1]} if len(name) > 1 else set(name)
    if required_name and any(required_name.issubset(set(_name_tokens(field))) for field in fields):
        return True
    normalized_email = _normalized_text(email)
    if normalized_email:
        # Compare full email tokens so jane@example.com.evil is not an exact match.
        email_pattern = r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w.-]+\.[\w-]+"
        if any(normalized_email in re.findall(email_pattern, field) for field in fields):
            return True
    normalized_username = _normalized_text(username)
    if normalized_username:
        pattern = r"(?<![\w.-])" + re.escape(normalized_username) + r"(?![\w.-])"
        if any(re.search(pattern, field) for field in fields):
            return True
    digits = re.sub(r"\D", "", _clean(phone))
    if digits:
        local = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
        accepted = {local, "1" + local}
        pattern = r"(?<!\d)\+?\d(?:[\s().-]*\d){9,10}(?!\d)"
        if any(re.sub(r"\D", "", match) in accepted
               for field in fields for match in re.findall(pattern, field)):
            return True
    return False


def _aggregate_searches(searches, queries_total, warnings, identifiers):
    """Build a detached record; count excluded pages once across completed queries.

    A URL supported by any query is a candidate, even when other rows for the
    same URL lack evidence. Per-query diagnostics keep raw rejected-row counts.
    """
    unique_results = {}
    filtered_urls = set()
    diagnostics = []
    for search in sorted(searches, key=lambda row: row["plan_index"]):
        diagnostic = dict(search)
        rejected = []
        accepted_count = 0
        for item in search.get("results", []):
            if not _has_identifier_support(item, **identifiers):
                if item.get("url"):
                    filtered_urls.add(item["url"])
                rejected.append(item)
                continue
            accepted_count += 1
            url = item.get("url")
            if not url:
                continue
            if url not in unique_results:
                unique_results[url] = {
                    "source": "Public Web Search",
                    "search_engine": item.get("search_engine") or f"DDGS ({SEARCH_BACKEND})",
                    "title": item.get("title", ""), "url": url,
                    "domain": item.get("domain", ""), "snippet": item.get("snippet", ""),
                    "query_types": [search["query_type"]], "queries": [search["query"]],
                }
            else:
                existing = unique_results[url]
                if search["query_type"] not in existing["query_types"]:
                    existing["query_types"].append(search["query_type"])
                if search["query"] not in existing["queries"]:
                    existing["queries"].append(search["query"])
                if len(item.get("snippet", "")) > len(existing["snippet"]):
                    existing["snippet"] = item["snippet"]
        diagnostic["supported_result_count"] = accepted_count
        diagnostic["filtered_result_count"] = len(rejected)
        diagnostic["filtered_results"] = rejected
        diagnostics.append(diagnostic)
    final_results = list(unique_results.values())
    domains = sorted({row["domain"] for row in final_results if row["domain"]})
    successful = sum(row["status"] == "success" for row in searches)
    empty = sum(row["status"] == "no_results" for row in searches)
    return deepcopy({
        "source": "Public Web Search", "search_engine": f"DDGS ({SEARCH_BACKEND})",
        "queries_total": queries_total, "queries_completed": len(searches),
        "queries_run": len(searches), "queries_with_results": successful,
        "no_result_queries": empty, "failed_queries": len(searches) - successful - empty,
        "unique_result_count": len(final_results), "unique_domain_count": len(domains),
        "filtered_result_count": len(filtered_urls.difference(unique_results)), "domains": domains,
        "warnings": warnings, "searches": diagnostics, "results": final_results,
    })


def search_identity_stream(
    full_name="", email="", phone="", location="",
    max_results_per_query=DEFAULT_RESULTS_PER_QUERY, username="",
):
    """Yield an initial record and one record per concurrently completed query.

    At most three independent DDGS searches run at once. Provider rows,
    including filtered rows, stay in search diagnostics. Supported pages are
    candidate evidence only; this gate never establishes account ownership.
    """
    full_name, email, phone, location, username = (
        _clean(value) for value in (full_name, email, phone, location, username)
    )
    warnings = []
    if phone and not _phone_variants(phone):
        warnings.append(
            "The phone number was ignored because it was not a valid 10-digit US number "
            "or an 11-digit US number beginning with 1."
        )
        phone = ""
    queries = build_queries(full_name=full_name, email=email, phone=phone, location=location, username=username)
    identifiers = dict(full_name=full_name, email=email, phone=phone, username=username)
    searches = []
    yield _aggregate_searches(searches, len(queries), warnings, identifiers)
    if not queries:
        return
    print(f"public_web_backend ddgs_version={ddgs.__version__} backend={SEARCH_BACKEND}", flush=True)
    provider_empty = 0
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="public-web-query") as executor:
        futures = {
            executor.submit(search_query, query_info["query"], max_results_per_query): (
                index, query_info, perf_counter(),
            )
            for index, query_info in enumerate(queries)
        }
        for future in as_completed(futures):
            index, query_info, submitted = futures[future]
            try:
                result = dict(future.result())
            except Exception as error:
                result = _query_failure(error)
            raw_count = result.pop("raw_result_count", result["result_count"])
            elapsed = result.pop("elapsed_seconds", round(perf_counter() - submitted, 3))
            exception_class = result.pop("exception_class", None)
            exception_message = result.pop("exception_message", None)
            result.update(query=query_info["query"], query_type=query_info["type"], plan_index=index)
            searches.append(result)
            snapshot = _aggregate_searches(searches, len(queries), warnings, identifiers)
            diagnostic = next(row for row in snapshot["searches"] if row["plan_index"] == index)
            accepted = diagnostic["supported_result_count"]
            print("public_web_query " + json.dumps({
                "query_type": query_info["type"], "query": query_info["query"],
                "backend": SEARCH_BACKEND, "elapsed_seconds": elapsed,
                "raw_result_count": raw_count, "accepted_result_count": accepted,
                "filtered_result_count": raw_count - accepted, "status": result["status"],
                "exception_class": exception_class, "exception_message": exception_message,
            }), flush=True)
            if raw_count == 0 and result["status"] == "no_results":
                provider_empty += 1
            if provider_empty == len(queries):
                print(
                    "All public-web queries returned zero provider results; "
                    "possible provider blocking or cloud-IP limitation.",
                    flush=True,
                )
            yield snapshot


def search_identity(
    full_name="", email="", phone="", location="",
    max_results_per_query=DEFAULT_RESULTS_PER_QUERY, username="",
):
    """Return the final stream snapshot, preserving the existing positional API."""
    final_snapshot = None
    for snapshot in search_identity_stream(
        full_name, email, phone, location, max_results_per_query, username,
    ):
        final_snapshot = snapshot
    return final_snapshot
