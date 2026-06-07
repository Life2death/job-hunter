"""
naukri_job_hunter.py
Adapted from Rockposedon/WebScraping for Vikram Panmand's 3-track job search.
Hits Naukri's internal API, applies freshness filter + scoring, outputs a ranked CSV.

Tracks:
  SM     — Scrum Master | Senior Scrum Master | Release Train Engineer | Agile Coach
  PM     — Program Manager | Senior Program Manager | Technical Program Manager
  DIR    — Director | VP | Head of Delivery | Delivery Head

Usage:
  python naukri_job_hunter.py               # all tracks
  python naukri_job_hunter.py --track SM    # single track
  python naukri_job_hunter.py --test        # test mode: 1 page per keyword, print table
"""

import requests
import csv
import json
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import base64
import time
import socket
import httpcloak
import os

# Force IPv4 (avoid IPv6 hangs on some networks)
socket.setdefaulttimeout(15)
orig_getaddrinfo = socket.getaddrinfo
def _ipv4_addrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_addrinfo

# ─── Configuration ────────────────────────────────────────────────────────────

TODAY = date.today()

SEARCHES = {
    "SM": [
        {"keyword": "Scrum Master",          "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Scrum Master",          "location": "Pune",   "loc_id": "67"},
        {"keyword": "Release Train Engineer","location": "Mumbai", "loc_id": "103"},
        {"keyword": "Release Train Engineer","location": "Pune",   "loc_id": "67"},
        {"keyword": "Agile Coach",           "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Agile Coach",           "location": "Pune",   "loc_id": "67"},
    ],
    "PM": [
        {"keyword": "Technical Program Manager", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Technical Program Manager", "location": "Pune",   "loc_id": "67"},
        {"keyword": "Senior Program Manager",    "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Senior Program Manager",    "location": "Pune",   "loc_id": "67"},
        {"keyword": "Delivery Program Manager",  "location": "Mumbai", "loc_id": "103"},
    ],
    "DIR": [
        {"keyword": "Director Engineering",  "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Director Engineering",  "location": "Pune",   "loc_id": "67"},
        {"keyword": "VP Engineering",        "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Head of Delivery",      "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Director Delivery",     "location": "Mumbai", "loc_id": "103"},
    ],
}

# Freshness (days since posted)
FRESH_MAX   = 3    # ≤3 days → FRESH,  no penalty
AGING_MAX   = 5    # 4-5 days → AGING, -10 penalty
# >5 days → excluded

COMP_FLOOR  = 4000000   # 40 LPA in INR

# ─── Scoring rubrics (max points per factor) ──────────────────────────────────

RUBRICS = {
    "SM": {
        "role_match":     25,
        "safe_signal":    20,
        "domain_fit":     15,
        "comp":           15,
        "location":       10,
        "availability":    5,
        "org_quality":    10,
    },
    "PM": {
        "role_match":     25,
        "governance":     20,
        "domain_fit":     15,
        "comp":           15,
        "location":       10,
        "availability":    5,
        "org_quality":    10,
    },
    "DIR": {
        "role_match":     25,
        "scope_scale":    20,
        "domain_fit":     15,
        "comp":           15,
        "location":       10,
        "availability":    5,
        "org_quality":    10,
    },
}

# ─── Keyword signals for auto-scoring ─────────────────────────────────────────

SAFE_KEYWORDS    = ["safe", "pi planning", "art", "agile release train", "scaled agile",
                    "less", "nexus", "scrum@scale", "lean agile"]
BFSI_KEYWORDS    = ["bank", "financial", "fintech", "insurance", "payment", "capital",
                    "credit", "lending", "wealth", "asset management", "securities"]
GOVERNANCE_KW    = ["program governance", "raid", "steering", "p&l", "budget",
                    "pmo", "transformation", "portfolio", "roadmap"]
SCOPE_KW         = ["p&l", "portfolio", "multi-account", "org building", "headcount",
                    "cxo", "cto", "vp", "director", "head of"]
SENIOR_SM_KW     = ["senior scrum", "lead scrum", "rte", "release train", "agile coach",
                    "enterprise agile", "tribe"]
SENIOR_PM_KW     = ["senior program", "sr program", "technical program", "delivery program",
                    "tpm", "pgm"]

def _score_job(job: dict, track: str) -> dict:
    title   = (job.get("title", "") or "").lower()
    company = (job.get("company", "") or "").lower()
    desc    = (job.get("description", "") or "").lower()
    salary  = job.get("salary_min", 0) or 0
    loc     = (job.get("location", "") or "").lower()
    exp     = (job.get("experience", "") or "").lower()
    text    = f"{title} {desc}"

    scores = {}

    if track == "SM":
        # Role/level match
        if any(k in title for k in ["rte", "release train", "agile coach", "enterprise agile"]):
            scores["role_match"] = 25
        elif "senior scrum" in title or "lead scrum" in title or "tribe" in title:
            scores["role_match"] = 22
        elif "scrum master" in title or "scrummaster" in title:
            scores["role_match"] = 18
        else:
            scores["role_match"] = 8   # adjacent title

        # SAFe signal
        safe_hits = sum(1 for k in SAFE_KEYWORDS if k in text)
        scores["safe_signal"] = min(20, safe_hits * 7)

        # Domain fit
        bfsi_hits = sum(1 for k in BFSI_KEYWORDS if k in text + company)
        scores["domain_fit"] = min(15, bfsi_hits * 5)

        # Comp
        if salary >= COMP_FLOOR:
            scores["comp"] = 15
        elif salary > 0:
            scores["comp"] = max(5, int(15 * salary / COMP_FLOOR))
        else:
            scores["comp"] = 8  # unknown

        # Location
        scores["location"] = 10 if any(l in loc for l in ["mumbai","pune","thane","navi mumbai","remote","hybrid"]) else 4

        # Availability — always positive (July joiner)
        scores["availability"] = 4

        # Org quality
        tier1 = ["barclays","jpmorgan","jp morgan","citi","deutsche","morgan stanley",
                 "goldman","amex","mastercard","visa","ubs","barclays","siemens","accenture","capgemini"]
        scores["org_quality"] = 10 if any(t in company for t in tier1) else 6

    elif track == "PM":
        if any(k in title for k in SENIOR_PM_KW):
            scores["role_match"] = 23
        elif "program manager" in title:
            scores["role_match"] = 18
        elif "project manager" in title:
            scores["role_match"] = 12
        else:
            scores["role_match"] = 8

        gov_hits = sum(1 for k in GOVERNANCE_KW if k in text)
        scores["governance"] = min(20, gov_hits * 5)

        bfsi_hits = sum(1 for k in BFSI_KEYWORDS if k in text + company)
        scores["domain_fit"] = min(15, bfsi_hits * 5)

        if salary >= COMP_FLOOR:
            scores["comp"] = 15
        elif salary > 0:
            scores["comp"] = max(5, int(15 * salary / COMP_FLOOR))
        else:
            scores["comp"] = 8

        scores["location"] = 10 if any(l in loc for l in ["mumbai","pune","thane","navi mumbai","remote","hybrid"]) else 4
        scores["availability"] = 4

        tier1 = ["barclays","jpmorgan","jp morgan","citi","deutsche","morgan stanley",
                 "goldman","amex","mastercard","visa","ubs","siemens","accenture","capgemini","infosys","tcs","wipro"]
        scores["org_quality"] = 10 if any(t in company for t in tier1) else 6

    elif track == "DIR":
        if any(k in title for k in ["vp","head of","cto","chief"]):
            scores["role_match"] = 25
        elif "director" in title:
            scores["role_match"] = 22
        elif "associate director" in title:
            scores["role_match"] = 18
        else:
            scores["role_match"] = 10

        scope_hits = sum(1 for k in SCOPE_KW if k in text)
        scores["scope_scale"] = min(20, scope_hits * 5)

        bfsi_hits = sum(1 for k in BFSI_KEYWORDS if k in text + company)
        scores["domain_fit"] = min(15, bfsi_hits * 5)

        comp_floor_dir = 5000000  # 50 LPA for Director
        if salary >= comp_floor_dir:
            scores["comp"] = 15
        elif salary > COMP_FLOOR:
            scores["comp"] = 10
        elif salary > 0:
            scores["comp"] = 5
        else:
            scores["comp"] = 8

        scores["location"] = 10 if any(l in loc for l in ["mumbai","pune","thane","navi mumbai","remote","hybrid"]) else 4
        scores["availability"] = 4

        tier1 = ["barclays","jpmorgan","jp morgan","citi","deutsche","morgan stanley",
                 "goldman","amex","mastercard","visa","ubs","siemens","accenture","capgemini"]
        scores["org_quality"] = 10 if any(t in company for t in tier1) else 6

    raw = sum(scores.values())
    return scores, raw


def _freshness(posted_str: str):
    """Return (tag, penalty, age_days) from a Naukri-style date string."""
    if not posted_str or posted_str.strip() == "":
        return "UNKNOWN", -10, None
    try:
        # Try ISO date first
        d = datetime.strptime(posted_str.strip(), "%Y-%m-%d").date()
        age = (TODAY - d).days
    except ValueError:
        # Try relative strings like "1 Day Ago", "3 Days Ago", "Just Now"
        s = posted_str.strip().lower()
        if "just" in s or "today" in s or "now" in s or "few hours" in s:
            age = 0
        elif "yesterday" in s:
            age = 1
        elif "day" in s:
            try:
                age = int(''.join(c for c in s if c.isdigit()))
            except ValueError:
                age = 99
        elif "week" in s:
            try:
                age = int(''.join(c for c in s if c.isdigit())) * 7
            except ValueError:
                age = 99
        elif "month" in s:
            return "STALE", None, 99
        else:
            return "UNKNOWN", -10, None
    if age <= FRESH_MAX:
        return "FRESH", 0, age
    elif age <= AGING_MAX:
        return "AGING", -10, age
    else:
        return "STALE", None, age


# ─── API ──────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.naukri.com/jobapi/v3/search"

# RSA public key for nkparam generation
NKPARAM_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALrlQ+djR0RjJwBF1xuisHmdFv334MIm
K6LgzJhmLhN7B5yuEyaKoasgXQk3+OQglsOaBxEJ0j5PcTL3nbOvt80CAwEAAQ==
-----END PUBLIC KEY-----"""


def generate_nkparam(page_type: str = "srp") -> str:
    key = RSA.import_key(NKPARAM_PUBLIC_KEY)
    cipher = PKCS1_v1_5.new(key)
    timestamp = int(time.time() * 1000)
    plaintext = f"v0|{timestamp}|121_{page_type}"
    encrypted = cipher.encrypt(plaintext.encode('utf-8'))
    return base64.b64encode(encrypted).decode('utf-8')

HEADERS = {
    "accept": "application/json",
    "appid": "109",
    "clientid": "d3skt0p",
    "content-type": "application/json",
    "systemid": "Naukri",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "gid": "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE",
    "nkparam": "",  # filled per-request
    # ── INSERT YOUR NAUKRI SESSION COOKIE BELOW ──────────────────────────────
    # How to get it:
    #   1. Open Chrome → naukri.com → log in
    #   2. Open DevTools (F12) → Network tab → reload page
    #   3. Click any naukri.com request → Headers → Request Headers → Copy the full 'cookie' value
    #   4. Paste it here (replace the placeholder string)
    # The cookie refreshes every ~7 days; update it when you get 403 errors.
    "cookie": "PC396725C870D4C446F045BA5C8C125F7~YAAQEwkgF5ZMZ4WeAQAA3znrmwABO1CAd8at/JO7gPIUq35du5BmLdoJTLoSt+6M110zrUalXZf4fAB49limjzlFP2Ykx47Fo/uZedDuAD7MaPo1KecoBWxjCdLqguFJprjMzvtULjSKODI+iXbTh4KTUK6KiV1PqizPt5o0NFypiTuHnZpryp58rUAweKi2S9rHDpwzXJ7gVeCRV8a4Uxm/NTRayY4HoYihfmad2Kteszo2rO8Vi23syj6rCRjdOAE",
}


def fetch_page(keyword: str, location: str, page: int = 1, results_per_page: int = 20) -> list:
    params = {
        "noOfResults": str(results_per_page),
        "urlType": "search_by_keyword",
        "searchType": "adv",
        "keyword": keyword,
        "location": location,
        "pageNo": str(page),
        "sort": "r",   # r = recently posted
        "src": "jobsearchDesk",
        "latLong": "",
    }
    headers = HEADERS.copy()
    headers["nkparam"] = generate_nkparam("srp")
    try:
        resp = requests.get(BASE_URL, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("jobDetails", [])
        elif resp.status_code == 403:
            print(f"  [!] 403 Forbidden - your Naukri cookie has expired. Update HEADERS['cookie'].")
            return []
        else:
            print(f"  [!] HTTP {resp.status_code} for '{keyword}' / {location}")
            return []
    except Exception as e:
        print(f"  [!] Request error: {e}")
        return []


def parse_job(raw: dict) -> dict:
    placeholders = raw.get("placeholders", [])
    experience = salary_label = location = ""
    sal_min = sal_max = 0

    for p in placeholders:
        t = p.get("type", "")
        if t == "experience":
            experience = p.get("label", "")
        elif t == "salary":
            salary_label = p.get("label", "")
        elif t == "location":
            location = p.get("label", "")

    # Try to extract numeric salary
    sal_range = raw.get("salaryDetail", {})
    if sal_range:
        sal_min = sal_range.get("minimumSalary", 0) or 0
        sal_max = sal_range.get("maximumSalary", 0) or 0

    job_id = raw.get("jobId", "")
    return {
        "job_id":      job_id,
        "title":       raw.get("title", ""),
        "company":     raw.get("companyName", ""),
        "location":    location,
        "experience":  experience,
        "salary_label": salary_label,
        "salary_min":  sal_min,
        "salary_max":  sal_max,
        "posted_date": raw.get("modifiedOn", "") or raw.get("createdOn", "") or raw.get("footerPlaceholderLabel", ""),
        "skills":      raw.get("tagsAndSkills", ""),
        "description": (raw.get("jobDescription", "") or "")[:500],
        "url":         f"https://www.naukri.com/job-listings-{job_id}" if job_id else "",
    }


# ─── Main pipeline ────────────────────────────────────────────────────────────

def run(tracks: list, test_mode: bool = False):
    all_results = []
    track_results = {t: [] for t in tracks}
    seen_ids = set()
    pages = 1 if test_mode else 5   # 5 pages × 20 = up to 100 results per keyword

    for track in tracks:
        print(f"\n{'='*60}")
        print(f"Track: {track}")
        for search in SEARCHES[track]:
            kw  = search["keyword"]
            loc = search["location"]
            print(f"  Searching '{kw}' in {loc}...")

            for page in range(1, pages + 1):
                raw_jobs = fetch_page(kw, loc, page)
                if not raw_jobs:
                    break

                for raw in raw_jobs:
                    job = parse_job(raw)
                    jid = job["job_id"]

                    # Dedupe
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)

                    # Freshness filter
                    freshness_tag, penalty, age = _freshness(job["posted_date"])
                    if penalty is None:   # STALE
                        continue

                    # Score
                    factor_scores, raw_score = _score_job(job, track)
                    final_score = raw_score + penalty

                    row = {
                        "track":       track,
                        "fit":         final_score,
                        "job_id":      jid,
                        "freshness":   freshness_tag,
                        "age_days":    age if age is not None else "?",
                        "title":       job["title"],
                        "company":     job["company"],
                        "location":    job["location"],
                        "experience":  job["experience"],
                        "salary":      job["salary_label"],
                        "posted":      job["posted_date"],
                        "skills":      job["skills"],
                        "url":         job["url"],
                        "scores_json": json.dumps(factor_scores),
                        "is_applied":  "",
                        "applied_date": "",
                    }
                    all_results.append(row)
                    track_results[track].append(row)

    # Sort overall by fit desc
    all_results.sort(key=lambda x: x["fit"], reverse=True)
    for t in tracks:
        track_results[t].sort(key=lambda x: x["fit"], reverse=True)
    return all_results, track_results


def save_csv(results: list, path: Path):
    if not results:
        print("No results to save.")
        return
    fieldnames = list(results[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\n[OK] Saved {len(results)} rows -> {path}")


def print_table(results: list, top_n: int = 15):
    sep = "-" * 110
    print(f"\n{sep}")
    print(f"{'#':<3} {'Fit':<5} {'Fresh':<7} {'Track':<5} {'Title':<35} {'Company':<25} {'Location':<18} {'Sal'}")
    print(sep)
    for i, r in enumerate(results[:top_n], 1):
        sal = r["salary"] or "NA"
        print(f"{i:<3} {r['fit']:<5} {r['freshness']:<7} {r['track']:<5} "
              f"{r['title'][:33]:<35} {r['company'][:23]:<25} "
              f"{r['location'][:16]:<18} {sal}")
    print(sep)
    print(f"Showing top {min(top_n, len(results))} of {len(results)} fresh/aging results\n")


# ─── Apply infrastructure ────────────────────────────────────────────────────

LOGIN_URL = "https://www.naukri.com/central-login-services/v1/login"
APPLY_URL = "https://www.naukri.com/cloudgateway-workflow/workflow-services/apply-workflow/v1/apply"
APPLIED_CSV = Path("applied_jobs.csv")
APPLIED_DB = Path("applied_jobs.db")
APPLY_SCORE_CUTOFF = 50
APPLY_DELAY = 4.0

APPLY_LOGSTR_MAP = {
    "search": "srp",
    "recommended": "drecomm_apply",
}
APPLY_LOGSTR_TEMPLATE = "--{src}-1-F-0-1--{sid}-"

APPLY_HEADERS = {
    "accept": "application/json",
    "appid": "121",
    "clientid": "d3skt0p",
    "content-type": "application/json",
    "systemid": "jobseeker",
}

LOGIN_HEADERS = {
    "accept": "application/json",
    "appid": "105",
    "clientid": "d3skt0p",
    "content-type": "application/json",
    "referer": "https://www.naukri.com/nlogin/login",
    "systemid": "jobseeker",
    "x-requested-with": "XMLHttpRequest",
}


class AppliedJobsDB:
    def __init__(self, db_path: Path = APPLIED_DB):
        self.db_path = db_path
        self.applied: dict[str, str] = {}
        self._init_db()
        self._migrate_csv()
        self._load()

    def _init_db(self):
        import sqlite3
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS applies (
                job_id    TEXT PRIMARY KEY,
                track     TEXT NOT NULL,
                applied_date TEXT NOT NULL,
                status    TEXT NOT NULL,
                title     TEXT NOT NULL,
                company   TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs_catalog (
                job_id    TEXT PRIMARY KEY,
                track     TEXT NOT NULL,
                title     TEXT NOT NULL,
                company   TEXT NOT NULL,
                location  TEXT,
                salary    TEXT,
                posted    TEXT,
                url       TEXT,
                fit       INTEGER DEFAULT 0,
                freshness TEXT,
                status    TEXT DEFAULT 'new',
                imported_date TEXT NOT NULL
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_track ON applies(track)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON applies(applied_date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jc_track ON jobs_catalog(track)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jc_status ON jobs_catalog(status)")
        self.conn.commit()

    def _migrate_csv(self):
        if not APPLIED_CSV.exists():
            return
        import sqlite3
        print(f"  [~] Migrating {APPLIED_CSV} -> {APPLIED_DB} ...")
        count = 0
        with open(APPLIED_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO applies (job_id, track, applied_date, status, title, company) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (row["job_id"], row.get("track", ""), row.get("applied_date", str(TODAY)),
                         row.get("status", ""), row.get("title", ""), row.get("company", ""))
                    )
                    count += 1
                except Exception:
                    pass
        self.conn.commit()
        backup = APPLIED_CSV.with_suffix(".csv.migrated")
        APPLIED_CSV.rename(backup)
        print(f"  [OK] Migrated {count} rows, renamed CSV -> {backup.name}")

    def _load(self):
        cursor = self.conn.execute("SELECT job_id, applied_date FROM applies")
        for job_id, applied_date in cursor:
            self.applied[job_id] = applied_date

    def save(self, job_id: str, track: str, status: str, title: str, company: str):
        import sqlite3
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO applies (job_id, track, applied_date, status, title, company) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, track, str(TODAY), status, title, company)
            )
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
        self.applied[job_id] = str(TODAY)

    def is_applied(self, job_id: str) -> bool:
        return job_id in self.applied

    def get_applied_date(self, job_id: str) -> str:
        return self.applied.get(job_id, "")

    def query(self, sql: str, params: tuple = ()) -> list:
        cursor = self.conn.execute(sql, params)
        return cursor.fetchall()

    def close(self):
        self.conn.close()

    def import_csv_jobs(self, track: str, csv_path: Path) -> int:
        if not csv_path.exists():
            print(f"  [!] {csv_path} not found")
            return 0
        count = 0
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                jid = row.get("job_id", "")
                if not jid:
                    continue
                already_applied = self.conn.execute(
                    "SELECT 1 FROM applies WHERE job_id=?", (jid,)
                ).fetchone()
                if already_applied:
                    status = "applied"
                else:
                    status = "new"
                try:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO jobs_catalog "
                        "(job_id, track, title, company, location, salary, posted, url, fit, freshness, status, imported_date) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (jid, track, row.get("title", ""), row.get("company", ""),
                         row.get("location", ""), row.get("salary", ""),
                         row.get("posted", ""), row.get("url", ""),
                         int(row.get("fit", 0)), row.get("freshness", ""),
                         status, str(TODAY))
                    )
                    if status == "applied":
                        self.conn.execute(
                            "UPDATE jobs_catalog SET status='applied' WHERE job_id=?",
                            (jid,)
                        )
                    count += 1
                except Exception:
                    pass
        self.conn.commit()
        return count

    def import_all_csvs(self):
        total = 0
        for track in ["SM", "PM", "DIR"]:
            p = Path(f"naukri_results_{track}.csv")
            if p.exists():
                c = self.import_csv_jobs(track, p)
                print(f"  {track}: {c} jobs imported")
                total += c
        print(f"  Total: {total} jobs in catalog")
        return total

    def get_next_batch(self, track: str, batch_size: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT job_id, title, company, location, salary, posted, url, fit, freshness, status "
            "FROM jobs_catalog WHERE track=? AND status='new' ORDER BY fit DESC LIMIT ?",
            (track, batch_size)
        ).fetchall()
        return [
            {
                "job_id": r[0], "title": r[1], "company": r[2],
                "location": r[3] or "", "salary": r[4] or "",
                "posted": r[5] or "", "url": r[6] or "",
                "fit": r[7] or 0, "freshness": r[8] or "",
                "status": r[9] or "new"
            }
            for r in rows
        ]

    def count_pending(self, track: str = None) -> int:
        if track:
            return self.conn.execute(
                "SELECT count(1) FROM jobs_catalog WHERE track=? AND status='new'",
                (track,)
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT count(1) FROM jobs_catalog WHERE status='new'"
        ).fetchone()[0]

    def update_job_status(self, job_id: str, status: str):
        self.conn.execute(
            "UPDATE jobs_catalog SET status=? WHERE job_id=?",
            (status, job_id)
        )
        self.conn.commit()

    def get_manual_report(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT track, title, company, location, salary, url "
            "FROM jobs_catalog WHERE status='questionnaire' "
            "ORDER BY track, company"
        ).fetchall()
        return [
            {
                "track": r[0], "title": r[1], "company": r[2],
                "location": r[3] or "", "salary": r[4] or "",
                "url": r[5] or ""
            }
            for r in rows
        ]


def enrich_results(results: list[dict], applied_db: AppliedJobsDB = None) -> list[dict]:
    if applied_db is None:
        applied_db = AppliedJobsDB()
    enriched = []
    for r in results:
        r = r.copy()
        jid = r.get("job_id", "")
        if applied_db.is_applied(jid):
            r["is_applied"] = "Yes"
            r["applied_date"] = applied_db.get_applied_date(jid)
        else:
            r["is_applied"] = ""
            r["applied_date"] = ""
        enriched.append(r)
    return enriched


def login_naukri(username: str, password: str):
    session = httpcloak.Session(preset="chrome-latest", timeout=30)
    resp = session.post(
        LOGIN_URL,
        headers=LOGIN_HEADERS,
        json={"username": username, "password": password},
    )
    if not resp.ok:
        print(f"  [!] Login failed ({resp.status_code}): {resp.text[:200]}")
        return None

    token = resp.cookies.get("nauk_at") if hasattr(resp.cookies, "get") else None
    if not token:
        token = session.cookies.get("nauk_at") if hasattr(session.cookies, "get") else None
    if not token:
        # Try iterating over response cookies as a fallback
        for cookie in resp.cookies:
            if cookie.name == "nauk_at":
                token = cookie.value
                break
    if not token:
        print("  [!] Login succeeded but no Bearer token ('nauk_at') found in cookies")
        return None

    print(f"  [OK] Logged in, token={token[:20]}...")
    return session, token


def apply_to_job(session, token: str, job: dict, track: str) -> str:
    sid = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "0000000"
    src = "search"
    logstr = APPLY_LOGSTR_TEMPLATE.format(
        src=APPLY_LOGSTR_MAP.get(src, src), sid=sid
    )

    headers = APPLY_HEADERS.copy()
    headers["authorization"] = f"Bearer {token}"
    headers["nkparam"] = generate_nkparam("srp")

    payload = {
        "strJobsarr": [job["job_id"]],
        "logstr": logstr,
        "flowtype": "show",
        "crossdomain": True,
        "jquery": 1,
        "rdxMsgId": "",
        "chatBotSDK": True,
        "mandatory_skills": [],
        "optional_skills": [],
        "applyTypeId": "107",
        "closebtn": "y",
        "applySrc": APPLY_LOGSTR_MAP.get(src, src),
        "sid": sid,
        "mid": "",
    }

    try:
        resp = session.post(APPLY_URL, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            body = resp.json()
            jobs_resp = body.get("jobs", [])
            if jobs_resp:
                job_result = jobs_resp[0]
                if job_result.get("questionnaire"):
                    return "questionnaire"
                status_code = job_result.get("status", 0)
                msg = (job_result.get("message") or "").lower()
                if status_code == 200 or "successfully applied" in msg:
                    return "applied"
                return f"apply_issue:{msg[:80]}"
            if body.get("applyRedirectUrl"):
                return "applied_redirect"
            if body.get("success") or body.get("status") == "success":
                return "applied"
            return f"unknown:{resp.text[:120]}"
        elif resp.status_code in (401, 403):
            return "auth_failed"
        else:
            return f"http_{resp.status_code}"
    except Exception as e:
        return f"error:{e}"


def print_apply_summary(results: list[dict], tag: str = "APPLY"):
    applied = [r for r in results if r["_apply_status"] == "applied"]
    redirected = [r for r in results if r["_apply_status"] == "applied_redirect"]
    skipped = [r for r in results if r["_apply_status"] == "skipped_applied"]
    qnr = [r for r in results if r["_apply_status"] == "questionnaire"]
    failed = [r for r in results if r["_apply_status"].startswith("error")]
    other_issues = [r for r in results if r["_apply_status"] not in
                    ("applied", "applied_redirect", "skipped_applied", "skipped_low_score",
                     "skipped_limit", "pending", "questionnaire", "aborted_no_login")
                    and r["_apply_status"].startswith(("apply_issue", "unknown"))]
    auth_fail = [r for r in results if r["_apply_status"] == "auth_failed"]
    print(f"\n{'='*60}")
    print(f"{tag} Summary: {len(applied)} applied, {len(redirected)} redirect (tracked), "
          f"{len(skipped)} skipped (already applied), "
          f"{len(qnr)} questionnaire, {len(other_issues)} issues, {len(failed)} errors, {len(auth_fail)} auth failures")
    if applied:
        print(f"\n  Applied:")
        for r in applied:
            print(f"    + {r['title'][:40]:<42} @ {r['company'][:25]:<25} fit={r['fit']}")
    if redirected:
        print(f"\n  Redirected (tracked as applied):")
        for r in redirected:
            print(f"    ~ {r['title'][:40]:<42} @ {r['company'][:25]:<25}")
    if qnr:
        print(f"\n  Questionnaires (skipped):")
        for r in qnr:
            print(f"    ? {r['title'][:40]:<42} @ {r['company'][:25]:<25}")
    if other_issues:
        print(f"\n  Other issues:")
        for r in other_issues:
            print(f"    - {r['title'][:40]:<42} @ {r['company'][:25]:<25} [{r['_apply_status'][:50]}]")
    if failed:
        print(f"\n  Errors:")
        for r in failed:
            print(f"    ! {r['title'][:40]:<42} @ {r['company'][:25]:<25} [{r['_apply_status']}]")
    if auth_fail:
        print(f"\n  [!] Auth failures - session may have expired")


def run_apply(track: str, limit: int, skip_search: bool = False):
    profiles_path = Path("profiles.json")
    if not profiles_path.exists():
        print(f"[!] profiles.json not found. Create it with your Naukri credentials per track.")
        return

    with open(profiles_path, "r") as f:
        profiles = json.load(f)

    if track not in profiles:
        print(f"[!] No profile found for track '{track}' in profiles.json")
        return

    creds = profiles[track]
    applied_db = AppliedJobsDB()
    result_path = Path(f"naukri_results_{track}.csv")

    # Step 1: Search if needed
    if not skip_search or not result_path.exists():
        print(f"\n{'='*60}")
        print(f"Phase 1: Searching jobs for track {track}")
        _, track_results = run([track], test_mode=False)
        results = track_results[track]
    else:
        print(f"\nLoading existing results from {result_path}")
        results = []
        with open(result_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row["fit"] = int(row["fit"])
                results.append(row)

    # Step 2: Filter to unapplied, high-fit jobs
    candidates = []
    for r in results:
        jid = r["job_id"]
        if applied_db.is_applied(jid):
            r["_apply_status"] = "skipped_applied"
            candidates.append(r)
            continue
        if r["fit"] < APPLY_SCORE_CUTOFF:
            r["_apply_status"] = "skipped_low_score"
            candidates.append(r)
            continue
        r["_apply_status"] = "pending"
        candidates.append(r)

    print(f"\n{'='*60}")
    print(f"Phase 2: Review candidates for {track}")
    print(f"  Total jobs in CSV: {len(results)}")
    print(f"  Already applied:   {sum(1 for r in candidates if r['_apply_status'] == 'skipped_applied')}")
    print(f"  Below fit cutoff:  {sum(1 for r in candidates if r['_apply_status'] == 'skipped_low_score')}")
    print(f"  Ready to apply:    {sum(1 for r in candidates if r['_apply_status'] == 'pending')}")

    to_apply = [r for r in candidates if r["_apply_status"] == "pending"]
    if not to_apply:
        print("  No new jobs to apply for.")
        print_apply_summary(candidates)
        return

    if limit and limit < len(to_apply):
        print(f"  Limiting to top {limit} (out of {len(to_apply)})")
        for r in to_apply[limit:]:
            r["_apply_status"] = "skipped_limit"
        to_apply = to_apply[:limit]

    # Print review table
    sep2 = "-" * 90
    print(f"\n{sep2}")
    print(f"{'#':<4} {'Fit':<5} {'Title':<40} {'Company':<25} {'Action'}")
    print(sep2)
    for i, r in enumerate(to_apply, 1):
        print(f"{i:<4} {r['fit']:<5} {r['title'][:38]:<40} {r['company'][:23]:<25} PENDING")
    print(sep2)
    print(f"Will apply to {len(to_apply)} jobs. Press Ctrl+C within 5s to cancel...\n")
    time.sleep(5)

    # Step 3: Login and apply
    print(f"\n{'='*60}")
    print(f"Phase 3: Logging into {track} profile")
    login_result = login_naukri(creds["username"], creds["password"])
    if login_result is None:
        print("[!] Aborting apply - login failed")
        for r in to_apply:
            r["_apply_status"] = "aborted_no_login"
        print_apply_summary(candidates)
        return

    session, token = login_result

    print(f"\nApplying to {len(to_apply)} jobs...")
    for i, r in enumerate(to_apply, 1):
        print(f"  [{i}/{len(to_apply)}] {r['title'][:45]:<48} ", end="")
        status = apply_to_job(session, token, r, track)
        r["_apply_status"] = status
        applied_db.save(r["job_id"], track, status, r["title"], r["company"])

        if status == "applied":
            print("APPLIED")
        elif status == "questionnaire":
            print("SKIP (questionnaire)")
        elif status == "auth_failed":
            print("AUTH FAILED - session expired")
            break
        elif status.startswith("error"):
            print(f"ERROR: {status}")
        else:
            print(status)

        time.sleep(APPLY_DELAY)

    print_apply_summary(candidates)


def run_batch_apply(track: str, batch_size: int, interval_minutes: int):
    profiles_path = Path("profiles.json")
    if not profiles_path.exists():
        print("[!] profiles.json not found")
        return
    with open(profiles_path) as f:
        profiles = json.load(f)
    if track not in profiles:
        print(f"[!] No profile for track '{track}'")
        return

    db = AppliedJobsDB()
    pending = db.count_pending(track)
    if pending == 0:
        print(f"  No pending jobs for {track}. Run --import-csv first.")
        db.close()
        return

    print(f"\n{track}: {pending} jobs to apply, {interval_minutes} min gap between each")
    print("Logging in once for all jobs...")
    creds = profiles[track]
    login_result = login_naukri(creds["username"], creds["password"])
    if login_result is None:
        print("[!] Login failed, aborting")
        db.close()
        return
    session, token = login_result

    applied_count = 0
    qnr_count = 0
    error_count = 0
    batch_num = 0
    stopped = False

    while not stopped:
        jobs = db.get_next_batch(track, batch_size)
        if not jobs:
            break

        batch_num += 1
        batch_applied = 0
        batch_qnr = 0

        print(f"\n{'='*60}")
        print(f"Batch {batch_num} - {len(jobs)} jobs (pending: {db.count_pending(track)})")
        for j in jobs:
            print(f"  {j['title'][:45]:<47} fit={j['fit']}")

        for i, j in enumerate(jobs, 1):
            print(f"[{i}/{len(jobs)}] {j['title'][:45]:<48} ", end="")
            status = apply_to_job(session, token, j, track)
            db.update_job_status(j["job_id"], status)
            db.save(j["job_id"], track, status, j["title"], j["company"])

            if status == "applied":
                print("APPLIED")
                applied_count += 1
                batch_applied += 1
            elif status == "applied_redirect":
                print("APPLIED (redirect)")
                applied_count += 1
                batch_applied += 1
            elif status == "questionnaire":
                print("QUESTIONNAIRE - manual apply")
                qnr_count += 1
                batch_qnr += 1
            elif status == "auth_failed":
                print("AUTH FAILED - session expired, stopping")
                stopped = True
                error_count += 1
                break

            else:
                print(f"ISSUE: {status[:50]}")
                error_count += 1

            if i < len(jobs) and interval_minutes > 0:
                print(f"  (waiting {interval_minutes} min...)")
                try:
                    time.sleep(interval_minutes * 60)
                except KeyboardInterrupt:
                    print("\n  Interrupted by user")
                    stopped = True
                    break

        if stopped:
            break

        remaining = db.count_pending(track)
        if remaining == 0:
            print(f"\n  [OK] All {track} jobs processed!")
            break

    db.close()
    pending = AppliedJobsDB().count_pending(track)
    generate_manual_report(track)
    print(f"\n{'='*60}")
    print(f"{track} Summary: {applied_count} applied, {qnr_count} manual (questionnaire), {error_count} issues")
    print(f"Pending remaining: {pending}")


def generate_manual_report(track: str = None):
    db = AppliedJobsDB()
    label = track if track else "ALL"
    out_path = Path(f"manual_apply_{label}.csv")

    if track:
        rows = db.get_manual_report()
        rows = [r for r in rows if r["track"] == track]
    else:
        rows = db.get_manual_report()

    if not rows:
        print(f"\n  No jobs needing manual apply.")
        db.close()
        return

    print(f"\n{'='*60}")
    print(f"Manual Apply Report - {label}")
    print(f"Total jobs: {len(rows)}")
    print(f"{'='*60}")

    track_groups = {}
    for r in rows:
        t = r["track"]
        if t not in track_groups:
            track_groups[t] = []
        track_groups[t].append(r)

    all_rows = []
    for t in sorted(track_groups):
        print(f"\n  Track {t}:")
        sep = "-" * 80
        print(f"  {sep}")
        for i, r in enumerate(track_groups[t], 1):
            print(f"  {i:<3} {r['title'][:40]:<42} @ {r['company'][:25]:<25}")
            print(f"      URL: {r['url']}")
            row_out = {
                "track": t,
                "title": r["title"],
                "company": r["company"],
                "location": r["location"],
                "salary": r["salary"],
                "url": r["url"],
            }
            all_rows.append(row_out)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["track", "title", "company", "location", "salary", "url"])
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\n  [OK] Report saved to {out_path} - open in any browser and click URLs to apply")

    db.close()


# ─── Entry point ─────────────────────────────────────────────────────────────


def print_stats():
    db = AppliedJobsDB()
    sep = "-" * 50

    print(f"\n{'='*50}")
    print(f"    Naukri Job Hunter - Statistics")
    print(f"{'='*50}")

    total = db.query("SELECT count(1) FROM applies")[0][0]
    companies = db.query("SELECT count(DISTINCT company) FROM applies")[0][0]
    first = db.query("SELECT min(applied_date) FROM applies")[0][0] or "-"
    last = db.query("SELECT max(applied_date) FROM applies")[0][0] or "-"

    print(f"\n  Total jobs applied:     {total}")
    print(f"  Unique companies:       {companies}")
    print(f"  First apply:            {first}")
    print(f"  Latest apply:           {last}")

    # This week trend
    print(f"\n{sep}")
    print(f"  This Week")
    print(sep)
    trend = db.query(
        "SELECT applied_date, count(1) FROM applies "
        "WHERE applied_date >= date('now', '-7 days') "
        "GROUP BY applied_date ORDER BY applied_date"
    )
    week_total = 0
    for d, c in trend:
        print(f"    {d}  {'#' * min(c, 30)} {c}")
        week_total += c
    if not trend:
        print("    (no data this week)")
    print(f"  {'-' * 40}")
    print(f"    Total:                {week_total}")

    # By track
    print(f"\n{sep}")
    print(f"  By Track")
    print(sep)
    track_data = db.query(
        "SELECT track, status, count(1) FROM applies GROUP BY track, status ORDER BY track"
    )
    track_summary = {}
    for t, s, c in track_data:
        if t not in track_summary:
            track_summary[t] = {}
        track_summary[t][s] = c
    for t in sorted(track_summary):
        stats = track_summary[t]
        applied_t = stats.get("applied", 0) + stats.get("applied_redirect", 0)
        qnr = stats.get("questionnaire", 0)
        errors_t = sum(v for k, v in stats.items() if k not in ("applied", "applied_redirect", "questionnaire"))
        total_t = sum(stats.values())
        qnr_pct = f" ({100 * qnr // max(total_t, 1)}%)" if qnr else ""
        print(f"    {t:<4} : {total_t:>4} total  |  {applied_t} applied  |  {qnr} questionnaire{qnr_pct}  |  {errors_t} errors")

    # By status
    status_counts = db.query("SELECT status, count(1) FROM applies GROUP BY status ORDER BY count(1) DESC")
    print(f"\n{sep}")
    print(f"  By Status")
    print(sep)
    for s, c in status_counts:
        pct = 100 * c // total if total else 0
        label = s[:25]
        note = "  <- manual apply needed" if s == "questionnaire" else ""
        print(f"    {label:<25} {c:>4} ({pct}%){note}")

    # Top companies
    top_companies = db.query(
        "SELECT company, count(1) as cnt FROM applies GROUP BY company ORDER BY cnt DESC LIMIT 10"
    )
    print(f"\n{sep}")
    print(f"  Top Companies")
    print(sep)
    for i, (company, cnt) in enumerate(top_companies, 1):
        bar = "#" * min(cnt, 30)
        print(f"    {i:<2}. {company[:25]:<25} {bar} {cnt}")

    # Manual apply list
    manual = db.query(
        "SELECT track, applied_date, company, title FROM applies "
        "WHERE status='questionnaire' ORDER BY applied_date DESC LIMIT 15"
    )
    if manual:
        print(f"\n{sep}")
        print(f"  Manual Apply List (questionnaire jobs - skipped)")
        print(sep)
        print(f"    {'#':<3} {'Trk':<4} {'Date':<12} {'Company':<25} {'Title'}")
        print(f"    {'-'*3} {'-'*4} {'-'*12} {'-'*25} {'-'*30}")
        for i, (t, d, co, ti) in enumerate(manual, 1):
            print(f"    {i:<3} {t:<4} {d:<12} {co[:23]:<25} {ti[:30]}")

    db.close()
    print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vikram's Naukri Job Hunter")
    parser.add_argument("--track", choices=["SM","PM","DIR","ALL"], default="ALL",
                        help="Which track to search (default: ALL)")
    parser.add_argument("--test",  action="store_true",
                        help="Test mode: 1 page per keyword, print table only")
    parser.add_argument("--out",   default="naukri_results.csv",
                        help="Output CSV filename (when --track is SM/PM/DIR)")
    parser.add_argument("--apply", action="store_true",
                        help="Enable apply mode: search + auto-apply to high-fit jobs")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max jobs to apply for in this run (0 = all eligible)")
    parser.add_argument("--skip-search", action="store_true",
                        help="Skip search phase in apply mode, use existing CSVs")
    parser.add_argument("--stats", action="store_true",
                        help="Show apply statistics from applied_jobs.db")
    parser.add_argument("--import-csv", action="store_true",
                        help="Import all CSV results into DB catalog (safe, no re-query)")
    parser.add_argument("--batch-apply", action="store_true",
                        help="Batch apply mode: apply with intervals between batches")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="Jobs per batch (default: 10)")
    parser.add_argument("--interval", type=int, default=10,
                        help="Minutes between batches (default: 10)")
    parser.add_argument("--manual-report", action="store_true",
                        help="Generate manual apply report (questionnaire jobs with URLs)")
    args = parser.parse_args()

    if args.stats:
        print_stats()
        exit(0)

    if args.import_csv:
        print(f"\nImporting CSVs into catalog...")
        db = AppliedJobsDB()
        db.import_all_csvs()
        db.close()
        exit(0)

    if args.batch_apply:
        if args.track == "ALL":
            print("[!] --batch-apply requires a specific track (--track SM/PM/DIR)")
            exit(1)
        run_batch_apply(args.track, args.batch_size, args.interval)
        exit(0)

    if args.manual_report:
        track = None if args.track == "ALL" else args.track
        generate_manual_report(track)
        exit(0)

    if args.apply:
        if args.track == "ALL":
            print("[!] --apply requires a specific track (--track SM/PM/DIR), not ALL")
            print("    Example: python naukri_job_hunter.py --apply --track SM --limit 2")
            exit(1)
        run_apply(args.track, limit=args.limit, skip_search=args.skip_search)
        exit(0)

    tracks = list(SEARCHES.keys()) if args.track == "ALL" else [args.track]

    print(f"Naukri Job Hunter - {TODAY}  |  Tracks: {tracks}  |  Test: {args.test}")
    results, track_results = run(tracks, test_mode=args.test)

    applied_db = AppliedJobsDB()

    if args.test:
        enriched = enrich_results(results, applied_db)
        print_table(enriched, top_n=20)
    else:
        if args.track == "ALL":
            for t in tracks:
                enriched = enrich_results(track_results[t], applied_db)
                out_path = Path(f"naukri_results_{t}.csv")
                save_csv(enriched, out_path)
            enriched_all = enrich_results(results, applied_db)
            print_table(enriched_all, top_n=15)
        else:
            enriched = enrich_results(results, applied_db)
            out_path = Path(args.out)
            save_csv(enriched, out_path)
            print_table(enriched, top_n=15)
