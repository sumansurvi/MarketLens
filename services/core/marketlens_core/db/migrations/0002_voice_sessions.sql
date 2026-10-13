-- Phase 4: voice sessions. A row binds a LiveKit room to the browser session that asked for it,
-- so research started by voice belongs to that session (the room name is the worker's handle).
CREATE TABLE voice_sessions (
    room_name            TEXT PRIMARY KEY,
    session_hash         TEXT NOT NULL,
    participant_identity TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    active_run_id        TEXT REFERENCES research_runs(run_id),
    ended_at             TEXT
);

CREATE INDEX idx_voice_sessions_session ON voice_sessions(session_hash, created_at);
