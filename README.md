# Digital Footprint Visualizer

Use the project virtual environment (it contains the DDGS search dependency):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Enter at least one name, username / online handle, email address, or phone number, and confirm authorization. Each identifier works independently; combine identifiers when available. City / State, Email, and Phone are inside **Improve match accuracy**. Location refines a search and cannot be used by itself. Phone searches accept US numbers with ten digits or eleven starting with 1. Email and phone are used as public-web search identifiers only.

The light theme is in `.streamlit/config.toml`; the responsive hero, overlapping search form, and result cards are styled in `assets/app.css`. When a username is supplied, Username Exposure shows site outcomes and category filters. When only a name, email, or phone is supplied, results open directly on Public Web Exposure; no empty username metrics or username summary are shown. Counts come from source results. No risk score, password check, or breach lookup is offered. Local backend changes are detected using Streamlit's polling file watcher and automatic reruns.

`backend.whatsmyname.scan_username_stream()` yields an initial snapshot and a fresh snapshot after each concurrent site check completes. `scan_username()` retains its blocking interface. Both default to `max_sites=None`: the app checks **every active WhatsMyName definition across all categories**, with 20 concurrent workers. The shared `DEFAULT_WORKERS` constant also configures the analysis coordinator. There is no 100-site cap or category exclusion. Definitions marked disabled by the dataset are excluded. Progress reflects the number of active definitions actually selected; full scans can take several minutes. Explicit `max_sites` and `workers` values remain supported for controlled tests. Site matching rules and the existing eight-second request timeout are unchanged. More concurrent checks reduce queueing, but actual speed depends on network latency and site restrictions.

`backend.analysis.analyze_identity_stream()` runs public-web search alongside username checking when a handle is supplied. Public-web search uses every supplied name, username, email, and valid phone identifier, plus name/location, name/username, and username/location combinations where available. Username-only searches use both WhatsMyName and public-web queries. Name-, email-, or phone-only searches use public-web search alone. A name may contain a single word; neither a handle nor a second name component is required. The app does not invent a username from a name or email.

`backend.web_search.search_identity_stream()` runs up to three independent Bing queries concurrently and yields an initial record and one update after each query completes. Name-only searches use eight query types: exact name, beyond LinkedIn, public PDFs, unquoted name, LinkedIn profiles, GitHub, profile/portfolio/biography pages, and social profiles. Optional details add more queries. `search_identity()` remains a blocking wrapper with compatible arguments.

Worker threads send data through a queue; only the Streamlit script thread updates the page. Both username profile cards and public-web cards appear as their checks/queries complete. The dedicated web loading card shows an animated indicator, actual searches completed versus planned, elapsed time, and candidate-page/domain counts; it does not pretend to count every site on the internet. An interrupted source preserves partial results and does not discard the other source. There is no stop button: active HTTP requests finish within the backend timeouts. Closing the page or rerunning the script is not a reliable cancellation mechanism.

Public-web provider rows must have visible identifier support before appearing as candidate pages: first and last supplied name tokens (or the whole token for a single name), an exact email, an exact handle, or a matching phone number. Name matching normalizes case, accents, punctuation, and URL encoding and allows intervening middle names. Location alone is insufficient. Clearly unsupported rows are excluded from cards; their count is explained and their raw data remains in Technical Details for review. This conservative gate can also exclude a relevant page whose title/snippet/URL omits identifying evidence; it is not proof of ownership or a claim to retrieve every page.

Results and confidence labels stay tied to the submitted input snapshot. Public-web labels use the existing transparent heuristic, not a probability or proof of ownership. Missing results and blocked/time-out/rate-limit outcomes must be interpreted separately.

Each completed analysis saves JSON to `output/evaluation/`, relative to the project directory. Records include a unique run ID, UTC timestamp, total wall time, separate source timings (including failures), planned and completed site/query counts, filtered-page counts, and username status totals. Skipped username scans remain null rather than becoming an error or a zero-result scan. Total wall time reflects overlapping sources and is not their sum. Unique filenames prevent records from overwriting each other. A local save failure leaves results and downloads available.

**Research Evaluation**, collapsed near the bottom, contains optional manual ownership classifications and JSON downloads. Saved manual evaluations include the run ID, source summaries, total time, and confirmed/unrelated/cannot-determine/not-reviewed counts. Confirmation rate is calculated only among resolved reviews; it does not measure recall or overall accuracy. **Technical Details**, also collapsed, contains raw JSON, diagnostic errors, and local output paths.

Automatic evaluation records omit submitted identifiers and page content, but source error messages can contain request details. Manual records intentionally include usernames and profile URLs. Treat these files accordingly.

Check syntax without writing bytecode:

```powershell
.\.venv\Scripts\python.exe -c "import ast,pathlib; files=[p for p in pathlib.Path('.').rglob('*.py') if not any(s in {'.venv','venv','__pycache__','.git'} for s in p.parts)]; [ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p)) for p in files]; print('Syntax OK:',len(files))"
```

Run the offline regression suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite checks source concurrency, early match delivery, failures, single-identifier searches, authorization, UI reruns, manual review, evaluation persistence, WhatsMyName aggregation beyond 100 active sites, and public-web query generation/deduplication with controlled fixtures. Test fixtures are never used as application results.

The legacy `backend_test.py` performs live external requests, including an unrelated HIBP integration check. `web_search_test.py` requires interactive input. They are not part of the offline regression suite. Real results depend on dataset availability and third-party search/site restrictions.

Optional browser and live checks:

```powershell
.\.venv\Scripts\python.exe tests/browser_check.py
.\.venv\Scripts\python.exe tests/live_smoke.py
.\.venv\Scripts\python.exe tests/live_smoke.py --name-only
```

The browser check requires Chrome at its standard Windows path and the installed `websockets` package. It starts a separate preview server on port 8508 with controlled test sources, uses port 9237 for Chrome debugging, checks early profile/web-card rendering and mobile overflow, and saves screenshots in `output/ui/`. Test-source data is isolated from production code and scan records. The live check uses a generated handle and project-title search, checks three sites, and saves an evaluation explicitly marked `live_smoke_test`. With `--name-only`, it skips username checks, reports each real web-query completion, and marks the record `live_name_only_smoke_test`. Neither optional script runs during unittest discovery. See `VALIDATION.md` for the completed verification results.
