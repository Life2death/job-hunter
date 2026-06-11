-- Run this in your Supabase SQL Editor (Dashboard > SQL Editor)
-- 1. Create approval table
CREATE TABLE IF NOT EXISTS profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  approved BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT now()
);

-- 2. Add user isolation column
ALTER TABLE job_listings ADD COLUMN IF NOT EXISTS user_id TEXT;

-- 3. Backfill your existing data
UPDATE job_listings SET user_id = lower('vikram.panmand@gmail.com') WHERE user_id IS NULL;

-- 4. Add canonical URL column for dedup
ALTER TABLE job_listings ADD COLUMN IF NOT EXISTS canon_url TEXT;
CREATE INDEX IF NOT EXISTS ix_job_canon_url ON job_listings(user_id, canon_url);

-- 5. Repair applied flags wiped by the daily extraction (one-off).
--    The buggy upsert reset status to 'not_applied' but never touched
--    applied_date, so the flags can be restored from it.
--    Preview the scope first:
--      SELECT count(*) FROM job_listings
--      WHERE applied_date IS NOT NULL AND applied_date <> '' AND status = 'not_applied';
UPDATE job_listings
SET status = 'applied'
WHERE applied_date IS NOT NULL AND applied_date <> '' AND status = 'not_applied';

-- 6. Column defaults so inserts never need to send user-owned columns
ALTER TABLE job_listings ALTER COLUMN status SET DEFAULT 'not_applied';

-- 7. Guard trigger: an automated writer must never regress an applied job
--    back to 'not_applied'. A deliberate revert from the web app clears
--    applied_date in the same update and passes through; a buggy write
--    leaves applied_date untouched and gets blocked.
CREATE OR REPLACE FUNCTION protect_user_status() RETURNS trigger AS $$
BEGIN
  IF NEW.status = 'not_applied'
     AND OLD.status IN ('applied', 'manual_apply')
     AND OLD.applied_date IS NOT NULL
     AND NEW.applied_date IS NOT DISTINCT FROM OLD.applied_date THEN
    NEW.status := OLD.status;
  END IF;
  -- imported_date is set once at first insert and must never change
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
