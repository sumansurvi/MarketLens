-- Phase 5: local product analytics. Event names and property keys are allowlisted in code
-- (marketlens_core.analytics); values are numbers, booleans or short codes. No identifiers of
-- people or sessions, no free text, no secrets, no audio or transcripts.
CREATE TABLE analytics_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at     TEXT NOT NULL,
    event_name      TEXT NOT NULL,
    properties_json TEXT NOT NULL
);

CREATE INDEX idx_analytics_events_name ON analytics_events(event_name, occurred_at);
