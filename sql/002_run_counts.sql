-- Migration 002: record how many rows each run inserted and updated.
-- Safe to run more than once (IF NOT EXISTS).
ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS rows_inserted INTEGER CHECK (rows_inserted >= 0),
    ADD COLUMN IF NOT EXISTS rows_updated  INTEGER CHECK (rows_updated >= 0);
