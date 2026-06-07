"""
cloud_db.py
Supabase-backed cloud database for multi_portal_job_hunter.
Mirrors MultiPortalDB interface but talks to Supabase PostgreSQL.
"""

import os, json
from datetime import date
from typing import Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_AVAILABLE = bool(SUPABASE_URL and SUPABASE_KEY)

TODAY = date.today()


class CloudDB:
    def __init__(self):
        if not _AVAILABLE:
            raise RuntimeError(
                "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY env vars."
            )
        try:
            from supabase import create_client
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
        except ImportError:
            raise RuntimeError("Missing dependency: pip install supabase")

    def _table(self):
        return self.client.table("job_listings")

    def save_results(self, results: list) -> int:
        rows = []
        for r in results:
            rows.append({
                "job_id": r["job_id"],
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
                "status": "not_applied",
                "imported_date": str(TODAY),
                "last_seen_date": str(TODAY),
            })
        if not rows:
            return 0
        count = 0
        for row in rows:
            data = self._table().upsert(row, on_conflict="job_id").execute()
            count += 1
        return count

    def search(self, track: str = None, portal: str = None,
               min_fit: int = 0) -> list:
        query = self._table().select("*").eq("status", "not_applied")
        if track:
            query = query.eq("track", track)
        if portal:
            query = query.eq("portal", portal)
        query = query.gte("fit", min_fit).order("fit", desc=True)
        return query.execute().data

    def all_jobs(self, track: str = None, portal: str = None,
                 min_fit: int = 0, status: str = None) -> list:
        query = self._table().select("*")
        if track:
            query = query.eq("track", track)
        if portal:
            query = query.eq("portal", portal)
        if status:
            query = query.eq("status", status)
        query = query.gte("fit", min_fit).order("fit", desc=True)
        return query.execute().data

    def mark_applied(self, job_id: str) -> bool:
        data = {
            "status": "applied",
            "applied_date": str(TODAY),
        }
        result = self._table().update(data).eq("job_id", job_id).execute()
        return len(result.data) > 0

    def update_status(self, job_id: str, new_status: str) -> bool:
        data = {"status": new_status}
        if new_status == "applied":
            data["applied_date"] = str(TODAY)
        result = self._table().update(data).eq("job_id", job_id).execute()
        return len(result.data) > 0

    def count_by_status(self, track: str = None, portal: str = None) -> list:
        query = self._table().select("track, portal, status, count")
        if track:
            query = query.eq("track", track)
        if portal:
            query = query.eq("portal", portal)
        result = query.execute().data
        return result

    def pull_to_local(self, local_db):
        """Sync cloud data into a local MultiPortalDB instance."""
        jobs = self.all_jobs()
        if not jobs:
            print("[cloud] No jobs to pull")
            return 0
        count = 0
        for j in jobs:
            try:
                local_db.conn.execute(
                    """INSERT OR REPLACE INTO job_listings
                    (job_id, track, portal, title, company, location, salary, posted, url,
                     fit, freshness, scores_json, status, imported_date, applied_date, last_seen_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        j.get("job_id", ""), j.get("track", ""), j.get("portal", ""),
                        j.get("title", ""), j.get("company", ""),
                        j.get("location", ""), j.get("salary", ""),
                        j.get("posted", ""), j.get("url", ""),
                        j.get("fit", 0), j.get("freshness", ""),
                        j.get("scores_json", ""), j.get("status", "not_applied"),
                        j.get("imported_date", str(TODAY)),
                        j.get("applied_date", ""),
                        j.get("last_seen_date", ""),
                    )
                )
                count += 1
            except Exception:
                pass
        local_db.conn.commit()
        return count


def is_available() -> bool:
    return _AVAILABLE
