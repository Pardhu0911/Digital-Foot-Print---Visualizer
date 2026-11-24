"""Coordinate both evidence sources without calling Streamlit from worker threads."""

from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
import time

from backend.whatsmyname import scan_username_stream


def search_public_web(**inputs):
    """Import the optional search dependency only inside its source worker."""
    from backend.web_search import search_identity

    return search_identity(**inputs, max_results_per_query=10)


def search_public_web_stream(**inputs):
    """Forward each completed public-web query without waiting for the whole search."""
    from backend.web_search import search_identity_stream

    yield from search_identity_stream(**inputs, max_results_per_query=10)


def analyze_identity_stream(inputs, username="", max_sites=None, workers=None):
    """Yield snapshots as either source changes, plus elapsed-time heartbeats.

    Source workers put messages into a queue. Only the caller renders the UI.
    A failed source leaves the other running and retains partial username results.
    """
    messages = Queue()
    started = time.perf_counter()
    username = username.strip()
    username_requested = bool(username)
    web_requested = username_requested or any(inputs.get(field, "").strip() for field in ("full_name", "email", "phone"))
    state = dict(
        inputs=dict(inputs), username=username, web=None, profiles=None,
        web_error=None, username_error=None, web_elapsed=None, username_elapsed=None,
        web_requested=web_requested, username_requested=username_requested,
        web_done=not web_requested, username_done=not username_requested, total_elapsed=0.0,
    )

    def public_worker():
        source_started = time.perf_counter()
        result, error = None, None
        try:
            for result in search_public_web_stream(**inputs, username=username):
                messages.put(("web", result, None, time.perf_counter() - source_started))
            if result and result.get("failed_queries") and result["failed_queries"] == result.get("queries_total"):
                error = "All public-web provider queries failed."
        except Exception as exc:
            error = str(exc)
        messages.put(("web_done", result, error, time.perf_counter() - source_started))

    def username_worker():
        source_started = time.perf_counter()
        error = None
        try:
            for snapshot in scan_username_stream(username, max_sites=max_sites, workers=workers):
                messages.put(("profiles", snapshot, None, time.perf_counter() - source_started))
        except Exception as exc:
            error = str(exc)
        messages.put(("username_done", None, error, time.perf_counter() - source_started))

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="footprint-source") as executor:
        if web_requested:
            executor.submit(public_worker)
        if username_requested:
            executor.submit(username_worker)
        yield dict(state)
        while not (state["web_done"] and state["username_done"]):
            try:
                event, result, error, elapsed = messages.get(timeout=0.2)
            except Empty:
                elapsed = round(time.perf_counter() - started, 2)
                state["total_elapsed"] = elapsed
                if web_requested and not state["web_done"]:
                    state["web_elapsed"] = elapsed
                if username_requested and not state["username_done"]:
                    state["username_elapsed"] = elapsed
                yield dict(state)
                continue
            if event == "profiles":
                state["profiles"] = result
                state["username_elapsed"] = round(elapsed, 2)
            elif event == "web":
                state.update(web=result, web_elapsed=round(elapsed, 2))
            elif event == "web_done":
                state.update(web=result, web_error=error, web_elapsed=round(elapsed, 2), web_done=True)
            elif event == "username_done":
                state.update(username_error=error, username_elapsed=round(elapsed, 2), username_done=True)
            state["total_elapsed"] = round(time.perf_counter() - started, 2)
            yield dict(state)


def validate_identifiers(full_name, username, email="", phone="", authorized=False):
    """Validate submitted inputs before either source starts."""
    import re

    full_name, username, email, phone = (value.strip() for value in (full_name, username, email, phone))
    errors = []
    if not any((full_name, username, email, phone)):
        errors.append("Enter at least one name, username, email address, or phone number. Location alone is not enough.")
    if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        errors.append("Enter a valid email address, or leave it blank.")
    digits = re.sub(r"\D", "", phone)
    if phone and not (len(digits) == 10 or (len(digits) == 11 and digits.startswith("1"))):
        errors.append("Use a 10-digit US phone number, optionally starting with country code 1.")
    if not authorized:
        errors.append("Confirm that you own these identifiers or have permission to search them.")
    return errors
