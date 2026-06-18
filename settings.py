"""
settings.py
Central configuration loader. Loads .env + settings.json first, falls back to code defaults.
Provides module-level constants used by all job hunter scripts.
Call load_from_supabase() to override from cloud before running in GitHub Actions.
"""
import os, json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_SETTINGS_PATH = Path(__file__).parent / "settings.json"
_supabase_data = None

# ─── Default values (code defaults) ─────────────────────────────────────────

DEFAULT_SEARCHES = {
    "SM": [
        {"keyword": "Scrum Master", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Scrum Master", "location": "Pune", "loc_id": "67"},
        {"keyword": "Senior Scrum Master", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Release Train Engineer", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Release Train Engineer", "location": "Pune", "loc_id": "67"},
        {"keyword": "Agile Coach", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Agile Coach", "location": "Pune", "loc_id": "67"},
        {"keyword": "Enterprise Agile Coach", "location": "Mumbai", "loc_id": "103"},
    ],
    "PM": [
        {"keyword": "Program Manager", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Program Manager", "location": "Pune", "loc_id": "67"},
        {"keyword": "Technical Program Manager", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Technical Program Manager", "location": "Pune", "loc_id": "67"},
        {"keyword": "Senior Program Manager", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Senior Program Manager", "location": "Pune", "loc_id": "67"},
        {"keyword": "Delivery Program Manager", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Delivery Program Manager", "location": "Pune", "loc_id": "67"},
        {"keyword": "Delivery Manager", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Project Manager", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Project Manager", "location": "Pune", "loc_id": "67"},
    ],
    "DIR": [
        {"keyword": "Director Engineering", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Director Engineering", "location": "Pune", "loc_id": "67"},
        {"keyword": "Senior Director", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "VP Engineering", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Vice President", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Vice President", "location": "Pune", "loc_id": "67"},
        {"keyword": "Head of Delivery", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Head of Engineering", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Director Delivery", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "CTO", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Delivery Head", "location": "Mumbai", "loc_id": "103"},
        {"keyword": "Delivery Head", "location": "Pune", "loc_id": "67"},
    ],
}

DEFAULT_SCORING_KEYWORDS = {
    "SAFE_KEYWORDS": ["safe", "pi planning", "agile release train", "scaled agile", "less framework", "nexus", "scrum@scale", "lean agile", "lean portfolio", "art sync"],
    "BFSI_KEYWORDS": ["bank", "banking", "financial services", "fintech", "insurance", "payments", "capital markets", "credit", "lending", "wealth", "asset management", "securities", "trading", "broking"],
    "GOVERNANCE_KW": ["program governance", "raid", "steering committee", "p&l", "budget", "pmo", "transformation", "portfolio", "roadmap", "stakeholder management", "delivery governance"],
    "SCOPE_KW": ["p&l", "portfolio", "multi-account", "org building", "headcount", "span of control", "delivery org", "practice lead"],
    "SENIOR_PM_KW": ["senior program", "sr program", "technical program", "delivery program", "tpm", "program governance"],
    "NEGATIVE_KW": ["intern", "internship", "fresher", "trainee", "graduate program", "entry level", "junior"],
}

DEFAULT_TRACKS = ["SM", "PM", "DIR"]

DEFAULT_COMPANY_TIERS = {
    "TIER1_BFSI": ["barclays", "jpmorgan", "jp morgan", "citi", "citibank", "deutsche", "morgan stanley", "goldman", "amex", "american express", "mastercard", "visa", "ubs", "hsbc", "standard chartered", "nomura", "blackrock", "fidelity", "wells fargo", "bank of america", "bnp paribas"],
    "GCC_FINTECH": ["nasdaq", "fiserv", "fis", "broadridge", "paypal", "razorpay", "phonepe", "cred", "groww", "zerodha", "navi", "paytm", "stripe", "revolut", "wise"],
    "IT_SERVICES": ["accenture", "capgemini", "infosys", "tcs", "wipro", "cognizant", "hcl", "ltimindtree", "mphasis", "persistent", "deloitte", "ey", "pwc", "kpmg"],
}

DEFAULT_LOCATION_PREFS = {
    "GOOD_LOCS_PRIMARY": ["mumbai", "navi mumbai", "thane", "pune"],
    "RELOCATABLE_METROS": ["bengaluru", "bangalore", "hyderabad", "gurgaon", "gurugram", "noida"],
    "LOCATION_SCORE_GOOD": ["mumbai", "pune", "thane", "navi mumbai", "remote", "hybrid"],
    "LOCATION_SCORE_RELOCATE": ["bengaluru", "bangalore", "hyderabad", "gurgaon", "gurugram", "noida"],
}

DEFAULT_LOCATION_MAPS = {
    "NAUKRI_LOC_MAP": {"Mumbai": "103", "Pune": "67", "Bangalore": "4", "Delhi": "96", "Hyderabad": "17", "Chennai": "9", "Kolkata": "21", "Ahmedabad": "1", "Noida": "114", "Gurgaon": "118"},
    "LI_LOCATION_MAP": {"Mumbai": "Mumbai Metropolitan Region", "Pune": "Pune, Maharashtra, India"},
    "IIMJOBS_LOC_MAP": {"Mumbai": "Mumbai", "Pune": "Pune", "Bangalore": "Bangalore", "Delhi": "Delhi", "Hyderabad": "Hyderabad", "Chennai": "Chennai", "Kolkata": "Kolkata", "Ahmedabad": "Ahmedabad", "Noida": "Noida", "Gurgaon": "Gurgaon"},
}

DEFAULT_THRESHOLDS = {
    "FRESH_MAX": 3,
    "AGING_MAX": 7,
    "COMP_FLOOR": 4000000,
    "COMP_TARGET": 5000000,
    "COMP_FLOOR_DIR": 5000000,
    "APPLY_SCORE_CUTOFF": 50,
    "APPLY_DELAY": 5.0,
    "PAGES": 3,
    "NAUKRI_PAGES": 5,
    "RESULTS_PER_PAGE": 20,
    "RESULTS_TOP_N": 20,
    "LOOKUP_CHUNK": 50,
}

DEFAULT_SCORING_RUBRICS = {
    "SM": {"role_match": 25, "safe_signal": 20, "domain_fit": 15, "comp": 15, "location": 10, "availability": 5, "org_quality": 10},
    "PM": {"role_match": 23, "governance": 20, "domain_fit": 15, "comp": 15, "location": 10, "availability": 5, "org_quality": 10},
    "DIR": {"role_match": 25, "scope_scale": 20, "domain_fit": 15, "comp": 15, "location": 10, "availability": 5, "org_quality": 10},
}

DEFAULT_PORTALS = {
    "enabled": ["linkedin", "adzuna", "foundit", "iimjobs", "naukri"],
    "display_names": {"linkedin": "LinkedIn", "adzuna": "Adzuna", "foundit": "Foundit", "iimjobs": "IIMJobs", "naukri": "Naukri"},
}

_DEFAULTS = {
    "searches": DEFAULT_SEARCHES,
    "scoring_keywords": DEFAULT_SCORING_KEYWORDS,
    "company_tiers": DEFAULT_COMPANY_TIERS,
    "location_prefs": DEFAULT_LOCATION_PREFS,
    "location_maps": DEFAULT_LOCATION_MAPS,
    "thresholds": DEFAULT_THRESHOLDS,
    "scoring_rubrics": DEFAULT_SCORING_RUBRICS,
    "portals": DEFAULT_PORTALS,
}


def _load_file():
    if _SETTINGS_PATH.exists():
        try:
            return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


_file_data = _load_file()


def get(*keys, default=None):
    val = _file_data
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default


def _deep_default(category, key=None):
    d = _DEFAULTS.get(category, {})
    if key is not None:
        return d.get(key, {})
    return d


# ─── Section accessors ─────────────────────────────────────────────────────

SEARCHES               = get("searches", default=DEFAULT_SEARCHES)
SCORING_KEYWORDS       = get("scoring_keywords", default=DEFAULT_SCORING_KEYWORDS)
COMPANY_TIERS          = get("company_tiers", default=DEFAULT_COMPANY_TIERS)
LOCATION_PREFS         = get("location_prefs", default=DEFAULT_LOCATION_PREFS)
LOCATION_MAPS          = get("location_maps", default=DEFAULT_LOCATION_MAPS)
THRESHOLDS             = get("thresholds", default=DEFAULT_THRESHOLDS)
SCORING_RUBRICS        = get("scoring_rubrics", default=DEFAULT_SCORING_RUBRICS)
PORTALS                = get("portals", default=DEFAULT_PORTALS)

# ─── Flat convenience aliases (mirror original hardcoded names) ────────────

SAFE_KEYWORDS    = SCORING_KEYWORDS.get("SAFE_KEYWORDS", DEFAULT_SCORING_KEYWORDS["SAFE_KEYWORDS"])
BFSI_KEYWORDS    = SCORING_KEYWORDS.get("BFSI_KEYWORDS", DEFAULT_SCORING_KEYWORDS["BFSI_KEYWORDS"])
GOVERNANCE_KW    = SCORING_KEYWORDS.get("GOVERNANCE_KW", DEFAULT_SCORING_KEYWORDS["GOVERNANCE_KW"])
SCOPE_KW         = SCORING_KEYWORDS.get("SCOPE_KW", DEFAULT_SCORING_KEYWORDS["SCOPE_KW"])
SENIOR_PM_KW     = SCORING_KEYWORDS.get("SENIOR_PM_KW", DEFAULT_SCORING_KEYWORDS["SENIOR_PM_KW"])
NEGATIVE_KW      = SCORING_KEYWORDS.get("NEGATIVE_KW", DEFAULT_SCORING_KEYWORDS["NEGATIVE_KW"])

TIER1_BFSI       = COMPANY_TIERS.get("TIER1_BFSI", DEFAULT_COMPANY_TIERS["TIER1_BFSI"])
GCC_FINTECH      = COMPANY_TIERS.get("GCC_FINTECH", DEFAULT_COMPANY_TIERS["GCC_FINTECH"])
IT_SERVICES      = COMPANY_TIERS.get("IT_SERVICES", DEFAULT_COMPANY_TIERS["IT_SERVICES"])

GOOD_LOCS_PRIMARY     = LOCATION_PREFS.get("GOOD_LOCS_PRIMARY", DEFAULT_LOCATION_PREFS["GOOD_LOCS_PRIMARY"])
RELOCATABLE_METROS    = LOCATION_PREFS.get("RELOCATABLE_METROS", DEFAULT_LOCATION_PREFS["RELOCATABLE_METROS"])
LOCATION_SCORE_GOOD   = LOCATION_PREFS.get("LOCATION_SCORE_GOOD", DEFAULT_LOCATION_PREFS["LOCATION_SCORE_GOOD"])
LOCATION_SCORE_RELOCATE = LOCATION_PREFS.get("LOCATION_SCORE_RELOCATE", DEFAULT_LOCATION_PREFS["LOCATION_SCORE_RELOCATE"])

NAUKRI_LOC_MAP  = LOCATION_MAPS.get("NAUKRI_LOC_MAP", DEFAULT_LOCATION_MAPS["NAUKRI_LOC_MAP"])
LI_LOCATION_MAP = LOCATION_MAPS.get("LI_LOCATION_MAP", DEFAULT_LOCATION_MAPS["LI_LOCATION_MAP"])
IIMJOBS_LOC_MAP = LOCATION_MAPS.get("IIMJOBS_LOC_MAP", DEFAULT_LOCATION_MAPS["IIMJOBS_LOC_MAP"])

FRESH_MAX       = THRESHOLDS.get("FRESH_MAX", DEFAULT_THRESHOLDS["FRESH_MAX"])
AGING_MAX       = THRESHOLDS.get("AGING_MAX", DEFAULT_THRESHOLDS["AGING_MAX"])
COMP_FLOOR      = THRESHOLDS.get("COMP_FLOOR", DEFAULT_THRESHOLDS["COMP_FLOOR"])
COMP_TARGET     = THRESHOLDS.get("COMP_TARGET", DEFAULT_THRESHOLDS["COMP_TARGET"])
COMP_FLOOR_DIR  = THRESHOLDS.get("COMP_FLOOR_DIR", DEFAULT_THRESHOLDS["COMP_FLOOR_DIR"])
APPLY_SCORE_CUTOFF = THRESHOLDS.get("APPLY_SCORE_CUTOFF", DEFAULT_THRESHOLDS["APPLY_SCORE_CUTOFF"])
APPLY_DELAY     = THRESHOLDS.get("APPLY_DELAY", DEFAULT_THRESHOLDS["APPLY_DELAY"])
PAGES           = THRESHOLDS.get("PAGES", DEFAULT_THRESHOLDS["PAGES"])
NAUKRI_PAGES    = THRESHOLDS.get("NAUKRI_PAGES", DEFAULT_THRESHOLDS["NAUKRI_PAGES"])
RESULTS_PER_PAGE = THRESHOLDS.get("RESULTS_PER_PAGE", DEFAULT_THRESHOLDS["RESULTS_PER_PAGE"])
RESULTS_TOP_N   = THRESHOLDS.get("RESULTS_TOP_N", DEFAULT_THRESHOLDS["RESULTS_TOP_N"])
LOOKUP_CHUNK    = THRESHOLDS.get("LOOKUP_CHUNK", DEFAULT_THRESHOLDS["LOOKUP_CHUNK"])

TRACKS           = get("tracks", default=DEFAULT_TRACKS)
PORTAL_DISPLAY  = PORTALS.get("display_names", DEFAULT_PORTALS["display_names"])
ENABLED_PORTALS = PORTALS.get("enabled", DEFAULT_PORTALS["enabled"])

RUBRICS = SCORING_RUBRICS


# ─── Cloud sync ─────────────────────────────────────────────────────────────

def load_from_supabase(user_id=None):
    """Override settings from Supabase `settings` table. Returns True if loaded."""
    global _file_data, SEARCHES, SAFE_KEYWORDS, BFSI_KEYWORDS, GOVERNANCE_KW
    global SCOPE_KW, SENIOR_PM_KW, NEGATIVE_KW, TIER1_BFSI, GCC_FINTECH, IT_SERVICES
    global GOOD_LOCS_PRIMARY, RELOCATABLE_METROS, LOCATION_SCORE_GOOD, LOCATION_SCORE_RELOCATE
    global NAUKRI_LOC_MAP, LI_LOCATION_MAP, IIMJOBS_LOC_MAP
    global FRESH_MAX, AGING_MAX, COMP_FLOOR, COMP_TARGET, COMP_FLOOR_DIR
    global APPLY_SCORE_CUTOFF, APPLY_DELAY, PAGES, NAUKRI_PAGES, RESULTS_PER_PAGE, RESULTS_TOP_N, LOOKUP_CHUNK
    global TRACKS, PORTAL_DISPLAY, ENABLED_PORTALS, RUBRICS, SCORING_KEYWORDS, COMPANY_TIERS
    global LOCATION_PREFS, LOCATION_MAPS, THRESHOLDS, SCORING_RUBRICS, PORTALS

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return False

    try:
        from supabase import create_client
        sb = create_client(url, key)
        uid = user_id or os.environ.get("USER_EMAIL", "")
        if not uid:
            return False

        resp = sb.table("settings").select("data").eq("user_id", uid).execute()
        if not resp.data:
            return False

        cloud_data = resp.data[0].get("data")
        if not isinstance(cloud_data, dict):
            return False

        _file_data = cloud_data

        SEARCHES          = cloud_data.get("searches", DEFAULT_SEARCHES)
        SCORING_KEYWORDS  = cloud_data.get("scoring_keywords", DEFAULT_SCORING_KEYWORDS)
        COMPANY_TIERS     = cloud_data.get("company_tiers", DEFAULT_COMPANY_TIERS)
        LOCATION_PREFS    = cloud_data.get("location_prefs", DEFAULT_LOCATION_PREFS)
        LOCATION_MAPS     = cloud_data.get("location_maps", DEFAULT_LOCATION_MAPS)
        THRESHOLDS        = cloud_data.get("thresholds", DEFAULT_THRESHOLDS)
        SCORING_RUBRICS   = cloud_data.get("scoring_rubrics", DEFAULT_SCORING_RUBRICS)
        PORTALS           = cloud_data.get("portals", DEFAULT_PORTALS)

        sk = SCORING_KEYWORDS
        SAFE_KEYWORDS    = sk.get("SAFE_KEYWORDS", DEFAULT_SCORING_KEYWORDS["SAFE_KEYWORDS"])
        BFSI_KEYWORDS    = sk.get("BFSI_KEYWORDS", DEFAULT_SCORING_KEYWORDS["BFSI_KEYWORDS"])
        GOVERNANCE_KW    = sk.get("GOVERNANCE_KW", DEFAULT_SCORING_KEYWORDS["GOVERNANCE_KW"])
        SCOPE_KW         = sk.get("SCOPE_KW", DEFAULT_SCORING_KEYWORDS["SCOPE_KW"])
        SENIOR_PM_KW     = sk.get("SENIOR_PM_KW", DEFAULT_SCORING_KEYWORDS["SENIOR_PM_KW"])
        NEGATIVE_KW      = sk.get("NEGATIVE_KW", DEFAULT_SCORING_KEYWORDS["NEGATIVE_KW"])

        ct = COMPANY_TIERS
        TIER1_BFSI       = ct.get("TIER1_BFSI", DEFAULT_COMPANY_TIERS["TIER1_BFSI"])
        GCC_FINTECH      = ct.get("GCC_FINTECH", DEFAULT_COMPANY_TIERS["GCC_FINTECH"])
        IT_SERVICES      = ct.get("IT_SERVICES", DEFAULT_COMPANY_TIERS["IT_SERVICES"])

        lp = LOCATION_PREFS
        GOOD_LOCS_PRIMARY     = lp.get("GOOD_LOCS_PRIMARY", DEFAULT_LOCATION_PREFS["GOOD_LOCS_PRIMARY"])
        RELOCATABLE_METROS    = lp.get("RELOCATABLE_METROS", DEFAULT_LOCATION_PREFS["RELOCATABLE_METROS"])
        LOCATION_SCORE_GOOD   = lp.get("LOCATION_SCORE_GOOD", DEFAULT_LOCATION_PREFS["LOCATION_SCORE_GOOD"])
        LOCATION_SCORE_RELOCATE = lp.get("LOCATION_SCORE_RELOCATE", DEFAULT_LOCATION_PREFS["LOCATION_SCORE_RELOCATE"])

        lm = LOCATION_MAPS
        NAUKRI_LOC_MAP  = lm.get("NAUKRI_LOC_MAP", DEFAULT_LOCATION_MAPS["NAUKRI_LOC_MAP"])
        LI_LOCATION_MAP = lm.get("LI_LOCATION_MAP", DEFAULT_LOCATION_MAPS["LI_LOCATION_MAP"])
        IIMJOBS_LOC_MAP = lm.get("IIMJOBS_LOC_MAP", DEFAULT_LOCATION_MAPS["IIMJOBS_LOC_MAP"])

        th = THRESHOLDS
        FRESH_MAX       = th.get("FRESH_MAX", DEFAULT_THRESHOLDS["FRESH_MAX"])
        AGING_MAX       = th.get("AGING_MAX", DEFAULT_THRESHOLDS["AGING_MAX"])
        COMP_FLOOR      = th.get("COMP_FLOOR", DEFAULT_THRESHOLDS["COMP_FLOOR"])
        COMP_TARGET     = th.get("COMP_TARGET", DEFAULT_THRESHOLDS["COMP_TARGET"])
        COMP_FLOOR_DIR  = th.get("COMP_FLOOR_DIR", DEFAULT_THRESHOLDS["COMP_FLOOR_DIR"])
        APPLY_SCORE_CUTOFF = th.get("APPLY_SCORE_CUTOFF", DEFAULT_THRESHOLDS["APPLY_SCORE_CUTOFF"])
        APPLY_DELAY     = th.get("APPLY_DELAY", DEFAULT_THRESHOLDS["APPLY_DELAY"])
        PAGES           = th.get("PAGES", DEFAULT_THRESHOLDS["PAGES"])
        NAUKRI_PAGES    = th.get("NAUKRI_PAGES", DEFAULT_THRESHOLDS["NAUKRI_PAGES"])
        RESULTS_PER_PAGE = th.get("RESULTS_PER_PAGE", DEFAULT_THRESHOLDS["RESULTS_PER_PAGE"])
        RESULTS_TOP_N   = th.get("RESULTS_TOP_N", DEFAULT_THRESHOLDS["RESULTS_TOP_N"])
        LOOKUP_CHUNK    = th.get("LOOKUP_CHUNK", DEFAULT_THRESHOLDS["LOOKUP_CHUNK"])

        pr = PORTALS
        PORTAL_DISPLAY  = pr.get("display_names", DEFAULT_PORTALS["display_names"])
        ENABLED_PORTALS = pr.get("enabled", DEFAULT_PORTALS["enabled"])

        TRACKS = cloud_data.get("tracks", DEFAULT_TRACKS)

        RUBRICS = SCORING_RUBRICS
        return True
    except Exception:
        return False


def save_to_file():
    _SETTINGS_PATH.write_text(json.dumps(_file_data, indent=2), encoding="utf-8")


def get_all():
    return _file_data if _file_data else _DEFAULTS


def get_all_defaults():
    return dict(_DEFAULTS)
