-- Cross-process request pacing for providers with burst limits (Alpha Vantage free keys allow
-- about one request per second). The market-data and research-sources MCP servers are separate
-- processes that share this database, so the next allowed request time lives here (decision D-77).
-- Only a provider name and a timestamp are stored: no request data, keys or payloads.
CREATE TABLE provider_rate_limits (
    provider        TEXT PRIMARY KEY,
    next_allowed_at DOUBLE PRECISION NOT NULL
);
