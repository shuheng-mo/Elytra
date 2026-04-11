-- Migration 002: Observability + Self-Evolution + Multi-turn (v0.5.0)
-- Safe to run on existing databases.
--
-- Part 1: query_history.error_type — canonical ErrorType enum value
--         (populated by self_correction node via classify_error)
-- Part 2: experience_pool + query_feedback — empty shells, the embedding
--         column is added at runtime by Embedder.bootstrap_experience_tables()
--         because the vector dim depends on the active EMBEDDING_MODEL
-- Part 3: conversation_summary — same pattern, embedding column deferred
--         (currently only text summary is stored, embedding for cross-session
--         recall is a future extension)

-- ============================================================
-- Part 1: query_history.error_type
-- ============================================================

ALTER TABLE query_history ADD COLUMN IF NOT EXISTS error_type VARCHAR(40);

CREATE INDEX IF NOT EXISTS query_history_error_type_idx
    ON query_history (error_type)
    WHERE error_type IS NOT NULL;

-- ============================================================
-- Part 2: Self-Evolution tables
-- ============================================================

CREATE TABLE IF NOT EXISTS experience_pool (
    id              BIGSERIAL PRIMARY KEY,
    user_query      TEXT NOT NULL,
    intent          VARCHAR(30),
    source_name     VARCHAR(50),
    failed_sql      TEXT NOT NULL,
    error_message   TEXT NOT NULL,
    error_type      VARCHAR(40),
    corrected_sql   TEXT NOT NULL,
    model_used      VARCHAR(50),
    retry_count     INT,
    created_at      TIMESTAMP DEFAULT NOW(),
    times_retrieved INT DEFAULT 0,
    times_helpful   INT DEFAULT 0
    -- embedding column added by Embedder.bootstrap_experience_tables() at startup
    -- with dim matching EMBEDDING_MODEL
);

CREATE INDEX IF NOT EXISTS experience_pool_source_idx
    ON experience_pool (source_name);
CREATE INDEX IF NOT EXISTS experience_pool_error_type_idx
    ON experience_pool (error_type);

CREATE TABLE IF NOT EXISTS query_feedback (
    id              BIGSERIAL PRIMARY KEY,
    history_id      BIGINT NOT NULL REFERENCES query_history(id) ON DELETE CASCADE,
    feedback_type   VARCHAR(10) NOT NULL,   -- 'positive' | 'negative'
    feedback_detail TEXT,
    user_query      TEXT NOT NULL,
    generated_sql   TEXT NOT NULL,
    source_name     VARCHAR(50),
    intent          VARCHAR(30),
    created_at      TIMESTAMP DEFAULT NOW()
    -- embedding column added by Embedder.bootstrap_experience_tables() at startup
);

CREATE INDEX IF NOT EXISTS query_feedback_source_idx
    ON query_feedback (source_name);
CREATE INDEX IF NOT EXISTS query_feedback_type_idx
    ON query_feedback (feedback_type);

-- ============================================================
-- Part 3: Multi-turn conversation summaries
-- ============================================================

CREATE TABLE IF NOT EXISTS conversation_summary (
    id           BIGSERIAL PRIMARY KEY,
    session_id   VARCHAR(50) NOT NULL UNIQUE,
    summary      TEXT NOT NULL,
    turn_count   INT NOT NULL,
    last_updated TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS conversation_summary_session_idx
    ON conversation_summary (session_id);
