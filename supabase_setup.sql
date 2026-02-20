-- Run this once in the Supabase SQL Editor (supabase.com → your project → SQL Editor)
-- Creates the table and enables public read access for the dashboard

CREATE TABLE IF NOT EXISTS facility_status (
  id                        SERIAL PRIMARY KEY,
  last_updated              TIMESTAMPTZ NOT NULL,
  facility_name             TEXT NOT NULL,
  city                      TEXT NOT NULL,
  country                   TEXT NOT NULL,
  type                      TEXT NOT NULL,
  lat                       DECIMAL(9,4) NOT NULL,
  lng                       DECIMAL(9,4) NOT NULL,
  weather_score             INTEGER,
  weather_description       TEXT,
  unrest_score              INTEGER,
  unrest_description        TEXT,
  crime_score               INTEGER,
  crime_description         TEXT,
  geopolitical_score        INTEGER,
  geopolitical_description  TEXT,
  composite_score           DECIMAL(4,1),
  color                     TEXT,
  top_alert                 TEXT,
  recommended_action        TEXT
);

-- Ensures upsert works: one row per facility, overwritten each run
CREATE UNIQUE INDEX IF NOT EXISTS facility_status_name_idx ON facility_status (facility_name);

-- Enable Row Level Security
ALTER TABLE facility_status ENABLE ROW LEVEL SECURITY;

-- Allow the dashboard (public, anon key) to read all rows
CREATE POLICY "Allow public read"
  ON facility_status
  FOR SELECT
  USING (true);

-- Allow the Python script (service key) to insert and update
CREATE POLICY "Allow service write"
  ON facility_status
  FOR ALL
  USING (true)
  WITH CHECK (true);
