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
