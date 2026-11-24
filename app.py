"""A privacy-checker interface backed by two independent evidence streams."""

from collections import Counter
from html import escape
import json
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import streamlit as st

from backend.analysis import analyze_identity_stream, validate_identifiers
from backend.confidence import classify_web_result
from backend.evaluation import (
    build_evaluation_summary, build_manual_validation_summary,
    save_evaluation_summary, save_manual_validation_summary,
)

ROOT = Path(__file__).resolve().parent
PROGRESS_BATCH_SIZE = 5
PROGRESS_REFRESH_SECONDS = 0.2


def html(markup, target=None):
    """Render our markup; dynamic source text is escaped at insertion."""
    (target or st).markdown(markup, unsafe_allow_html=True)


def safe_link(url, label, button=False):
    """Allow public HTTP(S) links and escape attributes and labels."""
    if not isinstance(url, str):
        return '<span class="category">Profile link unavailable</span>'
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        valid = False
    if not valid:
        return '<span class="category">Link unavailable</span>'
    css = "card-link profile-button" if button else "card-link"
    return (f'<a class="{css}" href="{escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{escape(label)} <span aria-hidden="true">&rarr;</span></a>')


def badge(label):
    style = {"Likely Match": "likely", "Possible Match": "possible"}.get(label, "unverified")
    return f'<span class="badge badge-{style}">{escape(label)}</span>'


def metric_grid(items, target):
    cards = "".join(
        f'<div class="metric-card"><div class="metric-label">{escape(label)}</div>'
        f'<div class="metric-value">{escape(str(value))}</div></div>' for label, value in items
    )
    html(f'<div class="metric-grid">{cards}</div>', target)


def profile_cards(profiles):
    cards = []
    for profile in profiles:
        site = str(profile.get("site") or "Unknown site")
        category = str(profile.get("category") or "Uncategorized")
        cards.append(
            '<article class="profile-card"><div class="profile-top">'
            f'<div class="site-avatar" aria-hidden="true">{escape(site[:2].upper())}</div><div>'
            f'<div class="profile-name">{escape(site)}</div><div class="category">{escape(category)}</div>'
            f'</div></div>{badge("Possible Match")}'
            f'{safe_link(profile.get("profile_url"), "View Profile", button=True)}</article>'
        )
    return '<div class="profile-grid">' + "".join(cards) + '</div>'


def web_cards(results, inputs):
    cards = []
    for result in results:
        confidence = classify_web_result(result, **inputs)
        signals = confidence["signals"] or [confidence["reason"]]
        reasons = "".join(f'<li>{escape(signal)}</li>' for signal in signals)
        cards.append(
            '<article class="web-card"><div class="web-heading"><div>'
            f'<div class="web-title">{escape(str(result.get("title") or "Untitled page"))}</div>'
            f'<div class="web-domain">{escape(str(result.get("domain") or ""))}</div></div>'
            f'{badge(confidence["label"])}</div>'
            f'<p class="web-snippet">{escape(str(result.get("snippet") or ""))}</p>'
            f'<div class="why-label">Why this result</div><ul class="signals">{reasons}</ul>'
            f'{safe_link(result.get("url"), "View Public Page")}</article>'
        )
    return "".join(cards)


def render_progress(scan, target):
    profiles = scan.get("profiles") or {}
    username_requested = scan.get("username_requested", bool(scan.get("username")))
    checked, total = profiles.get("sites_checked", 0), profiles.get("sites_total", 0)
    percent = min(100, round(checked / total * 100)) if total else 0
    if not username_requested:
        title = "Searching public web pages..." if not scan.get("web_done") else "Public-web search complete"
    elif scan.get("username_error"):
        title = "Username search interrupted"
    elif scan.get("username_done"):
        title = "Username checks complete"
    elif total:
        title = "Searching across public sites..."
    else:
        title = "Preparing public site checks..."
    coverage = f"{checked} of {total} checks complete" if total else "Loading site definitions"
    if not username_requested:
        coverage = "No username supplied; website profile checks skipped"
    elif scan.get("username_done") and not total:
        coverage = "No site checks completed"
    web_status = "Public-web search is running."
    if username_requested:
        web_status = "Public-web search is running alongside your username checks."
    if scan.get("web_done"):
        web_status = "Public-web search is unavailable." if scan.get("web_error") else "Public-web search complete."
    html(
        f'<div class="progress-card"><div class="progress-title">{title}</div>'
        f'<div class="progress-track" role="progressbar" aria-valuenow="{percent}" aria-valuemin="0" '
        f'aria-valuemax="100" aria-label="Username checks"><div class="progress-fill" style="width:{percent}%"></div></div>'
        f'<div class="progress-detail"><span>{coverage}</span>'
        f'<span><strong>{profiles.get("found_count", 0)}</strong> possible matches found</span>'
        f'<span><strong>{profiles.get("uncertain_count", 0)}</strong> sites could not be verified</span></div>'
        f'<div class="source-status">{web_status}</div></div>', target,
    )


def render_web_progress(scan, target):
    """Show actual query completions independently of username site checks."""
    web = scan.get("web") or {}
    completed = web.get("queries_completed", web.get("queries_run", 0))
    total = web.get("queries_total", 0)
    percent = min(100, round(completed / total * 100)) if total else 0
    coverage = f"{completed} of {total} searches complete" if total else "Preparing searches for your supplied identifiers"
    elapsed = scan.get("web_elapsed") or 0
    html(
        '<section class="progress-card web-progress-card" aria-live="polite">'
        '<div class="progress-title"><span class="search-spinner" aria-hidden="true"></span>Searching the public web...</div>'
        '<div class="source-status">Checking public pages, profiles, and indexed documents. Results appear as searches finish.</div>'
        f'<div class="progress-track" role="progressbar" aria-valuenow="{percent}" aria-valuemin="0" '
        f'aria-valuemax="100" aria-label="Public-web searches"><div class="progress-fill" style="width:{percent}%"></div></div>'
        f'<div class="progress-detail query-progress-detail"><span>{coverage}</span>'
        f'<span><strong>{web.get("unique_result_count", 0)}</strong> candidate pages</span>'
        f'<span><strong>{web.get("unique_domain_count", 0)}</strong> domains</span>'
        f'<span><strong>{elapsed:.1f}s</strong> elapsed</span></div></section>', target,
    )


def render_username_source(scan, slots, live=False):
    """Render username metrics only when this source was actually requested."""
    profiles = scan.get("profiles") or {}
    candidates = [row for row in profiles.get("results", []) if row.get("status") == "found"]
    if live and not scan.get("username_done"):
        render_progress(scan, slots["progress"])
    else:
        slots["progress"].empty()
    elapsed = scan.get("username_elapsed")
    metric_grid([
        ("Possible Matches", profiles.get("found_count", "\u2014")),
        ("Sites Checked", profiles.get("sites_checked", "\u2014")),
        ("Not Found", profiles.get("not_found_count", "\u2014")),
        ("Could Not Verify", profiles.get("uncertain_count", "\u2014")),
        ("Scan Time", f"{elapsed:.1f}s" if elapsed is not None else "\u2014"),
    ], slots["username_metrics"])
    if scan.get("username_error"):
        slots["username_notice"].warning("Username checks could not finish. Any partial results appear below; try again later.")
    elif profiles.get("uncertain_count"):
        slots["username_notice"].caption("Some checks are unknown because sites blocked requests, timed out, or could not be verified.")
    else:
        slots["username_notice"].empty()
    filtered = candidates
    if candidates and not live:
        counts = Counter(str(row.get("category") or "Uncategorized") for row in candidates)
        options = ["All"] + sorted(counts)
        labels = {"All": f"All {len(candidates)}", **{name: f"{name} {count}" for name, count in counts.items()}}
        with slots["categories"].container():
            if hasattr(st, "pills"):
                selected = st.pills("Filter by category", options, default="All", format_func=labels.get,
                                    key="category_filter", label_visibility="collapsed") or "All"
            else:
                selected = st.radio("Filter by category", options, format_func=labels.get,
                                    horizontal=True, key="category_filter", label_visibility="collapsed")
        filtered = candidates if selected == "All" else [row for row in candidates if str(row.get("category") or "Uncategorized") == selected]
    if filtered:
        html(profile_cards(filtered), slots["username_cards"])
    else:
        message = "Possible profiles will appear here as soon as a match is found." if live and not scan.get("username_done") else "No possible profiles were returned by the completed checks. Results may be incomplete."
        html(f'<div class="empty-panel">{message}</div>', slots["username_cards"])
    return candidates


def render_web_source(scan, slots, live=False):
    web = scan.get("web") or {}
    if live and not scan.get("web_done"):
        render_web_progress(scan, slots["web_progress"])
    else:
        slots["web_progress"].empty()
    web_elapsed = scan.get("web_elapsed")
    completed = web.get("queries_completed", web.get("queries_run"))
    total = web.get("queries_total")
    coverage = f"{completed} / {total}" if total is not None else completed if completed is not None else "\u2014"
    metric_grid([
        ("Public Pages", web.get("unique_result_count", "\u2014")),
        ("Unique Domains", web.get("unique_domain_count", "\u2014")),
        ("Searches Completed", coverage),
        ("Searches With Results", web.get("queries_with_results", "\u2014")),
        ("Search Time", f"{web_elapsed:.1f}s" if web_elapsed is not None else "\u2014"),
    ], slots["web_metrics"])
    notices = []
    if scan.get("web_error"):
        message = "Public-web search could not finish. Any pages found so far remain available; try again later."
        notices.append(("warning", message))
    if web.get("failed_queries"):
        notices.append(("warning", f"{web['failed_queries']} queries could not complete. Public-web results may be incomplete."))
    if web.get("filtered_result_count"):
        notices.append(("caption", f"{web['filtered_result_count']} returned pages were excluded because the visible evidence did not match your supplied identifiers."))
    notices.extend(("caption", warning) for warning in web.get("warnings", []))
    if notices:
        with slots["web_notice"].container():
            for level, message in notices:
                getattr(st, level)(message)
    else:
        slots["web_notice"].empty()
    if web.get("results"):
        html(web_cards(web["results"], scan["inputs"]), slots["web_cards"])
    elif scan.get("web_done"):
        message = "Public pages could not be retrieved." if scan.get("web_error") else "No pages with visible evidence matching your identifiers were returned. Try a name variation or add a city, email, or username. A missing result does not prove that no public information exists."
        html(f'<div class="empty-panel">{message}</div>', slots["web_cards"])
    else:
        html('<div class="empty-panel">Looking for pages that reference your identifiers. The first candidates will appear here while other searches continue.</div>', slots["web_cards"])


def render_sources(scan, slots, live=False):
    """Render each requested evidence source into its independent live slots."""
    candidates = []
    if scan.get("username_requested", bool(scan.get("username"))):
        candidates = render_username_source(scan, slots, live=live)
    render_web_source(scan, slots, live=live)
    return candidates


def render_live_scan(snapshots, slots):
    """Refresh each source on its own schedule and show new profile matches immediately."""
    last_username = last_web = None
    username_refresh = web_refresh = 0
    for scan in snapshots:
        now = monotonic()
        if scan.get("username_requested", bool(scan.get("username"))):
            current = scan.get("profiles") or {}
            previous = (last_username or {}).get("profiles") or {}
            checked = current.get("sites_checked", 0)
            if (
                last_username is None
                or current.get("found_count", 0) != previous.get("found_count", 0)
                or current.get("sites_total") != previous.get("sites_total")
                or scan["username_done"] != last_username["username_done"]
                or scan["web_done"] != last_username["web_done"]
                or checked - previous.get("sites_checked", 0) >= PROGRESS_BATCH_SIZE
                or (not scan["username_done"] and now - username_refresh >= PROGRESS_REFRESH_SECONDS)
            ):
                render_username_source(scan, slots, live=True)
                last_username, username_refresh = scan, now
        if (
            last_web is None
            or scan.get("web") is not last_web.get("web")
            or scan["web_done"] != last_web["web_done"]
            or (not scan["web_done"] and now - web_refresh >= PROGRESS_REFRESH_SECONDS)
        ):
            render_web_source(scan, slots, live=True)
            last_web, web_refresh = scan, now
    return scan


def render_summary(scan):
    profiles, web = scan.get("profiles") or {}, scan.get("web") or {}
    username_requested = scan.get("username_requested", bool(scan.get("username")))
    username_body = "Username search unavailable." if username_requested else "Not run: no username supplied."
    if scan.get("profiles") is not None:
        username_body = (f'{profiles.get("sites_checked", 0)} sites checked<br>'
                         f'{profiles.get("found_count", 0)} possible profiles<br>'
                         f'{profiles.get("not_found_count", 0)} not found<br>'
                         f'{profiles.get("uncertain_count", 0)} could not verify')
        if scan.get("username_error"):
            username_body += '<br>Search interrupted; partial results.'
    web_body = "Public-web search unavailable."
    if scan.get("web") is not None:
        web_body = (f'{web.get("unique_result_count", 0)} candidate pages<br>'
                    f'{web.get("unique_domain_count", 0)} unique domains')
        if web.get("failed_queries"):
            web_body += '<br>Some queries could not complete.'
    username_section = ('<div><div class="summary-heading">Username Exposure</div>'
                        f'<div class="summary-body">{username_body}</div></div>') if username_requested else ''
    html('<section class="summary-panel"><div class="eyebrow">Digital Footprint Summary</div>'
         f'<div class="summary-grid">{username_section}<div><div class="summary-heading">Public Web Exposure</div>'
         f'<div class="summary-body">{web_body}</div></div></div>'
         f'<div class="summary-time">Total analysis time: {scan.get("total_elapsed", 0):.1f} seconds</div></section>')


def render_actions(scan):
    profiles, web = scan.get("profiles") or {}, scan.get("web") or {}
    actions = []
    if profiles.get("found_count"):
        actions.append(("Possible username profiles", "Verify whether each account belongs to you. Delete unused accounts you own and reduce public profile visibility."))
    if web.get("unique_result_count"):
        actions.append(("Public-web pages", "Review outdated public information. Change privacy settings and request corrections or removal where appropriate."))
    if profiles.get("uncertain_count") or scan.get("username_error") or scan.get("web_error") or web.get("failed_queries"):
        actions.append(("Checks that could not be verified", "These outcomes are unknown, not negative results. Manually review important sites or retry later."))
    st.subheader("What can I do next?")
    if actions:
        cards = "".join(f'<div><div class="action-heading">{escape(title)}</div><div class="action-body">{escape(body)}</div></div>' for title, body in actions)
        html(f'<div class="action-grid">{cards}</div>')
    else:
        st.caption("No source-specific action emerged from this check. A missing result does not establish that your information is private.")


def download_json(label, record, filename):
    st.download_button(label, json.dumps(record, indent=2), filename, "application/json")


def render_research(scan, candidates):
    with st.expander("Research Evaluation", expanded=False):
        st.caption("Optional manual review for research. Classify ownership only when you can establish it.")
        decisions = {}
        for index, profile in enumerate(candidates):
            st.write(profile.get("site", "Unknown site"))
            html(safe_link(profile.get("profile_url"), "Review candidate profile"))
            decisions[str(index)] = st.selectbox(
                "Manual classification", ["Not reviewed", "Confirmed mine", "Unrelated", "Cannot determine"],
                key=f"review_{index}",
            )
        evaluation = scan.get("evaluation", {})
        validation = build_manual_validation_summary(
            scan["username"], candidates, decisions, execution_time=scan.get("username_elapsed"),
            evaluation_summary=evaluation,
        )
        columns = st.columns(3)
        columns[0].metric("Manually confirmed", validation["confirmed_mine"])
        columns[1].metric("Unrelated", validation["unrelated"])
        columns[2].metric("Cannot determine", validation["cannot_determine"])
        st.caption(f"Reviewed {validation['reviewed_count']} of {validation['candidate_count']} candidates. These counts describe this reviewed sample.")
        if candidates:
            if st.button("Save manual evaluation"):
                try:
                    scan["manual_file"] = save_manual_validation_summary(validation)
                    st.success("Manual evaluation saved.")
                except OSError:
                    st.warning("Could not save locally. Download the evaluation instead.")
            download_json("Download manual evaluation", validation, "manual_evaluation.json")
        else:
            st.caption("There are no possible username profiles to review in this scan.")
        download_json("Download scan evaluation", evaluation, "footprint_evaluation.json")


def render_technical(scan):
    with st.expander("Technical Details", expanded=False):
        st.caption("Source diagnostics and evaluation records. Counts measure returned evidence, not overall identity coverage.")
        if scan.get("evaluation_file"):
            st.caption(f"Evaluation saved locally: {scan['evaluation_file']}")
        else:
            st.warning("Local evaluation storage was unavailable. Evaluation downloads remain available.")
        if scan.get("manual_file"):
            st.caption(f"Manual evaluation: {scan['manual_file']}")
        st.json(scan.get("evaluation", {}))
        st.json({"public_web_error": scan.get("web_error"), "username_error": scan.get("username_error")})
        st.json({"username_outcomes": (scan.get("profiles") or {}).get("results", []),
                 "public_web_queries": (scan.get("web") or {}).get("searches", [])})


def main():
    st.set_page_config(page_title="Digital Footprint Visualizer", page_icon=":mag:", layout="wide", initial_sidebar_state="collapsed")
    html(f'<style>{(ROOT / "assets" / "app.css").read_text(encoding="utf-8")}</style>')
    html('<div class="brand"><div class="brand-symbol" aria-hidden="true">'
         '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">'
         '<path d="M12 3 4.5 6v5.5c0 4.5 3.3 7.5 7.5 9.5 4.2-2 7.5-5 7.5-9.5V6L12 3Z"/>'
         '<circle cx="11" cy="10.5" r="2.6"/><path d="m13 12.5 3 3"/></svg></div>'
         '<span class="brand-name">Digital Footprint Visualizer</span><span class="brand-note">Your public presence, in perspective.</span></div>')
    html('<section class="hero"><div class="eyebrow">A clearer view of your public presence</div>'
         '<h1>What may be visible<br>about you online?</h1>'
         '<p>Discover publicly available pages and username profiles connected to your identity.</p>'
         '<div class="hero-trust"><span>Public sources</span><span>Possible matches</span>'
         '<span>Results may be incomplete</span></div></section>')

    with st.form("identity_form"):
        st.subheader("Start your privacy check")
        st.caption("Enter any one identifier, or combine details for better coverage. You can also search by email or phone below.")
        full_name = st.text_input("Full Name", placeholder="Your name (optional if using another identifier)", key="input_full_name")
        username = st.text_input("Username / Online Handle", placeholder="Your online handle (optional)", key="input_username",
                                 help="Checks all active WhatsMyName websites across every category, with no 100-site cap.")
        with st.expander("Improve match accuracy", expanded=False):
            location = st.text_input("City / State", placeholder="e.g. Cincinnati, Ohio", key="input_location",
                                     help="Refines name or username searches; location alone cannot identify a person.")
            email = st.text_input("Email", placeholder="name@example.com", key="input_email")
            phone = st.text_input("Phone", placeholder="US number, including area code", key="input_phone")
            st.caption("Email and phone are used only as public-web search identifiers.")
        st.caption("Username checks cover all active sites in the dataset. A full scan can take several minutes; matches appear as they are found.")
        authorized = st.checkbox("I confirm these identifiers belong to me or I have permission to search them.", key="input_authorized")
        submitted = st.form_submit_button("Analyze My Digital Footprint", type="primary", width="stretch")

    start_scan = False
    if submitted:
        inputs = dict(full_name=full_name.strip(), location=location.strip(), email=email.strip(), phone=phone.strip())
        handle = username.strip()
        errors = validate_identifiers(inputs["full_name"], handle, inputs["email"], inputs["phone"], authorized)
        if errors:
            for error in errors:
                st.error(error)
        else:
            start_scan = True
            for key in list(st.session_state):
                if key.startswith("review_") or key == "category_filter":
                    del st.session_state[key]
            st.session_state.pop("scan", None)

    scan = st.session_state.get("scan")
    if scan is None and not start_scan:
        html('<div class="welcome">Search with a name, username, email address, or phone number.<br>'
             'We will surface candidate evidence so you can review it for yourself.</div>')
    else:
        html('<div class="eyebrow section-kicker">Your public presence</div>')
        username_requested = bool(handle) if start_scan else scan.get("username_requested", bool(scan.get("username")))
        slots = {}
        if username_requested:
            st.header("Username Exposure")
            html('<p class="section-intro">A match is a lead, not proof that the account belongs to you. Open the profile to verify it.</p>')
            slots.update({key: st.empty() for key in ("progress", "username_metrics", "username_notice", "categories", "username_cards")})
        st.header("Public Web Exposure")
        html('<p class="section-intro">Candidate pages from public search results. Confidence labels explain evidence strength, not verified ownership.</p>')
        slots.update({key: st.empty() for key in ("web_progress", "web_metrics", "web_notice", "web_cards")})
        if start_scan:
            scan = render_live_scan(analyze_identity_stream(inputs, handle), slots)
            scan["evaluation"] = build_evaluation_summary(
                web_results=scan["web"], web_elapsed=scan["web_elapsed"],
                username_results=scan["profiles"], username_elapsed=scan["username_elapsed"],
                web_error=scan["web_error"], username_error=scan["username_error"],
                total_elapsed=scan["total_elapsed"],
            )
            try:
                scan["evaluation_file"] = save_evaluation_summary(scan["evaluation"])
            except OSError:
                scan["evaluation_file"] = None
            st.session_state.scan = scan
        candidates = render_sources(scan, slots)
        st.caption("Results reflect your last submitted search. Run the check again after changing your identifiers.")
        render_summary(scan)
        render_actions(scan)
        render_research(scan, candidates)
        render_technical(scan)

    html('<div class="footer">Digital Footprint Visualizer &nbsp;&middot;&nbsp; Public-data privacy self-audit<br>'
         'Evidence is a starting point. Verify ownership before taking action.</div>')


if __name__ == "__main__":
    main()
