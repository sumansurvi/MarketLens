"""SQLite connections shared by the backend and the workspace MCP server.

Each operation opens a short-lived connection (foreign keys on, busy timeout, WAL) so
several processes can use one database file safely. Writes use ``BEGIN IMMEDIATE`` so a
check-then-write sequence (e.g. grant validation + persistence) is atomic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from marketlens_core.errors import ErrorCode, MarketLensError


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise MarketLensError(
                ErrorCode.PERSISTENCE_ERROR,
                "The local database could not complete the operation.",
                details={"sqlite_error": type(exc).__name__},
            ) from exc
        finally:
            conn.close()
