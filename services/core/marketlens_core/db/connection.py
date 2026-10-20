"""Database connections shared by the backend and the MCP servers: SQLite or Postgres.

SQLite (local, the default): each operation opens a short-lived connection (foreign keys on,
busy timeout, WAL) so several processes can use one database file safely. Writes use
``BEGIN IMMEDIATE`` so a check-then-write sequence (e.g. grant validation + persistence) is atomic.

Postgres (hosted on Vercel, D-98): ``DATABASE_URL=postgresql://…``. Connections come from a small
per-process pool. Writes run in a transaction that first takes one transaction-scoped advisory
lock, so writes are serialized exactly as ``BEGIN IMMEDIATE`` serializes them in SQLite and every
check-then-write keeps its guarantee. Repositories keep writing ``?`` placeholders; the Postgres
connection translates them. The two dialect differences repositories need are exposed as
``Database.like`` (case-insensitive LIKE) and ``Database.json_text`` (one JSON field as text).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Protocol

from marketlens_core.errors import ErrorCode, MarketLensError

# Arbitrary constant: the key of the advisory lock that serializes writes (Postgres only).
WRITE_LOCK_KEY = 726_311_001


class Connection(Protocol):
    def execute(self, sql: str, params: Sequence[Any] = ...) -> Any: ...
    def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> Any: ...


class Database:
    """SQLite database at ``path`` (the local default)."""

    dialect: Literal["sqlite", "postgres"] = "sqlite"
    like = "LIKE"  # SQLite LIKE is case-insensitive for ASCII

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def json_text(column: str, key: str) -> str:
        return f"json_extract({column}, '$.{key}')"

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

    def close(self) -> None:
        """Nothing to release: SQLite connections are opened per operation."""


def _postgres_sql(sql: str) -> str:
    """Repositories write ``?`` placeholders (SQLite style); psycopg expects ``%s``.

    Literal ``%`` is escaped first. No statement in this codebase has a ``?`` or ``%`` inside
    a string literal (LIKE patterns are always bound parameters).
    """
    return sql.replace("%", "%%").replace("?", "%s")


class _PostgresConnection:
    """Wraps a psycopg connection with the sqlite3-style calls the repositories use."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._conn.cursor()
        cursor.execute(_postgres_sql(sql), tuple(params))
        return cursor

    def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> Any:
        cursor = self._conn.cursor()
        cursor.executemany(_postgres_sql(sql), [tuple(p) for p in params])
        return cursor


class PostgresDatabase:
    """Postgres database at ``url`` (hosted deployments; Neon from the Vercel Marketplace)."""

    dialect: Literal["sqlite", "postgres"] = "postgres"
    like = "ILIKE"

    def __init__(self, url: str, *, max_connections: int = 4) -> None:
        self.url = url
        self._max = max_connections
        self._pool: Any = None
        self._lock = threading.Lock()

    @staticmethod
    def json_text(column: str, key: str) -> str:
        return f"(({column})::jsonb ->> '{key}')"

    def _get_pool(self) -> Any:
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    from psycopg.rows import dict_row
                    from psycopg_pool import ConnectionPool

                    # prepare_threshold=None: no server-side prepared statements, so a
                    # transaction-mode pooler (Neon's pooled URL) works. check: a connection
                    # left idle while a serverless instance was frozen is replaced on checkout.
                    self._pool = ConnectionPool(
                        self.url,
                        min_size=0,
                        max_size=self._max,
                        max_idle=60,
                        timeout=15,
                        check=ConnectionPool.check_connection,
                        kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
                        open=True,
                    )
        return self._pool

    @contextmanager
    def read(self) -> Iterator[_PostgresConnection]:
        import psycopg

        try:
            with self._get_pool().connection() as conn:
                yield _PostgresConnection(conn)
        except psycopg.Error as exc:
            raise _persistence_error(exc) from exc

    @contextmanager
    def write(self) -> Iterator[_PostgresConnection]:
        import psycopg

        try:
            with self._get_pool().connection() as conn, conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,))
                yield _PostgresConnection(conn)
        except psycopg.Error as exc:
            raise _persistence_error(exc) from exc

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None


def _persistence_error(exc: Exception) -> MarketLensError:
    return MarketLensError(
        ErrorCode.PERSISTENCE_ERROR,
        "The database could not complete the operation.",
        details={"database_error": type(exc).__name__},
    )


AnyDatabase = Database | PostgresDatabase


def is_postgres_url(url: str) -> bool:
    return url.startswith(("postgresql://", "postgres://"))


def open_database(settings: Any) -> AnyDatabase:
    """The database ``settings.database_url`` names: Postgres for ``postgresql://``, else SQLite."""
    if is_postgres_url(settings.database_url):
        return PostgresDatabase(settings.database_url)
    return Database(settings.database_path)
