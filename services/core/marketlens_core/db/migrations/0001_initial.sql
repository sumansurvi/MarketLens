-- MarketLens schema v1. Applied once by marketlens_core.db.migrate; never dropped/recreated.
-- Timestamps are ISO-8601 UTC strings. Secrets (session IDs, grant IDs) are stored only as hashes.

CREATE TABLE research_runs (
    run_id              TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    question            TEXT NOT NULL,
    company_json        TEXT,
    time_horizon_json   TEXT NOT NULL,
    focus_areas_json    TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL CHECK (status IN ('queued','running','partial','completed','failed','cancelled')),
    stage               TEXT NOT NULL,
    data_mode           TEXT NOT NULL CHECK (data_mode IN ('fixture','live','mixed')),
    llm_mode            TEXT NOT NULL,
    origin              TEXT NOT NULL DEFAULT 'text',
    parent_run_id       TEXT,
    session_hash        TEXT,
    client_request_id   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    is_partial          INTEGER NOT NULL DEFAULT 0,
    errors_json         TEXT NOT NULL DEFAULT '[]',
    cancel_requested    INTEGER NOT NULL DEFAULT 0,
    duration_ms         INTEGER
);
CREATE INDEX idx_runs_symbol ON research_runs(symbol);
CREATE INDEX idx_runs_created ON research_runs(created_at);
CREATE UNIQUE INDEX idx_runs_client_request ON research_runs(session_hash, client_request_id)
    WHERE client_request_id IS NOT NULL;

CREATE TABLE research_steps (
    run_id      TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    step_key    TEXT NOT NULL,
    agent       TEXT NOT NULL,
    status      TEXT NOT NULL,
    summary     TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    started_at  TEXT,
    ended_at    TEXT,
    latency_ms  INTEGER,
    error_code  TEXT,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE tool_calls (
    call_id             TEXT PRIMARY KEY,
    run_id              TEXT REFERENCES research_runs(run_id) ON DELETE CASCADE,
    seq                 INTEGER,
    server_id           TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    transport           TEXT NOT NULL,
    input_summary_json  TEXT NOT NULL DEFAULT '{}',
    output_summary_json TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL CHECK (status IN ('ok','error')),
    error_code          TEXT,
    latency_ms          INTEGER,
    idempotency_key     TEXT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT
);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id);

CREATE TABLE evidence_items (
    run_id          TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    evidence_id     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    symbol          TEXT,
    source_name     TEXT NOT NULL,
    source_url      TEXT,
    published_date  TEXT,
    period_start    TEXT,
    period_end      TEXT,
    retrieved_at    TEXT NOT NULL,
    source_mode     TEXT NOT NULL,
    is_fixture      INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    item_json       TEXT NOT NULL,
    PRIMARY KEY (run_id, evidence_id)
);
CREATE INDEX idx_evidence_symbol ON evidence_items(symbol);

CREATE TABLE research_claims (
    run_id                TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id              TEXT NOT NULL,
    section               TEXT NOT NULL,
    claim_type            TEXT NOT NULL,
    text                  TEXT NOT NULL,
    materiality           TEXT NOT NULL,
    evidence_ids_json     TEXT NOT NULL,
    validation_status     TEXT NOT NULL,
    validation_notes_json TEXT NOT NULL DEFAULT '[]',
    withheld              INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, claim_id)
);

CREATE TABLE research_briefs (
    run_id        TEXT PRIMARY KEY REFERENCES research_runs(run_id) ON DELETE CASCADE,
    brief_json    TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    report_status TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE saved_briefs (
    brief_id      TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES research_runs(run_id),
    title         TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    report_status TEXT NOT NULL,
    data_mode     TEXT NOT NULL,
    is_fixture    INTEGER NOT NULL,
    payload_hash  TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    action_id     TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_saved_briefs_symbol ON saved_briefs(symbol);
CREATE INDEX idx_saved_briefs_created ON saved_briefs(created_at);

CREATE TABLE watchlist_items (
    symbol          TEXT PRIMARY KEY,
    display_name    TEXT,
    origin          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_action_id  TEXT
);

CREATE TABLE pending_actions (
    action_id     TEXT PRIMARY KEY,
    action_type   TEXT NOT NULL CHECK (action_type IN ('save_research_brief','add_watchlist_symbol','remove_watchlist_symbol')),
    run_id        TEXT,
    symbol        TEXT,
    title         TEXT,
    session_hash  TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    summary_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL CHECK (status IN ('pending','confirmed','consumed','expired','cancelled','failed')),
    origin        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    confirmed_at  TEXT,
    consumed_at   TEXT,
    result_json   TEXT
);
CREATE INDEX idx_pending_actions_session ON pending_actions(session_hash, status);

CREATE TABLE confirmation_grants (
    grant_hash    TEXT PRIMARY KEY,
    action_id     TEXT NOT NULL UNIQUE REFERENCES pending_actions(action_id),
    session_hash  TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    issued_at     TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    consumed_at   TEXT
);

CREATE TABLE audit_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at   TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    run_id        TEXT,
    action_id     TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_audit_occurred ON audit_events(occurred_at);

CREATE TABLE provider_cache (
    cache_key    TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
