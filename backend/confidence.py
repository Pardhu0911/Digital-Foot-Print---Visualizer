import re
from typing import Any, Dict, List


def _normalize_text(value: str) -> str:
    """
    Normalize text for simple identity comparison.
    """

    if not value:
        return ""

    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)

    return value


def _digits_only(value: str) -> str:
    """
    Return only numeric characters.
    """

    if not value:
        return ""

    return re.sub(r"\D", "", value)


def classify_web_result(
    result: Dict[str, Any],
    full_name: str = "",
    email: str = "",
    phone: str = "",
    location: str = "",
) -> Dict[str, Any]:
    """
    Apply a transparent identity-confidence heuristic.

    This is NOT a probability or risk score.

    Labels:
        Likely Match
        Possible Match
        Unverified
    """

    title = _normalize_text(
        str(result.get("title", ""))
    )

    snippet = _normalize_text(
        str(result.get("snippet", ""))
    )

    url = _normalize_text(
        str(result.get("url", ""))
    )

    combined_text = " ".join(
        [
            title,
            snippet,
            url,
        ]
    )

    normalized_name = _normalize_text(full_name)
    normalized_email = _normalize_text(email)
    normalized_location = _normalize_text(location)

    phone_digits = _digits_only(phone)
    result_digits = _digits_only(combined_text)

    query_types = result.get("query_types", [])

    if not isinstance(query_types, list):
        query_types = [str(query_types)]

    signals: List[str] = []

    # ------------------------------------------------------
    # NAME
    # ------------------------------------------------------

    name_match = False

    if normalized_name:
        name_match = normalized_name in combined_text

        if name_match:
            signals.append(
                "Full name appears in the returned evidence."
            )

    # ------------------------------------------------------
    # LOCATION
    # ------------------------------------------------------

    location_match = False

    if normalized_location:
        location_match = normalized_location in combined_text

        if location_match:
            signals.append(
                "Location information agrees with the search input."
            )

    # ------------------------------------------------------
    # EMAIL
    # ------------------------------------------------------

    email_match = False

    if normalized_email:
        email_match = normalized_email in combined_text

        if email_match:
            signals.append(
                "Exact email address appears in the returned evidence."
            )

    # ------------------------------------------------------
    # PHONE
    # ------------------------------------------------------

    phone_match = False

    if phone_digits and len(phone_digits) >= 7:
        phone_match = phone_digits in result_digits

        if phone_match:
            signals.append(
                "Phone-number digits appear in the returned evidence."
            )

    # ------------------------------------------------------
    # MULTIPLE QUERY SUPPORT
    # ------------------------------------------------------

    repeated_support = len(set(query_types)) >= 2

    if repeated_support:
        signals.append(
            "The page was returned through multiple search-query types."
        )

    # ------------------------------------------------------
    # CLASSIFICATION
    # ------------------------------------------------------

    if email_match or phone_match:
        label = "Likely Match"

        reason = (
            "A strong exact identifier agrees with the "
            "retrieved public evidence."
        )

    elif name_match and location_match:
        label = "Likely Match"

        reason = (
            "The returned evidence agrees with both the "
            "supplied name and location."
        )

    elif name_match and repeated_support:
        label = "Likely Match"

        reason = (
            "The full name matches and the page received "
            "support from multiple search queries."
        )

    elif name_match or location_match or repeated_support:
        label = "Possible Match"

        reason = (
            "Some identity evidence agrees, but there is "
            "not enough evidence to confirm ownership."
        )

    else:
        label = "Unverified"

        reason = (
            "The available evidence is too weak or "
            "ambiguous to associate the page confidently "
            "with the searched person."
        )

    return {
        "label": label,
        "reason": reason,
        "signals": signals,
    }