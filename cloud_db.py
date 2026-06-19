"""
cloud_db.py
Supabase-backed cloud database for multi_portal_job_hunter.
Mirrors MultiPortalDB interface but talks to Supabase PostgreSQL.
"""

import os, json
from datetime import date
from typing import Optional
from dedup import canonical_url, normalize_job_key
from settings import LOOKUP_CHUNK

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_AVAILABLE = bool(SUPABASE_URL and SUPABASE_KEY)

TODAY = date.today()

# Columns the scraper may refresh on existing rows. User-owned columns
# (status, applied_date, imported_date) must never appear here.
REFRESH_COLS = ("canon_url", "track", "portal", "title", "company",
                "location", "salary", "posted", "url", "freshness")


def _chunked(seq: list, size: int = LOOKUP_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


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

        # Find existing rows by canon_url (preferred) or job_id (fallback).
        # Lookups are chunked to stay under request-size limits and fail closed:
        # a failed lookup aborts the whole save, because misclassifying existing
        # jobs as new is what resets their applied status. A skipped save is
        # recoverable on the next run; a status wipe is not.
        existing_by_url: dict[str, dict] = {}
        existing_by_jid: set[str] = set()
        non_empty_urls = [r["canon_url"] for r in rows if r["canon_url"]]
        all_ids = [r["job_id"] for r in rows]
        try:
            for chunk in _chunked(non_empty_urls):
                existing_resp = self._table().select("job_id, canon_url, status, imported_date, applied_date, fit, scores_json") \
                    .eq("user_id", self.user_id) \
                    .in_("canon_url", chunk) \
                    .execute()
                for r in existing_resp.data or []:
                    existing_by_url[r["canon_url"]] = r
            for chunk in _chunked(all_ids):
                existing_resp2 = self._table().select("job_id") \
                    .eq("user_id", self.user_id) \
                    .in_("job_id", chunk) \
                    .execute()
                existing_by_jid.update(r["job_id"] for r in existing_resp2.data or [])
        except Exception as e:
            print(f"[cloud] existence lookup failed, aborting save to protect statuses: {e}")
            return 0

        today_str = str(TODAY)
        new_rows: list[dict] = []
        update_rows: list[tuple[str, dict]] = []
        for row in rows:
            existing = existing_by_url.get(row["canon_url"]) if row["canon_url"] else None
            if existing:
                # Refresh only scraper-owned columns; status/applied_date/
                # imported_date stay untouched.
                upd = {c: row[c] for c in REFRESH_COLS}
                upd["last_seen_date"] = today_str
                if row["fit"] >= (existing.get("fit") or 0):
                    upd["fit"] = row["fit"]
                    upd["scores_json"] = row.get("scores_json", "")
                update_rows.append((row["job_id"], upd))
            elif row["job_id"] in existing_by_jid:
                upd = {c: row[c] for c in REFRESH_COLS}
                upd["last_seen_date"] = today_str
                upd["fit"] = row["fit"]
                upd["scores_json"] = row.get("scores_json", "")
                update_rows.append((row["job_id"], upd))
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
                self._table().insert(row).execute()
                count += 1
            except Exception as e:
                # Conflict means the row already exists despite the lookup
                # (race or missed match). Never fall back to update/upsert
                # here — that would overwrite the user-set status.
                print(f"[cloud] insert conflict for {row['job_id']}, skipped: {e}")
        for job_id, upd in update_rows:
            try:
                self._table().update(upd) \
                    .eq("user_id", self.user_id) \
                    .eq("job_id", job_id) \
                    .execute()
                count += 1
            except Exception as e:
                print(f"[cloud] update failed for {job_id}: {e}")
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
        elif new_status == "not_applied":
            # Clearing applied_date marks this as a deliberate revert so the
            # protect_user_status trigger lets it through.
            data["applied_date"] = None
        result = self._table().update(data).eq("job_id", job_id).execute()
        return len(result.data) > 0

    def count_by_status(self, track: str = None, portal: str = None) -> list:
        query = self._table().select("track, portal, status")
        if track:
            query = query.eq("track", track)
        if portal:
            query = query.eq("portal", portal)
        rows = query.execute().data or []
        counts: dict = {}
        for r in rows:
            key = (r["track"], r["portal"], r["status"])
            counts[key] = counts.get(key, 0) + 1
        return [(t, p, s, c) for (t, p, s), c in sorted(counts.items())]

    def clear_all(self):
        """Delete ALL job_listings for this user from Supabase.
        Use with extreme care — this is irreversible."""
        try:
            resp = self._table().delete().eq("user_id", self.user_id).execute()
            deleted = resp.data if resp.data else []
            print(f"[cloud] Cleared {len(deleted)} rows for user {self.user_id}")
            return len(deleted)
        except Exception as e:
            print(f"[cloud] Clear failed: {e}")
            return 0

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
