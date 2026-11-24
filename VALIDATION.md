# Frontend redesign verification

Implemented and checked on November 4, 2026 using the project's `.venv` interpreter.

## Files

- `app.py`: privacy-checker frontend, independent name/handle/email/phone searches with required authorization, optional-input expander, live username cards, category filters, separate public-web evidence, source-specific actions, summary, collapsed research and technical sections.
- `assets/app.css`: navy hero, overlapping white form, light input styling, responsive cards, badges and progress styling.
- `.streamlit/config.toml`: requested light theme and colors.
- `backend/whatsmyname.py`: concurrent progress snapshots across all active categories with no default site limit; site classification and blocking interface preserved.
- `backend/analysis.py`: conditional source orchestration, queue-based updates and validation requiring at least one identifier.
- `backend/web_search.py`: concurrent progressive public-web queries, eight name-search strategies, exact username and combined identifier queries, conservative visible-evidence gating, and detached progress snapshots; positional compatibility and URL deduplication preserved.
- `backend/evaluation.py`: total wall time, selected site/query totals, completed-query counts, excluded-page counts and linked manual-review source summaries; unique JSON storage preserved.
- `README.md`: setup, behavior, logging and verification commands.
- `tests/test_analysis.py`, `tests/test_app.py`, `tests/test_evaluation.py`, `tests/test_web_search.py`, `tests/test_whatsmyname.py`: 40 offline regression tests.
- `tests/browser_check.py`, `tests/fixtures/streamlit_preview.py`: isolated browser fixture and visual/progress checks.
- `tests/live_smoke.py`: explicit, small real-source integration check.
- `VALIDATION.md`: this record.

## Functions

New backend functions include `scan_username_stream()`, `search_identity_stream()`, `search_public_web_stream()`, `analyze_identity_stream()`, and `validate_identifiers()`, with helpers for aggregation and visible-identifier support. Blocking `scan_username()` and `search_identity()` wrappers remain available.

`scan_username()` remains available with its original arguments and returns the final summary. `build_evaluation_summary()` accepts total elapsed time; `build_manual_validation_summary()` optionally includes the associated evaluation summary and run ID.

Frontend rendering helpers include `render_web_progress()`, `render_username_source()`, and `render_web_source()` in addition to card, metric, summary, research, and technical rendering helpers. Name-only searches omit unused username UI entirely. Stale elements from prior searches are hidden during a new run.

## Results

- Syntax parsing passed for all **21 project Python files**, excluding the virtual environment and caches.
- `.venv/Scripts/python.exe -m unittest discover -s tests -v`: **40 tests passed**, including single-identifier searches, default scans beyond 100 active sites, concurrent query completion, detached snapshots, relevance filtering, and early public-page rendering.
- Streamlit started successfully on localhost; its health endpoint returned `ok`.
- At least one identifier, optional-input validation and required authorization are covered by backend and Streamlit AppTest checks. Name and handle are independently optional. Empty/location-only submissions do not start source requests. Web-only scans skip the username source without reporting a failure.
- Offline tests prove site checks overlap and an early positive is yielded before a blocked slow check completes. Source-level tests prove username updates continue while public-web search is still running.
- Event-based tests prove an early public page is yielded while another web query is blocked; single-token names require whole-token evidence, and unrelated provider rows do not appear as candidates. Excluded-page counts deduplicate URLs and do not exclude a URL supported by another query.
- AppTest records live rendering calls and verifies a name-only search displays actual partial query progress and a public-page card before the final summary. No username heading, site metrics, or username cards appear for name-only searches.
- Real headless Chrome checks passed at desktop and 390px mobile widths: form overlaps hero, no horizontal overflow, no HTML code blocks, and responsive result cards render correctly.
- Browser checks observed **one possible-match card with only 1 of 3 fixture checks complete**, before the summary appeared. After completion both evidence sections rendered and loading messages cleared. Fixtures are confined to a separate test server and `output/ui/fixture-evaluation/`; they are never used as application results.
- The browser suite also submits a name-only search after a combined-source run. It verifies a visible loading card and first public page at **1 of 3 fixture queries complete**, no username heading/site metrics, no visible old summary, cleared loading on completion, and no mobile overflow.
- UI tests verify category filtering preserves full scan counts and the full manual-review population, edited but unsubmitted identifiers do not change existing evidence, reruns do not repeat scan logging, failures preserve other-source results and downloads, and research/technical expanders are collapsed.
- Scan and manual-review JSON files were actually written and read back in tests, including linked run ID, total time, source timings/counts, and manual classifications. Filename uniqueness was verified.
- Live WhatsMyName dataset retrieval succeeded with **692 site definitions** at verification time.
- A small live coordinator check emitted username progress **0/3, 1/3, 2/3, 3/3**, ran **3 public-web queries**, returned **8 unique candidate pages**, and saved a record marked `live_smoke_test` in `output/evaluation/`. The three site checks returned **1 blocked and 2 error outcomes**, correctly retained as unknown outcomes rather than negative results. Both source workers completed without a source-level exception.
- The current name-only live smoke check used the project title without a username. It emitted **0/8 through 8/8 completed queries**, surfaced **6 candidate pages after 2/8 completed**, and ended with **7 candidate pages**, with no failed queries or source exceptions. Username checks were not started. A record marked `live_name_only_smoke_test` was saved in `output/evaluation/`.
- After restarting the production app on port 8501, a real Chrome submission using only the project title showed **0/8 loading progress** and **7 candidate pages at 1/8 searches complete**, then finished with **7 pages across 2 domains in 7.3 seconds**. Username remained blank and no username section appeared. A live screenshot is retained as `output/ui/live-name-only-progress.png`.
- No password, breach, API-key, or risk-score functionality appears in the normal UI. Existing public-web queries were extended with username combinations; deduplication, WhatsMyName `check_site()`, and confidence classification were retained.
- A regression fixture with **126 active definitions plus one disabled definition** verifies both username scan APIs check all active definitions by default, including the previously excluded category. Explicit small limits remain available to tests.

Browser screenshots are in `output/ui/`, including initial/combined-source previews and `name-only-progress.png`, `name-only-results.png`, and `name-only-results-mobile.png`. These previews show explicitly controlled test content, not a person's live footprint.

## Limits

- The application checks every active definition across all dataset categories with 20 concurrent site workers. Disabled definitions are excluded. Removing the site cap does not guarantee a successful response from every website or complete coverage of the internet.
- Public-web results appear after each completed query, with up to three concurrent queries. Username results stream per completed check. No cancellation control is offered because in-flight HTTP requests are not safely cancellable.
- The prior live smoke verification used three sites, not a full-dataset sweep. Individual sites can block or reject automated checks, and search results change over time. Public-web queries retain the provider result limit of ten results per query.
- Public-web searches query indexes rather than crawl every internet site. A page without visible identifier support in its title/snippet/URL can be excluded even if relevant; raw provider data remains in Technical Details. Name-only results are leads rather than confirmed ownership. Adding location, email, or a handle may help disambiguate a common name, but is not required.
- Browser checks cover installed Chrome on Windows, not every browser/device. Streamlit DOM styling may need adjustment if its pinned version changes.
- The restricted execution sandbox blocked Windows temporary-file access, browser startup and external requests. The persistence suite, browser checks and live checks passed after running with the required permissions; application restrictions were not disabled.
- Legacy interactive/network scripts were left intact and excluded from offline discovery; the unrelated HIBP integration script was not executed.

## Username concurrency adjustment

A saved full scan recorded 691 sites in 119.97 seconds at the previous default of ten workers. The default has been raised to twenty through a shared `DEFAULT_WORKERS` constant used by both username scan APIs and the coordinator. Coverage, request timeouts, site classification, and streaming behavior are unchanged. Explicit worker overrides remain supported.

All 40 regression tests and syntax checks for all 21 Python files pass after the adjustment. A controlled fixture with 200 equal-duration simulated I/O checks completed in 0.413 seconds with ten workers and 0.208 seconds with twenty, retaining all 200 outcomes. This confirms concurrency wiring and queueing improvements in the fixture; it is not a live-web speed guarantee. A new full live scan was not run for benchmarking, so actual improvement should be measured on the next normal scan.
