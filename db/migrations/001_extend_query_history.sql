-- Migration 001: Extend query_history with audit fields (Phase 2+)
-- Safe to run on existing databases — all columns are nullable.

ALTER TABLE query_history ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS user_role VARCHAR(30);
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS source_name VARCHAR(100);
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS retrieved_tables TEXT;
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS correction_history_json JSONB;
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS result_row_count INT;
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS result_hash VARCHAR(64);
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS token_input INT;
ALTER TABLE query_history ADD COLUMN IF NOT EXISTS token_output INT;
