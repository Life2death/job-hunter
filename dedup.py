"""
dedup.py
URL-based identity for jobs — stable canonical URLs to recognize same postings across runs.
No cross-portal dedup: different portals → different URLs → different rows.
"""

from urllib.parse import urlparse, urlunparse


PORTAL_PREFIXES = ("nk_", "li_", "adz_", "fi_", "iim_")


def canonical_url(url: str) -> str:
    """Normalize a job URL to a stable canonical form.

    - lowercase host, strip www. prefix
    - keep scheme (http → https)
    - remove query string, fragment
    - strip trailing slash from path
    - return empty string for falsy input
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        host = parsed.hostname or ""
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme or "https", host, path, "", "", ""))
    except Exception:
        return ""


def normalize_job_key(job: dict) -> str:
    """Fallback identity when URL is empty.

    Strips portal prefix (nk_, li_, adz_, fi_, iim_) and trailing _<page>
    from job_id so bare and prefixed variants produce the same key.
    """
    jid = (job.get("job_id") or "").strip()
    if not jid:
        return ""
    lowered = jid.lower()
    for prefix in PORTAL_PREFIXES:
        if lowered.startswith(prefix):
            jid = jid[len(prefix):]
            break
    if "_" in jid:
        parts = jid.rsplit("_", 1)
        if parts[1].isdigit():
            jid = parts[0]
    return jid


def job_identity(job: dict) -> str:
    """Primary stable identity for a job row.

    Uses canonical_url if available; falls back to normalize_job_key.
    """
    url = job.get("url") or ""
    c = canonical_url(url)
    if c:
        return c
    return normalize_job_key(job)
