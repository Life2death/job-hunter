"""
repair_supabase.py
One-shot repair: restore wiped applied flags + add column default + create
protection trigger.  Run via GitHub Actions (has SUPABASE_URL/SUPABASE_KEY).
"""
import os
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ["SUPABASE_KEY"]
client = create_client(url, key)

# ---------------------------------------------------------------
# Section 5 — Repair wiped applied flags
# ---------------------------------------------------------------
resp = client.table("job_listings") \
    .update({"status": "applied"}) \
    .neq("applied_date", "") \
    .eq("status", "not_applied") \
    .execute()
print(f"[5] Repaired {len(resp.data)} rows (applied_date -> applied)")

# ---------------------------------------------------------------
# Section 6 — Column default (DDL via raw SQL endpoint)
# ---------------------------------------------------------------
try:
    r = client.table("job_listings").select("status", count="exact") \
        .eq("status", "not_applied").limit(1).execute()
    print("[6] Column default: run in Supabase SQL Editor:")
    print("    ALTER TABLE job_listings ALTER COLUMN status SET DEFAULT 'not_applied';")
except Exception as e:
    print(f"[6] Error: {e}")

# ---------------------------------------------------------------
# Section 7 — Trigger (DDL via raw SQL endpoint)
# ---------------------------------------------------------------
print()
print("[7] Trigger: run in Supabase SQL Editor:")
print("""
CREATE OR REPLACE FUNCTION protect_user_status() RETURNS trigger AS $$
BEGIN
  IF NEW.status = 'not_applied'
     AND OLD.status IN ('applied', 'manual_apply')
     AND OLD.applied_date IS NOT NULL
     AND NEW.applied_date IS NOT DISTINCT FROM OLD.applied_date THEN
    NEW.status := OLD.status;
  END IF;
  IF OLD.imported_date IS NOT NULL THEN
    NEW.imported_date := OLD.imported_date;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_protect_user_status ON job_listings;
CREATE TRIGGER trg_protect_user_status
  BEFORE UPDATE ON job_listings
  FOR EACH ROW
  EXECUTE FUNCTION protect_user_status();
""")
print("Paste the above in https://supabase.com/dashboard -> SQL Editor")
