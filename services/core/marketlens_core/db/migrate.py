"""Versioned, forward-only schema migrations (no destructive re-initialization).

Migrations are ``NNNN_name.sql`` files applied in order and recorded in
``schema_migrations``. Applying is idempotent; a database newer than the code is
reported rather than modified.
"""

from __future__ import annotations

import re
from importlib import resources

from marketlens_core.db.connection import Database
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.timeutil import iso_z, utc_now

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def available_migrations() -> list[tuple[int, str, str]]:
    folder = resources.files("marketlens_core.db") / "migrations"
    found = []
    for entry in folder.iterdir():
        match = _NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), match.group(2), entry.read_text(encoding="utf-8")))
    return sorted(found)


def initialize_database(db: Database) -> list[int]:
    """Apply pending migrations; returns the versions applied in this call."""
    applied_now: list[int] = []
    conn = db.connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        migrations = available_migrations()
        known = {version for version, _, _ in migrations}
        unknown = sorted(done - known)
        if unknown:
            raise MarketLensError(
                ErrorCode.PERSISTENCE_ERROR,
                f"The database has schema versions {unknown} that this code does not know. "
                "Use a newer MarketLens build or restore a backup; the database was not modified.",
            )
        for version, name, sql in migrations:
            if version in done:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Re-check under the write lock: another process (e.g. a sibling MCP server
                # starting at the same moment) may have applied this version already.
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
                    conn.execute("COMMIT")
                    continue
                for statement in _split_statements(sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, iso_z(utc_now())),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            applied_now.append(version)
    finally:
        conn.close()
    return applied_now


def _split_statements(sql: str) -> list[str]:
    """Split a migration file on semicolons (migrations contain no triggers/strings with ';')."""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]
