import os
from urllib.parse import quote

import requests
from dotenv import load_dotenv


load_dotenv()


HIBP_BASE_URL = "https://haveibeenpwned.com/api/v3"

USER_AGENT = "Digital-Footprint-Visualizer-UC-Capstone/1.0"

TIMEOUT = 10

HIBP_TEST_EMAIL = "account-exists@hibp-integration-tests.com"
HIBP_TEST_KEY = "00000000000000000000000000000000"


def _normalize_breach(breach):
    return {
        "source": "hibp",
        "evidence_type": "breach",
        "name": breach.get("Name"),
        "title": breach.get("Title"),
        "domain": breach.get("Domain"),
        "breach_date": breach.get("BreachDate"),
        "added_date": breach.get("AddedDate"),
        "verified": breach.get("IsVerified"),
        "fabricated": breach.get("IsFabricated"),
        "sensitive": breach.get("IsSensitive"),
        "retired": breach.get("IsRetired"),
        "spam_list": breach.get("IsSpamList"),
        "data_classes": breach.get(
            "DataClasses",
            [],
        ),
        "verification_status": (
            "verified"
            if breach.get("IsVerified")
            else "unverified"
        ),
    }


def lookup_email(
    email,
    use_test_mode=False,
):
    """
    Query HIBP for breach metadata.

    Real searches require HIBP_API_KEY in .env.

    The email itself is not returned in the
    normalized result, which reduces unnecessary
    storage of personal information.
    """

    email = email.strip()

    if not email and not use_test_mode:
        raise ValueError(
            "Email address cannot be empty."
        )

    if use_test_mode:
        query_email = HIBP_TEST_EMAIL
        api_key = HIBP_TEST_KEY
    else:
        query_email = email
        api_key = os.getenv(
            "HIBP_API_KEY",
            "",
        ).strip()

        if not api_key:
            return {
                "source": "hibp",
                "status": "not_configured",
                "message": (
                    "HIBP API key is not configured."
                ),
                "breach_count": 0,
                "breaches": [],
            }

    encoded_email = quote(
        query_email,
        safe="",
    )

    url = (
        f"{HIBP_BASE_URL}/"
        f"breachedaccount/"
        f"{encoded_email}"
    )

    headers = {
        "hibp-api-key": api_key,
        "user-agent": USER_AGENT,
    }

    params = {
        "truncateResponse": "false",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=TIMEOUT,
        )

        if response.status_code == 404:
            return {
                "source": "hibp",
                "status": "success",
                "test_mode": use_test_mode,
                "breach_count": 0,
                "breaches": [],
            }

        if response.status_code == 429:
            return {
                "source": "hibp",
                "status": "rate_limited",
                "retry_after": response.headers.get(
                    "retry-after"
                ),
                "breach_count": 0,
                "breaches": [],
            }

        if response.status_code in {
            401,
            403,
        }:
            return {
                "source": "hibp",
                "status": "authentication_error",
                "http_status": response.status_code,
                "breach_count": 0,
                "breaches": [],
            }

        response.raise_for_status()

        raw_breaches = response.json()

        breaches = [
            _normalize_breach(breach)
            for breach in raw_breaches
        ]

        return {
            "source": "hibp",
            "status": "success",
            "test_mode": use_test_mode,
            "breach_count": len(breaches),
            "breaches": breaches,
        }

    except requests.Timeout:
        return {
            "source": "hibp",
            "status": "timeout",
            "breach_count": 0,
            "breaches": [],
        }

    except requests.RequestException as error:
        return {
            "source": "hibp",
            "status": "error",
            "error": str(error),
            "breach_count": 0,
            "breaches": [],
        }