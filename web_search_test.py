from collections import Counter

from backend.web_search import search_identity


print()
print("=" * 75)
print("DIGITAL FOOTPRINT VISUALIZER")
print("Bing Public Web Search Test")
print("=" * 75)

print()
print(
    "Use your own information or information "
    "you have authorization to search."
)

print()

full_name = input(
    "Full name: "
).strip()

email = input(
    "Email (optional): "
).strip()

phone = input(
    "Phone number (optional): "
).strip()

location = input(
    "City / State (optional): "
).strip()


print()
print(
    "Searching publicly indexed web results "
    "using Bing..."
)
print()


result = search_identity(
    full_name=full_name,
    email=email,
    phone=phone,
    location=location,
    max_results_per_query=10,
)


# ============================================================
# WARNINGS
# ============================================================

if result["warnings"]:

    print()
    print("WARNINGS")
    print("=" * 75)

    for warning in result[
        "warnings"
    ]:
        print("-", warning)


# ============================================================
# SUMMARY
# ============================================================

print()
print("SEARCH SUMMARY")
print("=" * 75)

print(
    "Search engine:",
    result["search_engine"]
)

print(
    "Queries run:",
    result["queries_run"]
)

print(
    "Queries with results:",
    result["queries_with_results"]
)

print(
    "Queries with no results:",
    result["no_result_queries"]
)

print(
    "Failed queries:",
    result["failed_queries"]
)

print(
    "Unique public results:",
    result["unique_result_count"]
)

print(
    "Unique domains:",
    result["unique_domain_count"]
)


# ============================================================
# DOMAINS
# ============================================================

print()
print("DOMAINS FOUND")
print("=" * 75)

if result["domains"]:

    domain_counts = Counter(
        item["domain"]
        for item in result["results"]
        if item["domain"]
    )

    for domain, count in (
        domain_counts.most_common()
    ):

        print(
            f"{domain}: "
            f"{count} result(s)"
        )

else:

    print(
        "No public domains were discovered."
    )


# ============================================================
# QUERY STATUS
# ============================================================

print()
print("QUERY STATUS")
print("=" * 75)

for search in result[
    "searches"
]:

    print()

    print(
        "Type:",
        search.get(
            "query_type",
            "unknown"
        )
    )

    print(
        "Query:",
        search.get(
            "query",
            ""
        )
    )

    print(
        "Status:",
        search.get(
            "status",
            "unknown"
        )
    )

    print(
        "Results:",
        search.get(
            "result_count",
            0
        )
    )

    if search.get("error"):

        print(
            "Error:",
            search["error"]
        )


# ============================================================
# RESULTS
# ============================================================

print()
print("PUBLIC WEB RESULTS")
print("=" * 75)

if not result["results"]:

    print()
    print(
        "No public web results were returned."
    )

else:

    for index, item in enumerate(
        result["results"],
        start=1
    ):

        print()

        print(
            f"{index}. "
            f"{item.get('title', 'No title')}"
        )

        print(
            "Domain:",
            item.get(
                "domain",
                ""
            )
        )

        print(
            "Matched by:",
            ", ".join(
                item.get(
                    "query_types",
                    []
                )
            )
        )

        print(
            "Search engine:",
            item.get(
                "search_engine",
                "Bing"
            )
        )

        print(
            "URL:",
            item.get(
                "url",
                ""
            )
        )

        snippet = item.get(
            "snippet",
            ""
        )

        if snippet:

            print(
                "Snippet:",
                snippet[:350]
            )


print()
print("=" * 75)
print("TEST COMPLETE")
print("=" * 75)