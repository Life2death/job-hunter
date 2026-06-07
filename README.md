# Naukri Job Hunter

Automated job search tool that hits Naukri's internal API, scores and ranks fresh postings, and outputs ranked CSVs — built for a 3-track senior-level job search.

## Tracks

| Code | Roles |
|------|-------|
| `SM` | Scrum Master, Senior Scrum Master, Release Train Engineer (RTE), Agile Coach |
| `PM` | Program Manager, Senior Program Manager, Technical Program Manager (TPM) |
| `DIR` | Director of Engineering/Delivery, VP Engineering, Head of Delivery |

## Features

- Searches Mumbai & Pune locations for each keyword
- **Freshness filter** — only surfaces jobs posted in the last 5 days (FRESH ≤ 3 days, AGING 4–5 days)
- **Scoring rubric** — ranks jobs on role match, domain fit (BFSI), SAFe/governance signals, compensation, location, and org quality
- Deduplicates across keyword/location combinations
- Outputs one ranked CSV per track + a combined `naukri_results_ALL.csv`

## Requirements

```bash
pip install requests pycryptodome
```

## Setup

1. Log in to [naukri.com](https://www.naukri.com) in Chrome
2. Open DevTools (F12) → Network tab → reload the page
3. Click any naukri.com request → Headers → copy the full `cookie` value
4. Paste it into the `HEADERS["cookie"]` field in `naukri_job_hunter.py`

> The cookie expires every ~7 days. Update it when you start getting 403 errors.

## Usage

```bash
# Run all 3 tracks (outputs naukri_results_SM.csv, _PM.csv, _DIR.csv)
python naukri_job_hunter.py

# Run a single track
python naukri_job_hunter.py --track SM
python naukri_job_hunter.py --track PM
python naukri_job_hunter.py --track DIR

# Test mode — 1 page per keyword, prints table to console only
python naukri_job_hunter.py --test
```

## Output Columns

| Column | Description |
|--------|-------------|
| `track` | SM / PM / DIR |
| `fit` | Composite score (higher = better fit) |
| `freshness` | FRESH / AGING |
| `age_days` | Days since posted |
| `title` | Job title |
| `company` | Company name |
| `location` | City |
| `salary` | Salary label from Naukri |
| `url` | Direct link to the job listing |
| `scores_json` | Breakdown of individual scoring factors |

## Scoring Rubrics

Each track has a max score of **100** across 7 factors:

| Factor | SM | PM | DIR |
|--------|----|----|-----|
| Role / level match | 25 | 25 | 25 |
| SAFe signal / Governance / Scope & scale | 20 | 20 | 20 |
| Domain fit (BFSI) | 15 | 15 | 15 |
| Compensation | 15 | 15 | 15 |
| Location | 10 | 10 | 10 |
| Org quality | 10 | 10 | 10 |
| Availability | 5 | 5 | 5 |

AGING jobs receive a −10 penalty. Jobs older than 5 days are excluded.

## Notes

- Compensation floor: ₹40 LPA (SM/PM), ₹50 LPA (DIR)
- Targets Mumbai, Pune, Thane, Navi Mumbai, remote, and hybrid locations
- Tier-1 companies (Barclays, JPMorgan, Citi, Deutsche, Accenture, etc.) receive bonus org-quality points
