"""
cloud_db.py
Supabase-backed cloud database for multi_portal_job_hunter.
Mirrors MultiPortalDB interface but talks to Supabase PostgreSQL.
"""

import os, json
from datetime import date
from typing import Optional
from dedup import canonical_url, normalize_job_key

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_AVAILABLE = bool(SUPABASE_URL and SUPABASE_KEY)

TODAY = date.today()


class CloudDB:
    def __init__(self, user_id: str = ""):
        if not _AVAILABLE:
            raise RuntimeError(
                "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY env vars."
            )
        self.user_id = user_id or os.environ.get("USER_EMAIL", "")
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
            cu = canonical_url(r.get("url", ""))
            rows.append({
                "job_id": r["job_id"],
                "user_id": self.user_id,
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

        # Find existing rows by canon_url (preferred) or job_id (fallback)
        existing_by_url: dict[str, dict] = {}
        existing_by_jid: set[str] = set()
        non_empty_urls = [r["canon_url"] for r in rows if r["canon_url"]]
        all_ids = [r["job_id"] for r in rows]
        if non_empty_urls:
            try:
                existing_resp = self._table().select("job_id, canon_url, status, imported_date, applied_date, fit, scores_json") \
                    .eq("user_id", self.user_id) \
                    .in_("canon_url", non_empty_urls) \
                    .execute()
                if existing_resp.data:
                    for r in existing_resp.data:
                        existing_by_url[r["canon_url"]] = r
            except Exception:
                pass
        if all_ids:
            try:
                existing_resp2 = self._table().select("job_id").in_("job_id", all_ids).execute()
                if existing_resp2.data:
                    existing_by_jid = {r["job_id"] for r in existing_resp2.data}
            except Exception:
                pass

        today_str = str(TODAY)
        new_rows: list[dict] = []
        update_rows: list[dict] = []
        for row in rows:
            existing = existing_by_url.get(row["canon_url"]) if row["canon_url"] else None
            if existing:
                upd = {
                    **row,
                    "last_seen_date": today_str,
                }
                if row["fit"] >= (existing.get("fit") or 0):
                    upd["fit"] = row["fit"]
                    upd["scores_json"] = row.get("scores_json", "")
                update_rows.append(upd)
            elif row["job_id"] in existing_by_jid:
                update_rows.append({
                    **row,
                    "last_seen_date": today_str,
                })
            else:
                new_rows.append({
                    **row,
                    "status": "not_applied",
                    "imported_date": today_str,
                    "last_seen_date": today_str,
                })

        count = 0
        for row in new_rows:
            try:
                self._table().upsert(row, on_conflict="job_id").execute()
                count += 1
            except Exception:
                try:
                    self._table().update(row).eq("job_id", row["job_id"]).execute()
                    count += 1
                except Exception:
                    pass
        for row in update_rows:
            try:
                self._table().update(row).eq("job_id", row["job_id"]).execute()
                count += 1
            except Exception:
                pass
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
                     fit, freshness, scores_json, status, imported_date, applied_date, last_seen_date, canon_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        j.get("canon_url", ""),
                    )
                )
                count += 1
            except Exception:
                pass
        local_db.conn.commit()
        return count


def is_available() -> bool:
    return _AVAILABLE
