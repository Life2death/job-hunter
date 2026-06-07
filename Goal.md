Goal -
Build a multi-track Naukri job auto-applicant with per-profile login, batch application, SQLite tracking, and manual-apply reporting.
Constraints & Preferences
Three tracks (SM, PM, DIR) each with a separate Naukri account
Only auto-apply to jobs with fit score > 50
Batch apply: N jobs at a time, with configurable intervals between batches
Questionnaire jobs must be skipped, logged, and reported for manual apply
Must use existing CSV data (no re-querying Naukri) for the catalog
Must not lose existing data; SQLite is crash-safe
Windows cp1252 console — no Unicode box-drawing or emoji characters allowed
Progress
Done
Naukri API fixed: added RSA nkparam header generation using pycryptodome (public key from NopeRi project)
IPv6 hang fixed: forced IPv4 via socket monkey-patch
Date parsing fixed: _freshness() now handles relative strings like "1 Day Ago", "3 Days Ago", "30+ Days Ago"
Unicode encoding fixed: all emoji/box-drawing chars replaced with ASCII equivalents
Multi-track search + scoring: outputs naukri_results_SM.csv, naukri_results_PM.csv, naukri_results_DIR.csv
is_applied and applied_date columns added to all CSV output via enrich_results()
profiles.json created with 3-track credentials (SM/PM/DIR)
AppliedJobsDB rewritten with SQLite (applied_jobs.db) — ACID-safe, indexed, auto-migrates from CSV
login_naukri() uses httpcloak.Session for TLS fingerprinting, extracts Bearer token from response cookies
apply_to_job() posts to Naukri's apply API; handles direct-success, redirect, questionnaire, and auth-failure responses
--stats flag: prints weekly trend, per-track breakdown, status summary, top companies, manual-apply list
--import-csv flag: reads all naukri_results_*.csv into jobs_catalog table, marks already-applied jobs as "applied"
--batch-apply --track SM --batch-size 10 --interval 10: batch apply with configurable intervals
--manual-report flag: generates manual_apply_SM.csv (or per-track) with clickable URLs for questionnaire-skipped jobs
252 jobs imported into DB catalog (SM: 144, PM: 53, DIR: 55); 5 already applied, 247 new
In Progress
Batch-apply test timed out at 3 minutes; needs debugging (likely login or network hang)
Blocked
(none)
Key Decisions
SQLite over CSV for apply log: ACID-safe, indexed queries, no corruption risk for long-running daily use
Separate jobs_catalog table from applies table: jobs_catalog holds all CSV jobs with status tracking; applies is append-only log of actual apply attempts
httpcloak.Session for login TLS fingerprinting (avoids Naukri bot detection on cloud IPs)
Questionnaire jobs are always skipped (not auto-answered) and logged to manual_apply_*.csv for manual click-and-apply
--batch-apply pulls from DB catalog (not from CSV) so it can track status across runs
4-second delay between individual applies to avoid rate limiting
Next Steps
Debug --batch-apply timeout: test with shorter timeout, check if login hangs, add more verbose logging
Once batch-apply works, run SM full batch (e.g. --batch-size 10 --interval 10) to start applying
After SM batch, repeat for PM and DIR tracks
Generate final manual_apply_ALL.csv report for questionnaire jobs
Critical Context
Naukri API /jobapi/v3/search requires nkparam RSA-signed header (generated via generate_nkparam())
Apply API endpoint: POST https://www.naukri.com/cloudgateway-workflow/workflow-services/apply-workflow/v1/apply
Login endpoint: POST https://www.naukri.com/central-login-services/v1/login
Session cookie nauk_at (Bearer token) extracted from resp.cookies, not session.cookies (httpcloak returns cookies as a list)
Apply response patterns: jobs[0].status==200 or message contains "successfully applied" → applied; applyRedirectUrl → redirect; jobs[0].questionnaire → questionnaire
Python 3.11.5, Windows; pycryptodome and httpcloak are installed
DNS IPv6 causes hangs on this network; forced IPv4 via socket.getaddrinfo override
Relevant Files
D:\Job\naukri_job_hunter.py: Main script (~1090 lines) — search, score, apply, stats, batch, report
D:\Job\profiles.json: Credentials per track (SM/PM/DIR)
D:\Job\applied_jobs.db: SQLite DB with applies and jobs_catalog tables
D:\Job\naukri_results_SM.csv, _PM.csv, _DIR.csv: Latest search results with is_applied column
D:\Job\applied_jobs.csv.migrated: Backed-up CSV after migration to SQLite