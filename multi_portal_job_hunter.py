"""
multi_portal_job_hunter.py
Multi-portal job search for Vikram Panmand's 3-track senior job search.
Fetches from LinkedIn, Adzuna, Foundit, IIMJobs, Naukri, applies scoring rubric,
and outputs combined ranked CSV + SQLite DB.

Tracks:
  SM   — Scrum Master | Release Train Engineer | Agile Coach
  PM   — Program Manager | Technical/Senior/Delivery Program Manager
  DIR  — Director | VP | Head of Delivery | CTO

Usage:
    python multi_portal_job_hunter.py               # all tracks, all portals
    python multi_portal_job_hunter.py --track SM    # single track
    python multi_portal_job_hunter.py --portal linkedin foundit
    python multi_portal_job_hunter.py --test        # 1 page per keyword, print table
"""

import sys, io, os, json, csv, argparse, time, socket, re, sqlite3, hashlib
from datetime import date, datetime
from pathlib import Path
from functools import lru_cache
from dedup import canonical_url

from settings import (
    SEARCHES, SAFE_KEYWORDS, BFSI_KEYWORDS, GOVERNANCE_KW, SCOPE_KW,
    SENIOR_PM_KW, NEGATIVE_KW, TIER1_BFSI, GCC_FINTECH, IT_SERVICES,
    GOOD_LOCS_PRIMARY, RELOCATABLE_METROS, FRESH_MAX, AGING_MAX,
    COMP_FLOOR, COMP_TARGET, NAUKRI_LOC_MAP, LI_LOCATION_MAP,
    IIMJOBS_LOC_MAP,
    PORTAL_DISPLAY, ENABLED_PORTALS, PAGES, RESULTS_TOP_N, RESULTS_PER_PAGE,
    LOCATION_SCORE_GOOD, LOCATION_SCORE_RELOCATE, RUBRICS, LOOKUP_CHUNK,
)

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
from curl_cffi import requests as curl_requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit("Missing dependency: pip install beautifulsoup4 lxml")

# Force IPv4 everywhere
socket.setdefaulttimeout(20)
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4


# ─── Configuration ────────────────────────────────────────────────────────────

def _load_config():
    cfg_path = Path(__file__).parent / "config.json"
    if not cfg_path.exists():
        return {}
    raw = cfg_path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}

CONFIG = _load_config()

def _cookie(key):
    fpath = Path(__file__).parent / f".{key}_cookie.txt"
    if fpath.exists():
        val = fpath.read_text(encoding="utf-8").strip()
        for prefix in ("1st ", "--"):
            if val.startswith(prefix):
                val = val[len(prefix):].lstrip()
        return val
    raw = CONFIG.get("_raw")
    if raw:
        pat = f'"{key}": "'
        i = raw.find(pat)
        if i >= 0:
            i += len(pat)
            j = raw.find('",\n    "', i)
            if j > i:
                val = raw[i:j]
                for prefix in ("1st ", "--"):
                    if val.startswith(prefix):
                        val = val[len(prefix):].lstrip()
                return val
    env_val = os.environ.get(f"{key.upper()}_COOKIE")
    if env_val:
        return env_val
    return CONFIG.get("cookies", {}).get(key, "")

TODAY = date.today()
MULTI_DB = Path("multi_portal_jobs.db")

# ─── Scoring v2 ───────────────────────────────────────────────────────────────

# Precompile
_RE_DAYS = re.compile(r"\d+")

def has_kw(text: str, kw: str) -> bool:
    return re.search(rf"\b{re.escape(kw)}\b", text) is not None

def count_kw(text: str, kws) -> int:
    return sum(1 for k in kws if has_kw(text, k))


def company_tier(company: str) -> int:
    c = company.lower()
    if any(t in c for t in TIER1_BFSI):  return 10
    if any(t in c for t in GCC_FINTECH): return 8
    if any(t in c for t in IT_SERVICES): return 6
    return 5


# Overseas destinations that leak into India searches (mostly via Foundit's
# Gulf aggregation). Tokens kept unambiguous to avoid false hits on substrings.
FOREIGN_LOCS = (
    "united arab emirates", "uae", "dubai", "abu dhabi", "sharjah",
    "saudi arabia", "saudi", "riyadh", "jeddah",
    "qatar", "doha", "kuwait", "bahrain", "manama", "oman", "muscat",
    "singapore", "malaysia", "kuala lumpur",
    "united kingdom", "london", "united states", "germany",
    "netherlands", "amsterdam", "canada", "toronto", "australia", "sydney",
)


def location_score(loc: str) -> int:
    l = loc.lower()
    # Down-rank jobs outside India before any positive metro match — a UAE job
    # mislabeled or correctly labeled should never score as a home metro.
    if any(x in l for x in FOREIGN_LOCS):        return 1
    if any(x in l for x in GOOD_LOCS_PRIMARY):  return 10
    if "remote" in l:                            return 8
    if "hybrid" in l:                            return 7
    if any(x in l for x in RELOCATABLE_METROS): return 6
    return 3


def comp_score(salary: int):
    if salary >= COMP_FLOOR:
        bonus = int(3 * (salary - COMP_FLOOR) / COMP_FLOOR)
        return min(18, 15 + bonus), None
    if salary > 0:
        return max(3, int(15 * salary / COMP_FLOOR)), "below_floor"
    # Naukri hides salary ("Not Disclosed") on most listings -> salary_min=0.
    # Treat unknown comp as neutral (~half of base) instead of zero so good
    # jobs aren't silently penalised for a missing field. (recall-first tuning)
    return 8, "comp_unknown"


def score_job(job: dict, track: str):
    title   = (job.get("title", "") or "").lower()
    company = (job.get("company", "") or "").lower()
    desc    = (job.get("description", "") or "").lower()
    salary  = job.get("salary_min", 0) or 0
    loc     = (job.get("location", "") or "").lower()
    text    = f"{title} {desc}"
    text_co = f"{text} {company}"
    scores  = {}
    flags   = []

    if len(desc.strip()) < 40:
        flags.append("no_description")

    if track == "SM":
        if any(has_kw(title, k) for k in ["rte", "release train", "agile coach", "enterprise agile"]):
            scores["role_match"] = 25
        elif "senior scrum" in title or "lead scrum" in title or "tribe" in title:
            scores["role_match"] = 22
        elif "scrum master" in title or "scrummaster" in title:
            scores["role_match"] = 18
        else:
            scores["role_match"] = 10
        scores["safe_signal"] = min(20, count_kw(text, SAFE_KEYWORDS) * 7)
        scores["domain_fit"]  = min(15, count_kw(text_co, BFSI_KEYWORDS) * 5)

    elif track == "PM":
        if any(has_kw(title, k) for k in SENIOR_PM_KW):
            scores["role_match"] = 23
        elif has_kw(title, "program manager"):
            scores["role_match"] = 18
        elif has_kw(title, "project manager"):
            scores["role_match"] = 15
        else:
            scores["role_match"] = 10
        scores["governance"] = min(20, count_kw(text, GOVERNANCE_KW) * 5)
        scores["domain_fit"] = min(15, count_kw(text_co, BFSI_KEYWORDS) * 5)

    elif track == "DIR":
        if any(has_kw(title, k) for k in ["vp", "head of", "cto", "chief"]):
            scores["role_match"] = 25
        elif "associate director" in title:
            scores["role_match"] = 20
        elif has_kw(title, "director"):
            scores["role_match"] = 22
        else:
            scores["role_match"] = 10
        scores["scope_scale"] = min(20, count_kw(text, SCOPE_KW) * 5)
        scores["domain_fit"]  = min(15, count_kw(text_co, BFSI_KEYWORDS) * 5)

    cs, cf = comp_score(salary)
    if cs is not None:
        scores["comp"] = cs
    if cf:
        flags.append(cf)

    scores["location"]    = location_score(loc)
    scores["org_quality"] = company_tier(company)

    neg = count_kw(text, NEGATIVE_KW)
    if neg:
        scores["seniority_penalty"] = -10 * neg
        flags.append("junior_signal")

    return scores, sum(scores.values()), flags


def freshness(posted_str):
    """
    Parses a posting timestamp into (tag, modifier, age_days).
    Handles, in order:
      1. Numeric epoch (int, or numeric string) in seconds or ms
      2. ISO-8601 datetime strings (Naukri's modifiedOn/createdOn look like
         "2026-06-08T06:00:00.000Z" or "2026-06-08T06:00:00")
      3. Plain "YYYY-MM-DD"
      4. Relative-text fallbacks ("today", "2 days ago", "1 week ago", "3 months ago")
    Falls back to UNKNOWN only if truly nothing matches.
    """
    if posted_str is None or posted_str == "":
        return "UNKNOWN", -10, None

    # Coerce to string up front - Naukri's JSON can hand back an int for epoch fields.
    if isinstance(posted_str, (int, float)):
        s_raw = str(int(posted_str))
    else:
        s_raw = str(posted_str).strip()

    if not s_raw:
        return "UNKNOWN", -10, None

    s = s_raw.lower()
    age = None

    # 1. Pure numeric epoch (seconds or milliseconds)
    if s_raw.isdigit() and len(s_raw) >= 9:
        ts = int(s_raw)
        ts = ts / 1000 if ts > 1e11 else ts
        try:
            age = (TODAY - datetime.fromtimestamp(ts).date()).days
        except (ValueError, OverflowError, OSError):
            age = None

    # 2. ISO-8601 datetime, with or without trailing Z / milliseconds / timezone offset
    if age is None:
        iso_candidate = s_raw.replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(iso_candidate)
            age = (TODAY - d.date()).days
        except ValueError:
            pass

    # 3. Plain date, e.g. "2026-06-08"
    if age is None:
        try:
            d = datetime.strptime(s_raw[:10], "%Y-%m-%d").date()
            age = (TODAY - d).days
        except ValueError:
            pass

    # 4. Relative-text fallbacks
    if age is None:
        if any(x in s for x in ["just", "today", "now", "few hours", "hour"]):
            age = 0
        elif "yesterday" in s:
            age = 1
        elif "week" in s:
            m = _RE_DAYS.search(s)
            age = int(m.group()) * 7 if m else None
        elif "month" in s:
            m = _RE_DAYS.search(s)
            age = int(m.group()) * 30 if m else 99
        elif "day" in s:
            # check this LAST among relative-text branches: "30+ days" style
            # strings can otherwise be misread if checked before week/month
            m = _RE_DAYS.search(s)
            age = int(m.group()) if m else None

    if age is None:
        return "UNKNOWN", -10, None
    if age < 0:
        # clock skew / future-dated posting - treat as fresh rather than erroring
        age = 0

    if age <= FRESH_MAX:
        return "FRESH", 0, age
    elif age <= AGING_MAX:
        return "AGING", -10, age
    return "STALE", None, age


# ─── Debug helper ─────────────────────────────────────────────────────────────

DEBUG_SAVE = bool(os.environ.get("DEBUG_PORTAL"))

def _save_debug(portal: str, resp):
    if not DEBUG_SAVE:
        return
    path = Path(__file__).parent / f"debug_{portal}.txt"
    content = (
        f"STATUS: {resp.status_code}\nURL: {resp.url}\n"
        f"CONTENT-TYPE: {resp.headers.get('content-type','')}\n\n"
        f"--- BODY (first 4000 chars) ---\n{resp.text[:4000]}"
    )
    path.write_text(content, encoding="utf-8", errors="replace")
    print(f"    → debug saved to {path.name}")


# ─── Portal: LinkedIn ─────────────────────────────────────────────────────────

LI_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

def _li_desc(jid: str) -> str:
    try:
        resp = requests.get(
            f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}",
            headers=LI_HEADERS,
            timeout=10,
            verify=False,
        )
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        el = soup.find("div", class_=lambda c: c and "show-more-less-html__markup" in c)
        if el:
            return el.get_text(separator=" ", strip=True)[:500]
        el = soup.find("section", class_=lambda c: c and "description" in (c or ""))
        if el:
            return el.get_text(separator=" ", strip=True)[:500]
        return ""
    except Exception:
        return ""


def fetch_linkedin(keyword: str, location: str, pages: int = 3) -> list:
    jobs = []
    loc  = LI_LOCATION_MAP.get(location, location)

    for page in range(pages):
        try:
            resp = requests.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                headers=LI_HEADERS,
                params={"keywords": keyword, "location": loc,
                        "start": str(page * 25), "f_TPR": "r604800"},
                timeout=20,
                verify=False,
            )
            if resp.status_code != 200:
                break
            soup   = BeautifulSoup(resp.text, "lxml")
            cards  = soup.find_all("li")
            if not cards:
                break

            for card in cards:
                try:
                    urn_div = card.find("div", {"data-entity-urn": True})
                    if not urn_div:
                        continue
                    jid     = urn_div["data-entity-urn"].split(":")[-1]

                    for sel in ["h3.base-search-card__title",
                                "h3[class*=title]",
                                ".base-search-card__title",
                                "[class*=job-card-list__title]"]:
                        title_el = card.select_one(sel)
                        if title_el and title_el.get_text(strip=True):
                            break
                    for sel in ["h4.base-search-card__subtitle",
                                "h4[class*=subtitle]",
                                ".base-search-card__subtitle",
                                "[class*=job-card-search__company-name]",
                                "[class*=artdeco-entity-lockup__subtitle]"]:
                        comp_el = card.select_one(sel)
                        if comp_el and comp_el.get_text(strip=True):
                            break
                    for sel in ["span.job-search-card__location",
                                "[class*=job-card-search__location]",
                                "[class*=artdeco-entity-lockup__caption]"]:
                        loc_el  = card.select_one(sel)
                        if loc_el and loc_el.get_text(strip=True):
                            break
                    time_el = card.find("time")

                    jobs.append({
                        "job_id":   f"li_{jid}",
                        "portal":   "LinkedIn",
                        "title":    title_el.get_text(strip=True) if title_el else "",
                        "company":  comp_el.get_text(strip=True) if comp_el else "",
                        "location": loc_el.get_text(strip=True) if loc_el else location,
                        "posted_date": time_el.get("datetime", "") if time_el else "",
                        "salary_min": 0,
                        "salary_max": 0,
                        "description": _li_desc(jid),
                        "url": f"https://www.linkedin.com/jobs/view/{jid}",
                    })
                    time.sleep(0.3)
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"    LinkedIn error: {e}")
            break
    return jobs


# ─── Portal: Adzuna (free API, no auth cookie required) ─────────────────────

ADZUNA_API = "https://api.adzuna.com/v1/api/jobs/in/search"

def fetch_adzuna(keyword: str, location: str, pages: int = 3) -> list:
    app_id = os.environ.get("ADZUNA_APP_ID") or _cookie("adzuna_app_id")
    api_key = os.environ.get("ADZUNA_API_KEY") or _cookie("adzuna_api_key")
    if not app_id or not api_key:
        print("    Adzuna: missing ADZUNA_APP_ID / ADZUNA_API_KEY env vars")
        return []

    jobs = []
    seen_urls = set()

    for page in range(pages):
        try:
            resp = requests.get(
                f"{ADZUNA_API}/{page + 1}",
                params={
                    "app_id": app_id,
                    "app_key": api_key,
                    "what": keyword,
                    "where": location,
                    "results_per_page": 20,
                    "sort_by": "date",
                    "max_days_old": 30,
                },
                headers={
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Safari/537.36"),
                },
                timeout=20,
            )
            if resp.status_code != 200:
                print(f"Adzuna HTTP {resp.status_code}")
                break

            data = resp.json()
            items = data.get("results", [])
            if not items:
                break

            for item in items:
                url = item.get("redirect_url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                jid = item.get("id", hashlib.md5(url.encode()).hexdigest()[:12])
                title = item.get("title", "")
                comp_data = item.get("company", {}) or {}
                company = comp_data.get("display_name", "") if isinstance(comp_data, dict) else ""
                loc_data = item.get("location", {}) or {}
                loc_txt = loc_data.get("display_name", location) if isinstance(loc_data, dict) else location
                sal_min = item.get("salary_min", 0) or 0
                sal_max = item.get("salary_max", 0) or 0
                desc = (item.get("description", "") or "")[:500]

                posted_raw = item.get("created", "")
                posted_fmt = ""
                if posted_raw:
                    try:
                        dt = datetime.strptime(posted_raw[:10], "%Y-%m-%d")
                        posted_fmt = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        posted_fmt = posted_raw[:10]

                jobs.append({
                    "job_id":   f"adz_{jid}",
                    "portal":   "Adzuna",
                    "title":    title,
                    "company":  company,
                    "location": loc_txt,
                    "posted_date": posted_fmt,
                    "salary_min": int(sal_min),
                    "salary_max": int(sal_max),
                    "description": desc,
                    "url": url,
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"    Adzuna error: {e}")
            break
    return jobs


# ─── Portal: Foundit (Monster India) ─────────────────────────────────────────

def _foundit_location(locs: list) -> str:
    """Build a location label from Foundit's `locations` array.

    Foundit (monsterindia) aggregates Gulf/overseas jobs that leak into India
    searches. Their location objects vary: India jobs carry a "city", while
    international (e.g. UAE) jobs often carry only "country"/"label" and no
    "city". The old code did `locs[0].get("city", search_location)`, so any
    job without a city silently inherited the *search* city (Mumbai/Pune) —
    stamping foreign jobs as Mumbai/Pune. Never fall back to the search city:
    return whatever real fields exist, or "" if none.
    """
    if not locs:
        return ""
    first = locs[0] if isinstance(locs[0], dict) else {}
    parts = []
    for key in ("city", "locality", "region", "state"):
        v = first.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
            break
    country = first.get("country")
    if isinstance(country, str) and country.strip():
        parts.append(country.strip())
    if not parts:
        for key in ("label", "name", "displayName"):
            v = first.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
                break
    return ", ".join(parts)


def fetch_foundit(keyword: str, location: str, pages: int = 3) -> list:
    cookie = _cookie("foundit")
    if not cookie:
        print("    Foundit: no cookie - add to config.json")
        return []

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.foundit.in/",
        "Origin": "https://www.foundit.in",
        "Cookie": cookie,
    }

    jobs = []
    base = "https://apiv3.monsterindia.com/raven_ml/api/public/search/v3/jobs"
    for page in range(pages):
        try:
            resp = curl_requests.get(
                base,
                headers=headers,
                params={"query": keyword, "location": location,
                        "limit": 20, "start": page * 20},
                impersonate="chrome124",
                timeout=20,
            )
            if resp.status_code != 200:
                print(f"    Foundit HTTP {resp.status_code}")
                break
            data  = resp.json()
            items = data.get("data", [])
            if not items:
                break

            for item in items:
                company_data = item.get("company") or {}
                comp = company_data.get("name", "") if isinstance(company_data, dict) else ""
                locs = item.get("locations") or []
                loc_txt = _foundit_location(locs)
                posted = item.get("postedAt", 0)
                try:
                    posted = datetime.fromtimestamp(posted / 1000).strftime("%Y-%m-%d")
                except Exception:
                    posted = ""
                sal  = item.get("minimumSalary") or {}
                salx = item.get("maximumSalary") or {}
                salmin = 0
                if isinstance(sal, dict) and sal.get("absoluteValue"):
                    salmin = sal["absoluteValue"]
                elif isinstance(salx, dict) and salx.get("absoluteValue"):
                    salmin = salx["absoluteValue"]
                desc = (item.get("description") or "").replace("<p>", " ").replace("</p>", " ")[:500]
                jid  = item.get("id") or item.get("jobId", "")
                url  = item.get("jdUrl", "") or ""
                if url and not url.startswith("http"):
                    url = "https://www.foundit.in" + url
                if not url:
                    url = f"https://www.foundit.in/job/{jid}"
                jobs.append({
                    "job_id":   f"fi_{jid}",
                    "portal":   "Foundit",
                    "title":    item.get("title", ""),
                    "company":  comp,
                    "location": loc_txt,
                    "posted_date": posted,
                    "salary_min": salmin,
                    "salary_max": 0,
                    "description": desc,
                    "url": url,
                })
            time.sleep(1)
        except Exception as e:
            print(f"    Foundit error: {e}")
            break
    return jobs


# ─── Portal: IIMJobs ─────────────────────────────────────────────────────────

def fetch_iimjobs(keyword: str, location: str, pages: int = 3) -> list:
    cookie = _cookie("iimjobs")
    if not cookie:
        print("IIMJobs requires logged-in session cookie (add to .iimjobs_cookie.txt)")
        return []

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json",
        "Referer": "https://www.iimjobs.com/",
        "Cookie": cookie,
    }

    loc = IIMJOBS_LOC_MAP.get(location, location)
    kw_parts = keyword.lower().split()

    jobs = []
    for page in range(1, pages + 1):
        try:
            resp = curl_requests.get(
                "https://gladiator.iimjobs.com/job/alumni-jobs",
                params={"page": page, "size": 50},
                headers=headers,
                impersonate="chrome124",
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"    IIMJobs API HTTP {resp.status_code}")
                break

            data  = resp.json().get("data", {})
            items = data.get("jobs", [])
            if not items:
                break

            for item in items:
                title  = (item.get("title") or "").strip()
                desc   = (item.get("introText") or "")
                desig  = (item.get("jobdesignation") or "")
                combined = (title + " " + desig + " " + desc).lower()

                if not any(w in combined for w in kw_parts):
                    continue

                raw_locs = [l.get("label", "") for l in (item.get("locations") or []) if l.get("label")]
                if raw_locs and loc != "India":
                    locs_lower = [x.lower() for x in raw_locs]
                    if loc not in locs_lower and not any(loc in x for x in locs_lower):
                        continue

                cd   = item.get("companyData") or {}
                comp = cd.get("companyName") or cd.get("name") or item.get("createdByAlias") or ""
                job_url = item.get("jobDetailUrl") or ""
                if job_url and not job_url.startswith("http"):
                    job_url = "https://www.iimjobs.com" + job_url

                created_raw = item.get("createdTime") or item.get("createdTimeMs") or ""
                if isinstance(created_raw, (int, float)):
                    created = datetime.fromtimestamp(created_raw / 1000).strftime("%Y-%m-%d")
                else:
                    created = str(created_raw)

                jobs.append({
                    "job_id":   f"iim_{item.get('id', page)}_{page}",
                    "portal":   "IIMJobs",
                    "title":    title,
                    "company":  comp,
                    "location": ", ".join(raw_locs) if raw_locs else location,
                    "posted_date": created,
                    "salary_min": item.get("minSal") or 0,
                    "salary_max": item.get("maxSal") or 0,
                    "description": BeautifulSoup(desc, "lxml").get_text(strip=True)[:500] if desc else "",
                    "url": job_url,
                })

            if len(jobs) >= 100:
                break
            time.sleep(1)
        except Exception as e:
            print(f"    IIMJobs error: {e}")
            break
    return jobs


# ─── Portal: Naukri ────────────────────────────────────────────────────────────

NAUKRI_BASE = "https://www.naukri.com/jobapi/v3/search"
NAUKRI_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALrlQ+djR0RjJwBF1xuisHmdFv334MIm
K6LgzJhmLhN7B5yuEyaKoasgXQk3+OQglsOaBxEJ0j5PcTL3nbOvt80CAwEAAQ==
-----END PUBLIC KEY-----"""

_NAUKRI_CRYPTO = None
def _naukri_nkparam(page_type="srp"):
    global _NAUKRI_CRYPTO
    if _NAUKRI_CRYPTO is None:
        try:
            from Crypto.PublicKey import RSA
            from Crypto.Cipher import PKCS1_v1_5
            key = RSA.import_key(NAUKRI_PUBLIC_KEY)
            _NAUKRI_CRYPTO = PKCS1_v1_5.new(key)
        except ImportError:
            raise RuntimeError("Missing dependency: pip install pycryptodome")
    timestamp = int(time.time() * 1000)
    plaintext = f"v0|{timestamp}|121_{page_type}"
    encrypted = _NAUKRI_CRYPTO.encrypt(plaintext.encode("utf-8"))
    import base64
    return base64.b64encode(encrypted).decode("utf-8")

def _naukri_url(raw: str, jid: str) -> str:
    """Return an absolute naukri.com URL.

    Naukri's `jdURL` is often a host-relative path (e.g.
    "job-listings-...-240626004046", sometimes with a leading "/"). Stored
    as-is, the dashboard renders it as a relative href and the browser
    resolves it against its own host (job-hunter-*.onrender.com), producing
    a dead link. Force an absolute https://www.naukri.com URL.
    """
    raw = (raw or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw:
        return "https://www.naukri.com/" + raw.lstrip("/")
    return f"https://www.naukri.com/job-listings-{jid}" if jid else ""


def fetch_naukri(keyword: str, location: str, pages: int = 3) -> list:
    cookie = os.environ.get("NAUKRI_COOKIE") or _cookie("naukri")
    if not cookie:
        print("    Naukri: no cookie - set NAUKRI_COOKIE env var or .naukri_cookie.txt")
        return []

    loc_id = NAUKRI_LOC_MAP.get(location, location)
    headers = {
        "accept": "application/json",
        "appid": "109",
        "clientid": "d3skt0p",
        "content-type": "application/json",
        "systemid": "Naukri",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "gid": "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE",
        "cookie": cookie,
    }

    jobs = []
    for page in range(pages):
        try:
            headers["nkparam"] = _naukri_nkparam("srp")
            resp = requests.get(
                NAUKRI_BASE,
                headers=headers,
                params={
                    "noOfResults": "20",
                    "urlType": "search_by_keyword",
                    "searchType": "adv",
                    "keyword": keyword,
                    "location": location,
                    "pageNo": str(page + 1),
                    "sort": "r",
                    "src": "jobsearchDesk",
                    "latLong": "",
                },
                timeout=20,
            )
            if resp.status_code == 403:
                print(f"    Naukri HTTP 403 - cookie expired")
                break
            if resp.status_code != 200:
                print(f"    Naukri HTTP {resp.status_code}")
                break
            data = resp.json()
            items = data.get("jobDetails", [])
            if not items:
                break

            for item in items:
                jid = item.get("jobId", "")
                if not jid:
                    continue
                placeholders = item.get("placeholders", [])
                exp = sal_label = loc_txt = ""
                for p in placeholders:
                    t = p.get("type", "")
                    if t == "experience":
                        exp = p.get("label", "")
                    elif t == "salary":
                        sal_label = p.get("label", "")
                    elif t == "location":
                        loc_txt = p.get("label", "")

                sal_range = item.get("salaryDetail", {})
                sal_min = sal_range.get("minimumSalary", 0) or 0 if sal_range else 0

                posted_raw = item.get("modifiedOn", "") or item.get("createdOn", "") or ""
                if DEBUG_SAVE and len(jobs) == 0:
                    # One-time-per-page sample so we can confirm the real
                    # field shape Naukri is sending (epoch / ISO / etc.)
                    # without spamming the log for every item.
                    print(f"    [naukri debug] posted_raw sample: "
                          f"{posted_raw!r} (type={type(posted_raw).__name__})")
                jobs.append({
                    "job_id": f"nk_{jid}",
                    "portal": "Naukri",
                    "title": item.get("title", ""),
                    "company": item.get("companyName", ""),
                    "location": loc_txt or location,
                    "posted_date": posted_raw,
                    "salary_min": sal_min,
                    "salary_max": 0,
                    "description": (item.get("jobDescription", "") or "")[:500],
                    "url": _naukri_url(item.get("jdURL") or item.get("jobUrl") or "", jid),
                })
            time.sleep(1)
        except Exception as e:
            print(f"    Naukri error: {e}")
            break
    return jobs


# ─── Portal registry ──────────────────────────────────────────────────────────

PORTALS = {
    "linkedin": fetch_linkedin,
    "adzuna":   fetch_adzuna,
    "foundit":  fetch_foundit,
    "iimjobs":  fetch_iimjobs,
    "naukri":   fetch_naukri,
}
# ─── SQLite Database ─────────────────────────────────────────────────────────

class MultiPortalDB:
    def __init__(self, db_path: Path = MULTI_DB):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(self.db_path))
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS job_listings (
                job_id    TEXT PRIMARY KEY,
                track     TEXT NOT NULL,
                portal    TEXT NOT NULL,
                title     TEXT NOT NULL,
                company   TEXT NOT NULL,
                location  TEXT,
                salary    TEXT,
                posted    TEXT,
                url       TEXT,
                fit       INTEGER DEFAULT 0,
                freshness TEXT,
                scores_json TEXT,
                status    TEXT DEFAULT 'not_applied',
                imported_date TEXT NOT NULL,
                applied_date TEXT,
                last_seen_date TEXT,
                canon_url TEXT
            )
        """)
        for col in ("track", "portal", "fit"):
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_mp_{col} ON job_listings({col})")
        for col in ("applied_date", "last_seen_date", "canon_url"):
            try:
                self.conn.execute(f"ALTER TABLE job_listings ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def save_results(self, results: list) -> int:
        rows = []
        for r in results:
            cu = canonical_url(r.get("url", ""))
            rows.append({
                "job_id": r["job_id"],
                "canon_url": cu,
                "track": r["track"],
                "portal": r.get("portal", ""),
                "title": r.get("title", ""),
                "company": r.get("company", ""),
                "location": r.get("location", ""),
                "salary": r.get("salary", ""),
                "posted": r.get("posted", ""),
                "url": r.get("url", ""),
                "fit": r["fit"],
                "freshness": r.get("freshness", ""),
                "scores_json": r.get("scores_json", ""),
            })
        if not rows:
            return 0
        before = self.conn.total_changes

        for row in rows:
            existing = None
            cu = row["canon_url"]
            if cu:
                existing = self.conn.execute(
                    "SELECT job_id, status, imported_date, applied_date, fit, scores_json "
                    "FROM job_listings WHERE canon_url=?",
                    (cu,)
                ).fetchone()
            if existing:
                self.conn.execute(
                    "UPDATE job_listings SET track=?, portal=?, title=?, company=?, "
                    "location=?, salary=?, posted=?, url=?, fit=?, freshness=?, "
                    "scores_json=?, last_seen_date=? WHERE canon_url=?",
                    (row["track"], row["portal"], row["title"], row["company"],
                     row["location"], row["salary"], row["posted"], row["url"],
                     max(row["fit"], existing[4] or 0), row["freshness"],
                     row["scores_json"] if row["fit"] >= (existing[4] or 0) else existing[5],
                     str(TODAY), cu)
                )
            else:
                self.conn.execute(
                    "INSERT OR IGNORE INTO job_listings "
                    "(job_id, canon_url, track, portal, title, company, location, salary, "
                    " posted, url, fit, freshness, scores_json, status, imported_date, last_seen_date) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_applied', ?, ?)",
                    (row["job_id"], cu, row["track"], row["portal"], row["title"],
                     row["company"], row["location"], row["salary"],
                     row["posted"], row["url"], row["fit"], row["freshness"],
                     row["scores_json"], str(TODAY), str(TODAY))
                )
        self.conn.commit()
        return self.conn.total_changes - before

    def search(self, track: str = None, portal: str = None, min_fit: int = 0) -> list:
        query = "SELECT * FROM job_listings WHERE status='not_applied'"
        params = []
        if track:
            query += " AND track=?"
            params.append(track)
        if portal:
            query += " AND portal=?"
            params.append(portal)
        query += " AND fit>=? ORDER BY fit DESC"
        params.append(min_fit)
        return self.conn.execute(query, params).fetchall()

    def update_status(self, job_id: str, new_status: str) -> bool:
        self.conn.execute("UPDATE job_listings SET status=? WHERE job_id=?", (new_status, job_id))
        self.conn.commit()
        return self.conn.total_changes > 0

    def mark_applied(self, job_id: str) -> bool:
        self.conn.execute(
            "UPDATE job_listings SET status='applied', applied_date=? WHERE job_id=?",
            (str(TODAY), job_id)
        )
        self.conn.commit()
        return self.conn.total_changes > 0

    def count_by_status(self, track: str = None, portal: str = None) -> list:
        query = "SELECT track, portal, status, COUNT(*) FROM job_listings"
        params = []
        clauses = []
        if track:
            clauses.append("track=?")
            params.append(track)
        if portal:
            clauses.append("portal=?")
            params.append(portal)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " GROUP BY track, portal, status ORDER BY track, portal, status"
        return self.conn.execute(query, params).fetchall()

    def all_jobs(self, track: str = None, portal: str = None,
                 min_fit: int = 0, status: str = None) -> list:
        query = "SELECT * FROM job_listings WHERE 1=1"
        params = []
        if track:
            query += " AND track=?"
            params.append(track)
        if portal:
            query += " AND portal=?"
            params.append(portal)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " AND fit>=? ORDER BY fit DESC"
        params.append(min_fit)
        return self.conn.execute(query, params).fetchall()

    def close(self):
        self.conn.close()


# ─── Main pipeline ────────────────────────────────────────────────────────────

def run(tracks: list, portals: list, test_mode: bool = False):
    all_results = []
    seen_ids    = set()
    seen_urls   = set()
    pages       = 1 if test_mode else PAGES

    for track in tracks:
        print(f"\n{'='*60}\nTrack: {track}")

        for search in SEARCHES[track]:
            kw  = search["keyword"]
            loc = search["location"]

            for portal_name in portals:
                fetcher = PORTALS[portal_name]
                print(f"  [{portal_name}] '{kw}' in {loc}...", end=" ", flush=True)

                try:
                    raw_jobs = fetcher(kw, loc, pages)
                except Exception as e:
                    print(f"ERROR: {e}")
                    continue

                added = 0
                for job in raw_jobs:
                    jid = job["job_id"]
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    cu = canonical_url(job.get("url", ""))
                    if cu and cu in seen_urls:
                        continue
                    if cu:
                        seen_urls.add(cu)

                    fresh_tag, penalty, age = freshness(job.get("posted_date", ""))
                    if penalty is None:
                        continue

                    factor_scores, raw_score, flags = score_job(job, track)
                    final_score = raw_score + penalty
                    salary_label = ""
                    if job.get("salary_min") or job.get("salary_max"):
                        salary_label = f"{job['salary_min']}-{job['salary_max']}"

                    all_results.append({
                        "job_id":    jid,
                        "track":     track,
                        "portal":    job.get("portal", portal_name.capitalize()),
                        "fit":       final_score,
                        "freshness": fresh_tag,
                        "age_days":  age if age is not None else "?",
                        "title":     job.get("title", ""),
                        "company":   job.get("company", ""),
                        "location":  job.get("location", ""),
                        "salary":    salary_label,
                        "posted":    job.get("posted_date", ""),
                        "url":       job.get("url", ""),
                        "scores_json": json.dumps({"s": factor_scores, "f": flags}),
                    })
                    added += 1

                print(f"{len(raw_jobs)} fetched, {added} kept")

    all_results.sort(key=lambda x: x["fit"], reverse=True)
    return all_results


def save_csv(results: list, path: Path):
    if not results:
        print("No results to save.")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\n[OK] Saved {len(results)} rows to {path}")


def save_to_db(results: list, db_path: Path = MULTI_DB):
    db = MultiPortalDB(db_path)
    n = db.save_results(results)
    db.close()
    print(f"[OK] Saved {n} new rows to {db_path}")


def print_table(results: list, top_n: int = 20):
    sep = "-" * 120
    print(f"\n{sep}")
    print(f"{'#':<3} {'Fit':<5} {'Fresh':<7} {'Track':<5} {'Portal':<11} {'Title':<33} {'Company':<22} {'Location'}")
    print(sep)
    for i, r in enumerate(results[:top_n], 1):
        print(f"{i:<3} {r['fit']:<5} {r['freshness']:<7} {r['track']:<5} "
              f"{r['portal']:<11} {r['title'][:31]:<33} {r['company'][:20]:<22} {r['location'][:20]}")
    print(sep)
    print(f"Showing top {min(top_n, len(results))} of {len(results)} results\n")


# ─── HTML Report ───────────────────────────────────────────────────────────────

def generate_html_report(jobs: list, output: Path, serve_mode: bool = False):
    """Generate a standalone HTML page with sortable/filterable job table."""
    import webbrowser
    rows_html = ""
    for j in jobs:
        job_id, track, portal, title, company, location, salary, posted, url, fit, freshness, scores_json, status, imported_date, applied_date, last_seen_date = j[:16] + (None,) * max(0, 16 - len(j))
        fit_cls = "fit-high" if fit >= 60 else ("fit-mid" if fit >= 40 else "fit-low")
        url = url or ""
        url_display = url[:60] + "..." if len(url) > 60 else url
        status_display = f"applied {applied_date}" if status == "applied" and applied_date else status
        rows_html += f"""
        <tr data-job-id="{job_id}">
          <td>{fit}</td>
          <td>{freshness}</td>
          <td>{track}</td>
          <td>{portal}</td>
          <td>{title}</td>
          <td>{company}</td>
          <td>{location}</td>
          <td><span class="status-{status}">{status_display}</span></td>
          <td class="url-cell"><a class="job-link" href="{url}" target="_blank" title="{url}">{url_display}</a></td>
        </tr>"""

    js_intercept = """
function attachApplyHandler() {
  document.querySelectorAll('.job-link').forEach(function(link) {
    link.addEventListener('click', function(e) {
      // Only intercept when served via HTTP (server mode)
      if (window.location.protocol !== 'http:' && window.location.protocol !== 'https:') return;
      e.preventDefault();
      var jobId = this.closest('tr').dataset.jobId;
      var url = this.href;
      var row = this.closest('tr');
      var statusCell = row.cells[7];
      fetch('/apply/' + encodeURIComponent(jobId), { method: 'POST' })
        .then(function(r) {
          if (r.ok) {
            statusCell.innerHTML = '<span class="status-applied">applied ' + new Date().toISOString().slice(0,10) + '</span>';
          }
          window.open(url, '_blank');
        })
        .catch(function() {
          // fallback: just open the URL
          window.open(url, '_blank');
        });
    });
  });
}
"""
    if serve_mode:
        js_intercept += "attachApplyHandler();\n"

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job Queue - Multi Portal</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font: 14px/1.5 system-ui, sans-serif; background: #f5f5f5; padding: 20px; }
  h1 { margin-bottom: 12px; }
  .filters { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
  .filters label { font-weight: 600; }
  .filters select, .filters input { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; }
  .filters .count { margin-left: auto; color: #555; }
  table { width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #eee; white-space: nowrap; }
  th { background: #f0f0f0; cursor: pointer; user-select: none; position: sticky; top: 0; }
  th:hover { background: #e0e0e0; }
  th::after { content: " \\25B4\\25BE"; font-size: 9px; color: #999; }
  tr:hover { background: #fafafa; }
  .fit-high { color: #1a7d1a; font-weight: 700; }
  .fit-mid  { color: #b8860b; }
  .fit-low  { color: #999; }
  .status-not_applied { color: #d32f2f; }
  .status-applied { color: #1a7d1a; }
  .status-skipped { color: #999; }
  .status-not_interested { color: #999; }
  .url-cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis; }
  .url-cell a { color: #1565c0; text-decoration: none; }
  .url-cell a:hover { text-decoration: underline; cursor: pointer; }
</style>
</head>
<body>
<h1>Job Queue</h1>
<div class="filters">
  <label>Track: <select id="fTrack"><option value="">All</option><option>SM</option><option>PM</option><option>DIR</option></select></label>
  <label>Portal: <select id="fPortal"><option value="">All</option><option>LinkedIn</option><option>Adzuna</option><option>Foundit</option><option>IIMJobs</option><option>Naukri</option></select></label>
  <label>Status: <select id="fStatus"><option value="">All</option><option>not_applied</option><option>applied</option><option>skipped</option><option>not_interested</option></select></label>
  <label>Min Fit: <input id="fMinFit" type="number" value="0" style="width:60px"></label>
  <span class="count" id="jobCount"></span>
</div>
<table>
<thead><tr>
  <th data-col="fit" data-num="1">Fit</th>
  <th data-col="freshness">Fresh</th>
  <th data-col="track">Track</th>
  <th data-col="portal">Portal</th>
  <th data-col="title">Title</th>
  <th data-col="company">Company</th>
  <th data-col="location">Location</th>
  <th data-col="status">Status</th>
  <th>URL</th>
</tr></thead>
<tbody id="tbody">__ROWS__</tbody>
</table>
<script>""" + js_intercept + """
const tbody = document.getElementById('tbody');
const allRows = Array.from(tbody.querySelectorAll('tr'));
let sortCol = 'fit', sortDesc = true;

function filterAndSort() {
  const fTrack = document.getElementById('fTrack').value;
  const fPortal = document.getElementById('fPortal').value;
  const fStatus = document.getElementById('fStatus').value;
  const fMin = parseInt(document.getElementById('fMinFit').value) || 0;
  let filtered = allRows.filter(r => {
    const c = r.cells;
    return (!fTrack || c[2].textContent === fTrack) &&
           (!fPortal || c[3].textContent === fPortal) &&
           (!fStatus || c[7].textContent === fStatus) &&
           (parseInt(c[0].textContent) >= fMin);
  });
  const colIdx = {fit:0, freshness:1, track:2, portal:3, title:4, company:5, location:6, status:7}[sortCol];
  filtered.sort((a,b) => {
    const va = a.cells[colIdx].textContent, vb = b.cells[colIdx].textContent;
    const na = parseFloat(va), nb = parseFloat(vb);
    const cmp = isNaN(na) || isNaN(nb) ? va.localeCompare(vb) : na - nb;
    return sortDesc ? -cmp : cmp;
  });
  tbody.innerHTML = '';
  filtered.forEach(r => tbody.appendChild(r));
  document.getElementById('jobCount').textContent = filtered.length + ' jobs';
}
document.querySelectorAll('.filters select, .filters input').forEach(el => el.addEventListener('change', filterAndSort));
document.querySelectorAll('th').forEach(th => th.addEventListener('click', () => {
  if (th.dataset.col) {
    if (sortCol === th.dataset.col) sortDesc = !sortDesc;
    else { sortCol = th.dataset.col; sortDesc = true; }
    filterAndSort();
  }
}));
filterAndSort();
</script>
</body>
</html>"""

    html = html.replace("__ROWS__", rows_html)
    output.write_text(html, encoding="utf-8")
    print(f"[OK] Report saved: {output}")
    if not serve_mode:
        webbrowser.open(str(output.resolve()))


# ─── Status Summary ────────────────────────────────────────────────────────────

def print_status_summary(track: str = None, portal: str = None):
    db = MultiPortalDB()
    rows = db.count_by_status(track, portal)
    db.close()

    if not rows:
        print("No jobs found.")
        return

    print(f"\n{'='*60}")
    print(f"  STATUS SUMMARY{' for ' + track if track else ''}{' on ' + portal if portal else ''}")
    print(f"{'='*60}")
    print(f"{'Track':<6} {'Portal':<12} {'Status':<18} {'Count':<6}")
    print("-" * 42)
    totals = {}
    grand = 0
    for r in rows:
        t, p, s, c = r
        print(f"{t:<6} {p:<12} {s:<18} {c:<6}")
        totals[s] = totals.get(s, 0) + c
        grand += c
    print("-" * 42)
    for s, c in sorted(totals.items()):
        print(f"{'':<6} {'':<12} {s:<18} {c:<6}")
    print(f"{'':<6} {'':<12} {'TOTAL':<18} {grand:<6}")
    print()


# ─── Interactive Review ───────────────────────────────────────────────────────

def review_mode(track: str = None, portal: str = None, min_fit: int = 0):
    import webbrowser

    db = MultiPortalDB()
    jobs = db.search(track, portal, min_fit)
    db.close()

    if not jobs:
        print("No pending jobs to review.")
        return

    total = len(jobs)
    idx = 0
    applied = 0
    skipped = 0
    not_int = 0

    print(f"\n{'='*60}")
    print(f"  REVIEW MODE – {total} jobs to review")
    print(f"  Commands: [A]pply [S]kip [N]o interest [Q]uit")
    print(f"{'='*60}\n")

    while idx < total:
        j = jobs[idx]
        job_id, jtrack, portal_name, title, company, location, salary, posted, url, fit, freshness, scores_json, status, imported_date = j

        print(f"[{idx+1}/{total}]  Fit: {fit}  {freshness}  |  {jtrack} / {portal_name}")
        print(f"      {title}")
        print(f"      {company}  —  {location}")
        if url:
            print(f"      {url}")
        print()

        while True:
            try:
                inp = input("  Action [A/S/N/Q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nQuitting.")
                inp = "q"

            if inp == "q":
                print(f"\nDone. {applied} applied, {skipped} skipped, {not_int} no interest ({idx+1-not_int-skipped-applied} left unseen)")
                return

            if inp in ("a", "s", "n"):
                break

            print("  Invalid. Use A=Apply, S=Skip, N=No interest, Q=Quit")

        db = MultiPortalDB()
        if inp == "a":
            db.update_status(job_id, "applied")
            applied += 1
            if url:
                print(f"  Opening: {url}")
                webbrowser.open(url)
                input("  Press Enter after you've applied...")
            else:
                print("  (no URL to open)")
        elif inp == "s":
            db.update_status(job_id, "skipped")
            skipped += 1
            print("  Skipped.")
        elif inp == "n":
            db.update_status(job_id, "not_interested")
            not_int += 1
            print("  Marked not interested.")
        db.close()

        print()
        idx += 1

    print(f"All done! {applied} applied, {skipped} skipped, {not_int} no interest.")


# ─── Serve Mode (Interactive HTML + API) ─────────────────────────────────────

def serve_report(track: str = None, portal: str = None, min_fit: int = 0,
                 port: int = 8080):
    import http.server
    import json
    import webbrowser
    from urllib.parse import urlparse

    class ReportHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                db = MultiPortalDB()
                jobs = db.all_jobs(track, portal, min_fit)
                db.close()
                buf = Path("job_queue_report.html")
                generate_html_report(jobs, buf, serve_mode=True)
                html = buf.read_text(encoding="utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path.startswith("/apply/"):
                job_id = self.path[len("/apply/"):]
                db = MultiPortalDB()
                ok = db.mark_applied(job_id)
                db.close()
                if ok:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "job_id": job_id}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False}).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt, *args):
            print(f"[server] {args[0]}" if args else "")

    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), ReportHandler)
    print(f"\n{'='*60}")
    print(f"  Interactive Report Server running at:")
    print(f"  → http://localhost:{port}")
    print(f"  Click any job URL → status updates to 'applied' + date in DB")
    print(f"  Press Ctrl+C to stop the server.")
    print(f"{'='*60}\n")
    webbrowser.open(f"http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Portal Job Hunter")
    parser.add_argument("--track",  choices=["SM","PM","DIR","ALL"], default="ALL")
    parser.add_argument("--portal", choices=list(PORTALS.keys()), nargs="+",
                        default=list(PORTALS.keys()),
                        help="Which portals to search (default: all)")
    parser.add_argument("--test",  action="store_true",
                        help="Test mode: 1 page per keyword, print table only")
    parser.add_argument("--out",   default="multi_portal_results.csv",
                        help="Output CSV filename")
    parser.add_argument("--report", action="store_true",
                        help="Generate HTML report from DB and open in browser")
    parser.add_argument("--review", action="store_true",
                        help="Interactive review mode: browse jobs, apply/skip/mark")
    parser.add_argument("--status", action="store_true",
                        help="Print status summary counts")
    parser.add_argument("--min-fit", type=int, default=0,
                        help="Minimum fit score for report/review (default: 0)")
    parser.add_argument("--serve", action="store_true",
                        help="Start interactive web server with click-to-apply")
    parser.add_argument("--port", type=int, default=8080,
                        help="Port for --serve mode (default: 8080)")
    parser.add_argument("--cloud", action="store_true",
                        help="Use Supabase cloud DB instead of local SQLite")
    parser.add_argument("--clear", action="store_true",
                        help="DELETE all job listings from Supabase for this user (use with caution)")
    parser.add_argument("--force", action="store_true",
                        help="Skip confirmation for destructive operations (--clear)")
    parser.add_argument("--pull", action="store_true",
                        help="Pull cloud DB into local SQLite")
    parser.add_argument("--email", default="",
                        help="User email for cloud DB (default: USER_EMAIL env var)")
    args = parser.parse_args()

    track_arg = None if args.track == "ALL" else args.track
    portal_arg = PORTAL_DISPLAY.get(args.portal[0]) if args.portal and len(args.portal) == 1 else None

    # Cloud DB import (lazy)
    cloud_db = None
    could_not_connect = False
    if args.cloud or args.pull:
        try:
            from cloud_db import CloudDB, is_available
            if is_available():
                cloud_db = CloudDB(user_id=args.email)
            else:
                print("[!] Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY env vars.")
                could_not_connect = True
        except ImportError:
            print("[!] cloud_db.py not found. Run with local SQLite or add cloud_db.py.")
            could_not_connect = True
        except Exception as e:
            print(f"[!] Supabase connection failed: {e}")
            could_not_connect = True

    if could_not_connect:
        if args.pull:
            exit(1)
        print("[~] Falling back to local SQLite mode")
        args.cloud = False

    if args.pull:
        if not cloud_db:
            exit(1)
        db = MultiPortalDB()
        n = cloud_db.pull_to_local(db)
        db.close()
        print(f"[OK] Pulled {n} jobs from cloud to local DB")
        exit(0)

    if args.clear:
        if not cloud_db:
            print("[!] --clear requires Supabase connection. Set SUPABASE_URL and SUPABASE_KEY.")
            exit(1)
        if args.force:
            n = cloud_db.clear_all()
            print(f"[OK] Deleted {n} rows")
        else:
            confirm = input(f"  This will DELETE ALL job listings for user '{cloud_db.user_id}' from Supabase. Are you sure? (yes/no): ")
            if confirm.strip().lower() == "yes":
                n = cloud_db.clear_all()
                print(f"[OK] Deleted {n} rows")
            else:
                print("Cancelled.")
        exit(0)

    if args.serve:
        serve_report(track_arg, portal_arg, args.min_fit, args.port)
    elif args.report:
        db = MultiPortalDB()
        jobs = db.all_jobs(track_arg, portal_arg, args.min_fit)
        db.close()
        generate_html_report(jobs, Path("job_queue_report.html"))
    elif args.review:
        review_mode(track_arg, portal_arg, args.min_fit)
    elif args.status:
        print_status_summary(track_arg, portal_arg)
    else:
        tracks  = list(SEARCHES.keys()) if args.track == "ALL" else [args.track]
        portals = args.portal

        print(f"Multi-Portal Job Hunter — {TODAY}")
        print(f"Tracks: {tracks}  |  Portals: {portals}  |  Test: {args.test}")

        if not args.cloud:
            print()
            print("  ⚠️  WARNING: Running without --cloud — results will NOT be pushed to Supabase dashboard")
            print("  ⚠️  Add --cloud to sync jobs to the web dashboard")
            print()

        try:
            results = run(tracks, portals, test_mode=args.test)
            print_table(results, top_n=20)

            if not args.test:
                save_csv(results, Path(args.out))
                if args.cloud:
                    try:
                        n = cloud_db.save_results(results)
                        print(f"[OK] Saved to Supabase: {n} rows")
                    except Exception as e:
                        print(f"[!] Supabase save failed: {e}")
                        import traceback
                        traceback.print_exc()
                        print("[~] Falling back to local SQLite save")
                        save_to_db(results)
                else:
                    save_to_db(results)
        except Exception as e:
            print(f"[!] Pipeline crashed: {e}")
            import traceback
            traceback.print_exc()
            exit(1)
