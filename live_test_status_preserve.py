"""
live_test_status_preserve.py
One-shot live check against the real Supabase DB: simulates tomorrow's
re-scrape of an already-applied job and verifies the status survives.

Usage:  python live_test_status_preserve.py [job_id]
Default job: nk_190526028484 (applied 2026-06-10).

Only scraper-owned columns are written — exactly what the daily run does.
"""

import sys
from cloud_db import CloudDB, is_available
from dedup import canonical_url

JOB_ID = sys.argv[1] if len(sys.argv) > 1 else "nk_190526028484"


def fetch(db, job_id):
    resp = db._table().select("*") \
        .eq("user_id", db.user_id).eq("job_id", job_id).execute()
    return resp.data[0] if resp.data else None


def main():
    if not is_available():
        print("SUPABASE_URL / SUPABASE_KEY not set in this environment — "
              "run from a shell where the job hunter normally runs.")
        return 1

    db = CloudDB()
    print(f"user_id: {db.user_id}")

    before = fetch(db, JOB_ID)
    if not before:
        print(f"Job {JOB_ID} not found for this user — nothing to test.")
        return 1
    print(f"BEFORE: status={before['status']!r} applied={before['applied_date']!r} "
          f"last_seen={before['last_seen_date']!r}")

    # Simulate the daily scraper finding this job again. Echo the stored
    # values back so the only real change is last_seen_date.
    simulated = {k: before.get(k, "") for k in
                 ("job_id", "track", "portal", "title", "company", "location",
                  "salary", "posted", "url", "freshness", "scores_json")}
    simulated["fit"] = before.get("fit", 0)
    saved = db.save_results([simulated])
    print(f"save_results processed {saved} row(s)")

    after = fetch(db, JOB_ID)
    print(f"AFTER:  status={after['status']!r} applied={after['applied_date']!r} "
          f"last_seen={after['last_seen_date']!r}")

    ok = (after["status"] == before["status"]
          and after["applied_date"] == before["applied_date"]
          and after["imported_date"] == before["imported_date"])
    print("PASS — status preserved across re-scrape" if ok
          else "FAIL — user-set fields changed!")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
