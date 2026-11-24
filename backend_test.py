import json
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# DIGITAL FOOTPRINT VISUALIZER
# Week 4 Backend Data Ingestion Test
#
# Approved project sources:
# WhatsMyName:
# https://github.com/WebBreacher/WhatsMyName
#
# Have I Been Pwned API v3:
# https://haveibeenpwned.com/API/v3
# ============================================================


WHATS_MY_NAME_JSON = (
    "https://raw.githubusercontent.com/"
    "WebBreacher/WhatsMyName/main/wmn-data.json"
)

HIBP_API_URL = (
    "https://haveibeenpwned.com/api/v3/breachedaccount/"
    "account-exists%40hibp-integration-tests.com"
)

HIBP_TEST_KEY = "00000000000000000000000000000000"


def load_whatsmyname():
    print("\n[1] Loading WhatsMyName dataset...")

    response = requests.get(
        WHATS_MY_NAME_JSON,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    categories = data.get("categories", [])
    sites = data.get("sites", [])

    print("Status: SUCCESS")
    print(f"Categories loaded: {len(categories)}")
    print(f"Site definitions loaded: {len(sites)}")

    print("\nFirst five site definitions:")

    for site in sites[:5]:
        print(
            f"- {site.get('name')} "
            f"| Category: {site.get('cat')}"
        )

    return {
        "status": "success",
        "category_count": len(categories),
        "site_count": len(sites),
        "sample_sites": [
            {
                "name": site.get("name"),
                "category": site.get("cat"),
                "profile_pattern": site.get("uri_check")
            }
            for site in sites[:5]
        ]
    }


def test_hibp():
    print("\n[2] Testing Have I Been Pwned API...")

    headers = {
        "hibp-api-key": HIBP_TEST_KEY,
        "user-agent": "Digital-Footprint-Visualizer-UC-Capstone"
    }

    params = {
        "truncateResponse": "false"
    }

    response = requests.get(
        HIBP_API_URL,
        headers=headers,
        params=params,
        timeout=30
    )

    print(f"HTTP Status: {response.status_code}")

    if response.status_code == 200:
        breaches = response.json()

        print(f"Breaches returned: {len(breaches)}")

        normalized_breaches = []

        for breach in breaches:
            normalized = {
                "source": "hibp",
                "evidence_type": "breach",
                "name": breach.get("Name"),
                "title": breach.get("Title"),
                "domain": breach.get("Domain"),
                "breach_date": breach.get("BreachDate"),
                "verified": breach.get("IsVerified"),
                "data_classes": breach.get("DataClasses", [])
            }

            normalized_breaches.append(normalized)

            print("\nBreach result:")
            print(f"Name: {normalized['name']}")
            print(f"Domain: {normalized['domain']}")
            print(
                f"Breach Date: "
                f"{normalized['breach_date']}"
            )
            print(
                f"Verified: "
                f"{normalized['verified']}"
            )

        return {
            "status": "success",
            "http_status": response.status_code,
            "breach_count": len(normalized_breaches),
            "breaches": normalized_breaches
        }

    elif response.status_code == 404:
        print("No breaches found.")

        return {
            "status": "success",
            "http_status": 404,
            "breach_count": 0,
            "breaches": []
        }

    elif response.status_code == 429:
        print("HIBP rate limit reached.")

        return {
            "status": "rate_limited",
            "http_status": 429,
            "retry_after": response.headers.get(
                "retry-after"
            )
        }

    else:
        print("HIBP request was not successful.")
        print(response.text)

        return {
            "status": "error",
            "http_status": response.status_code,
            "response": response.text
        }


def save_results(wmn_results, hibp_results):
    print("\n[3] Saving normalized results...")

    output_folder = Path("output")
    output_folder.mkdir(exist_ok=True)

    results = {
        "project": "Digital Footprint Visualizer",
        "milestone": "Week 4 Backend Data Ingestion",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "whatsmyname": wmn_results,
        "hibp": hibp_results
    }

    output_file = (
        output_folder / "week4_results.json"
    )

    with output_file.open(
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            results,
            file,
            indent=4
        )

    print(f"Saved to: {output_file}")


def main():
    print("=" * 60)
    print("DIGITAL FOOTPRINT VISUALIZER")
    print("WEEK 4 BACKEND DATA INGESTION")
    print("=" * 60)

    try:
        wmn_results = load_whatsmyname()
    except Exception as error:
        print(f"\nWhatsMyName ERROR: {error}")

        wmn_results = {
            "status": "error",
            "error": str(error)
        }

    try:
        hibp_results = test_hibp()
    except Exception as error:
        print(f"\nHIBP ERROR: {error}")

        hibp_results = {
            "status": "error",
            "error": str(error)
        }

    save_results(
        wmn_results,
        hibp_results
    )

    print("\n" + "=" * 60)
    print("WEEK 4 BACKEND TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()