"""Repository classes: all SQL lives here, behind typed methods.

Symbols and identifiers are always bound parameters — never interpolated into SQL.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from marketlens_core.db.connection import AnyDatabase
from marketlens_core.errors import ErrorCode
from marketlens_core.models import (
    AgentStep,
    CompanyIdentity,
    DomainModel,
    EvidenceItem,
    ResearchBrief,
    ResearchClaim,
    ResearchRun,
    RunError,
    RunStatus,
    SavedBrief,
    SavedBriefSummary,
    TimeHorizon,
    ToolCallRecord,
    WatchlistItem,
)
from marketlens_core.timeutil import iso_z, parse_iso_datetime, utc_now


def _ts(value: dt.datetime | None) -> str | None:
    return iso_z(value) if value else None


def _dt(value: str | None) -> dt.datetime | None:
    return parse_iso_datetime(value) if value else None


SearchField = Literal["ticker", "company", "question", "finding"]
_FIELD_RANK: dict[str, int] = {"ticker": 1, "company": 2, "question": 3, "finding": 4}
_SNIPPET_CHARS = 150


@dataclass(frozen=True)
class RunSearchHit:
    run: ResearchRun
    matched: SearchField
    snippet: str | None


def _search_terms(query: str) -> list[str]:
    """Lower-cased words of a search query: at most 6, each at most 40 characters, no repeats."""
    terms: list[str] = []
    for word in query.lower().split():
        word = word[:40]
        if word not in terms:
            terms.append(word)
    return terms[:6]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(text: str, term: str) -> str:
    """About 150 characters of ``text`` around the first occurrence of ``term``, cut at word boundaries."""
    at = text.lower().find(term)
    if len(text) <= _SNIPPET_CHARS or at < 0:
        return text if len(text) <= _SNIPPET_CHARS else text[:_SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
    start = max(0, at - 50)
    end = min(len(text), start + _SNIPPET_CHARS)
    start = max(0, end - _SNIPPET_CHARS)
    piece = text[start:end]
    if start > 0:
        piece = "…" + re.sub(r"^\S*\s", "", piece, count=1)
    if end < len(text):
        piece = re.sub(r"\s\S*$", "", piece, count=1) + "…"
    return piece


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RunRepository:
    def __init__(self, db: AnyDatabase) -> None:
        self.db = db

    # ------------------------------------------------------------------ runs
    def create_run(self, run: ResearchRun, *, session_hash: str | None, client_request_id: str | None) -> ResearchRun:
        """Insert a run; a repeated (session, client_request_id) returns the original run."""
        if client_request_id and session_hash:
            existing = self.find_by_client_request(session_hash, client_request_id)
            if existing:
                return existing
        with self.db.write() as conn:
            conn.execute(
                """INSERT INTO research_runs (run_id, symbol, question, company_json, time_horizon_json,
                focus_areas_json, status, stage, data_mode, llm_mode, origin, parent_run_id, session_hash,
                client_request_id, created_at, updated_at, started_at, completed_at, is_partial, errors_json,
                cancel_requested, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run.run_id,
                    run.symbol,
                    run.question,
                    run.company.model_dump_json() if run.company else None,
                    run.time_horizon.model_dump_json(),
                    _json([f.value for f in run.focus_areas]),
                    run.status.value,
                    run.stage.value,
                    run.data_mode.value,
                    run.llm_mode,
                    run.origin,
                    run.parent_run_id,
                    session_hash,
                    client_request_id,
                    _ts(run.created_at),
                    _ts(run.updated_at),
                    _ts(run.started_at),
                    _ts(run.completed_at),
                    int(run.is_partial),
                    _json([e.model_dump(mode="json") for e in run.errors]),
                    int(run.cancel_requested),
                    run.duration_ms,
                ),
            )
        return run

    def find_by_client_request(self, session_hash: str, client_request_id: str) -> ResearchRun | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE session_hash = ? AND client_request_id = ?",
                (session_hash, client_request_id),
            ).fetchone()
        return self._run(row) if row else None

    def get_run(self, run_id: str) -> ResearchRun | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def run_session_hash(self, run_id: str) -> str | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT session_hash FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
        return row["session_hash"] if row else None

    def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {
            "status": lambda v: RunStatus(v).value,
            "stage": lambda v: getattr(v, "value", v),
            "company": lambda v: v.model_dump_json() if v else None,
            "started_at": _ts,
            "completed_at": _ts,
            "is_partial": int,
            "errors": lambda v: _json([e.model_dump(mode="json") for e in v]),
            "cancel_requested": int,
            "duration_ms": lambda v: v,
            "data_mode": lambda v: getattr(v, "value", v),
        }
        columns = {"company": "company_json", "errors": "errors_json"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"field {key!r} cannot be updated")
            sets.append(f"{columns.get(key, key)} = ?")
            values.append(allowed[key](value))
        sets.append("updated_at = ?")
        values.append(iso_z(utc_now()))
        with self.db.write() as conn:
            conn.execute(f"UPDATE research_runs SET {', '.join(sets)} WHERE run_id = ?", (*values, run_id))  # noqa: S608 - column names are allowlisted above

    def list_runs(self, *, limit: int = 20, symbol: str | None = None, live_only: bool = False) -> list[ResearchRun]:
        """Newest first. ``live_only`` leaves out runs made from fixture (demo) data (D-104)."""
        query = "SELECT * FROM research_runs"
        conditions: list[str] = []
        params: list[Any] = []
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if live_only:
            conditions.append("data_mode = 'live'")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        with self.db.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._run(r) for r in rows]

    def search_runs(self, query: str, *, limit: int = 8, live_only: bool = False) -> list[RunSearchHit]:
        """Runs whose ticker, company name, question or kept findings contain every word of ``query``.

        Ranked by where the match is (exact ticker, ticker prefix, company name, question, findings),
        newest first within a rank. Statements set aside by the fact-checker are not searched. A
        finding snippet is returned for words the ticker, company and question don't contain.
        ``live_only`` leaves out runs made from fixture (demo) data (D-104).
        """
        terms = _search_terms(query)
        if not terms:
            return []
        conditions: list[str] = ["r.data_mode = 'live'"] if live_only else []
        params: list[Any] = []
        like, company = self.db.like, self.db.json_text("r.company_json", "company_name")
        for term in terms:
            conditions.append(
                f"(r.symbol {like} ? ESCAPE '\\' OR {company} {like} ? ESCAPE '\\' "  # noqa: S608 - dialect keywords from code; terms are bound
                f"OR r.question {like} ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM research_claims c WHERE c.run_id = r.run_id "
                f"AND c.withheld = 0 AND c.text {like} ? ESCAPE '\\'))"
            )
            params.extend([f"%{_escape_like(term)}%"] * 4)
        sql = f"SELECT r.* FROM research_runs r WHERE {' AND '.join(conditions)} ORDER BY r.created_at DESC LIMIT 200"  # noqa: S608 - fixed SQL fragments; terms are bound parameters
        with self.db.read() as conn:
            rows = conn.execute(sql, params).fetchall()
            ids = [r["run_id"] for r in rows]
            claims: dict[str, list[str]] = {}
            if ids:
                marks = ",".join("?" * len(ids))
                for c in conn.execute(
                    f"SELECT run_id, text FROM research_claims WHERE withheld = 0 AND run_id IN ({marks})",  # noqa: S608 - placeholders only
                    ids,
                ):
                    claims.setdefault(c["run_id"], []).append(c["text"])

        hits: list[tuple[int, RunSearchHit]] = []
        for row in rows:
            run = self._run(row)
            symbol = run.symbol.lower()
            company = ((run.company.company_name or "") if run.company else "").lower()
            question = run.question.lower()
            if symbol in terms:
                rank, matched = 0, "ticker"
            elif any(symbol.startswith(t) for t in terms):
                rank, matched = _FIELD_RANK["ticker"], "ticker"
            elif any(t in company for t in terms):
                rank, matched = _FIELD_RANK["company"], "company"
            elif any(t in question for t in terms):
                rank, matched = _FIELD_RANK["question"], "question"
            else:
                rank, matched = _FIELD_RANK["finding"], "finding"
            remaining = [t for t in terms if t not in symbol and t not in company and t not in question]
            snippet = None
            if remaining:
                texts = claims.get(run.run_id, [])
                best = max(texts, key=lambda text: sum(t in text.lower() for t in remaining), default=None)
                if best is not None:
                    first = next((t for t in remaining if t in best.lower()), remaining[0])
                    snippet = _snippet(best, first)
            hits.append((rank, RunSearchHit(run=run, matched=matched, snippet=snippet)))
        hits.sort(key=lambda item: item[0])  # stable: newest first within a rank
        return [hit for _, hit in hits[: max(1, min(limit, 20))]]

    def latest_run_times(self) -> dict[str, dt.datetime]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT symbol, MAX(completed_at) AS last FROM research_runs "
                "WHERE status IN ('completed','partial') GROUP BY symbol"
            ).fetchall()
        return {r["symbol"]: parse_iso_datetime(r["last"]) for r in rows if r["last"]}

    def claim_queued_run(self, run_id: str) -> bool:
        """Atomically move a queued run to running; False if another delivery got it first (D-98)."""
        with self.db.write() as conn:
            cursor = conn.execute(
                "UPDATE research_runs SET status = 'running', updated_at = ? WHERE run_id = ? AND status = 'queued'",
                (iso_z(utc_now()), run_id),
            )
            return cursor.rowcount == 1

    def fail_run(
        self, run_id: str, message: str, code: ErrorCode | str = ErrorCode.INTERNAL_ERROR, *, retryable: bool = False
    ) -> None:
        """Mark one unfinished run failed with a reason (queue mode: not queued, or interrupted)."""
        error = RunError(code=code, message=message, retryable=retryable)
        now = iso_z(utc_now())
        with self.db.write() as conn:
            conn.execute(
                "UPDATE research_runs SET status = 'failed', stage = 'done', updated_at = ?, completed_at = ?, "
                "errors_json = ? WHERE run_id = ? AND status IN ('queued','running')",
                (now, now, _json([error.model_dump(mode="json")]), run_id),
            )

    def fail_stale_runs(self, cutoff: dt.datetime) -> int:
        """Queue mode: runs still queued or running since before ``cutoff`` can't finish any more.

        A queue-triggered function stops at its maximum duration, so a run whose function was
        cut off would otherwise stay "running" forever. Measured from when it started (or was
        created, if it never started). A run that never started gets its own reason (D-102)."""
        now = iso_z(utc_now())
        stopped = 0
        with self.db.write() as conn:
            for status, code, message in (
                (
                    "queued",
                    ErrorCode.RESEARCH_NOT_STARTED,
                    "The research service didn't start this run in time. Try again.",
                ),
                (
                    "running",
                    ErrorCode.RESEARCH_TIMED_OUT,
                    "The research run didn't finish in time and was stopped. Try again.",
                ),
            ):
                error = RunError(code=code, message=message, retryable=True)
                cursor = conn.execute(
                    "UPDATE research_runs SET status = 'failed', stage = 'done', updated_at = ?, completed_at = ?, "
                    "errors_json = ? WHERE status = ? AND COALESCE(started_at, created_at) < ?",
                    (now, now, _json([error.model_dump(mode="json")]), status, iso_z(cutoff)),
                )
                stopped += cursor.rowcount
        return stopped

    def mark_interrupted(self) -> int:
        """Runs left active by a previous process cannot resume; mark them failed."""
        error = RunError(code="INTERNAL_ERROR", message="Interrupted by a backend restart before completion.")
        now = iso_z(utc_now())
        with self.db.write() as conn:
            cursor = conn.execute(
                "UPDATE research_runs SET status = 'failed', stage = 'done', updated_at = ?, completed_at = ?, "
                "errors_json = ? WHERE status IN ('queued','running')",
                (now, now, _json([error.model_dump(mode="json")])),
            )
            return cursor.rowcount

    @staticmethod
    def _run(row: sqlite3.Row) -> ResearchRun:
        return ResearchRun(
            run_id=row["run_id"],
            symbol=row["symbol"],
            company=CompanyIdentity.model_validate_json(row["company_json"]) if row["company_json"] else None,
            question=row["question"],
            time_horizon=TimeHorizon.model_validate_json(row["time_horizon_json"]),
            focus_areas=json.loads(row["focus_areas_json"]),
            status=row["status"],
            stage=row["stage"],
            data_mode=row["data_mode"],
            llm_mode=row["llm_mode"],
            origin=row["origin"],
            parent_run_id=row["parent_run_id"],
            created_at=parse_iso_datetime(row["created_at"]),
            updated_at=parse_iso_datetime(row["updated_at"]),
            started_at=_dt(row["started_at"]),
            completed_at=_dt(row["completed_at"]),
            is_partial=bool(row["is_partial"]),
            errors=[RunError.model_validate(e) for e in json.loads(row["errors_json"])],
            cancel_requested=bool(row["cancel_requested"]),
            duration_ms=row["duration_ms"],
        )

    # ------------------------------------------------------------------ steps
    def upsert_step(self, step: AgentStep) -> None:
        with self.db.write() as conn:
            conn.execute(
                """INSERT INTO research_steps (run_id, seq, step_key, agent, status, summary, detail_json,
                started_at, ended_at, latency_ms, error_code) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id, seq) DO UPDATE SET status = excluded.status, summary = excluded.summary,
                detail_json = excluded.detail_json, started_at = excluded.started_at, ended_at = excluded.ended_at,
                latency_ms = excluded.latency_ms, error_code = excluded.error_code""",
                (
                    step.run_id,
                    step.seq,
                    step.step_key,
                    step.agent,
                    step.status.value,
                    step.summary,
                    _json(step.detail),
                    _ts(step.started_at),
                    _ts(step.ended_at),
                    step.latency_ms,
                    step.error_code.value if step.error_code else None,
                ),
            )

    def list_steps(self, run_id: str) -> list[AgentStep]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM research_steps WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        return [
            AgentStep(
                run_id=r["run_id"],
                seq=r["seq"],
                step_key=r["step_key"],
                agent=r["agent"],
                status=r["status"],
                summary=r["summary"],
                detail=json.loads(r["detail_json"]),
                started_at=_dt(r["started_at"]),
                ended_at=_dt(r["ended_at"]),
                latency_ms=r["latency_ms"],
                error_code=r["error_code"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ tool calls
    def add_tool_call(self, record: ToolCallRecord) -> None:
        with self.db.write() as conn:
            conn.execute(
                """INSERT INTO tool_calls (call_id, run_id, seq, server_id, tool_name, transport, input_summary_json,
                output_summary_json, status, error_code, latency_ms, idempotency_key, started_at, ended_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.call_id,
                    record.run_id,
                    record.seq,
                    record.server_id,
                    record.tool_name,
                    record.transport,
                    _json(record.input_summary),
                    _json(record.output_summary),
                    record.status,
                    record.error_code.value if record.error_code else None,
                    record.latency_ms,
                    record.idempotency_key,
                    _ts(record.started_at),
                    _ts(record.ended_at),
                ),
            )

    def list_tool_calls(self, run_id: str) -> list[ToolCallRecord]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY seq, started_at", (run_id,)
            ).fetchall()
        return [
            ToolCallRecord(
                call_id=r["call_id"],
                run_id=r["run_id"],
                seq=r["seq"],
                server_id=r["server_id"],
                tool_name=r["tool_name"],
                transport=r["transport"],
                input_summary=json.loads(r["input_summary_json"]),
                output_summary=json.loads(r["output_summary_json"]),
                status=r["status"],
                error_code=r["error_code"],
                latency_ms=r["latency_ms"],
                idempotency_key=r["idempotency_key"],
                started_at=parse_iso_datetime(r["started_at"]),
                ended_at=_dt(r["ended_at"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ evidence, claims, brief
    def save_evidence(self, run_id: str, items: list[EvidenceItem]) -> None:
        with self.db.write() as conn:
            conn.executemany(
                """INSERT INTO evidence_items (run_id, evidence_id, kind, symbol, source_name, source_url,
                published_date, period_start, period_end, retrieved_at, source_mode, is_fixture, content_hash,
                item_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                [
                    (
                        run_id,
                        e.evidence_id,
                        e.kind.value,
                        e.symbol,
                        e.source_name,
                        e.source_url,
                        e.published_date.isoformat() if e.published_date else None,
                        e.period_start.isoformat() if e.period_start else None,
                        e.period_end.isoformat() if e.period_end else None,
                        _ts(e.retrieved_at),
                        e.source_mode.value,
                        int(e.is_fixture),
                        e.content_hash,
                        e.model_dump_json(),
                    )
                    for e in items
                ],
            )

    def list_evidence(self, run_id: str) -> list[EvidenceItem]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT item_json FROM evidence_items WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
        return [EvidenceItem.model_validate_json(r["item_json"]) for r in rows]

    def save_claims(self, run_id: str, claims: list[ResearchClaim], withheld: list[ResearchClaim]) -> None:
        rows = [(c, 0) for c in claims] + [(c, 1) for c in withheld]
        with self.db.write() as conn:
            conn.execute("DELETE FROM research_claims WHERE run_id = ?", (run_id,))
            conn.executemany(
                """INSERT INTO research_claims (run_id, claim_id, section, claim_type, text, materiality,
                evidence_ids_json, validation_status, validation_notes_json, withheld) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id,
                        c.claim_id,
                        c.section.value,
                        c.claim_type.value,
                        c.text,
                        c.materiality,
                        _json(c.evidence_ids),
                        c.validation_status.value,
                        _json(c.validation_notes),
                        flag,
                    )
                    for c, flag in rows
                ],
            )

    def save_brief(self, run_id: str, brief: ResearchBrief, payload_hash: str) -> None:
        with self.db.write() as conn:
            conn.execute(
                """INSERT INTO research_briefs (run_id, brief_json, payload_hash, report_status, created_at)
                VALUES (?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING""",
                (run_id, brief.model_dump_json(), payload_hash, brief.report_status.value, iso_z(utc_now())),
            )

    def get_brief(self, run_id: str) -> tuple[ResearchBrief, str] | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT brief_json, payload_hash FROM research_briefs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        return ResearchBrief.model_validate_json(row["brief_json"]), row["payload_hash"]


class WorkspaceRepository:
    """Watchlist and saved briefs. Writes happen only inside confirmed-action transactions."""

    def __init__(self, db: AnyDatabase) -> None:
        self.db = db

    def list_watchlist(self) -> list[WatchlistItem]:
        with self.db.read() as conn:
            rows = conn.execute(
                """SELECT w.*, (SELECT MAX(r.completed_at) FROM research_runs r WHERE r.symbol = w.symbol
                AND r.status IN ('completed','partial')) AS last_research_at
                FROM watchlist_items w ORDER BY w.symbol"""
            ).fetchall()
        return [
            WatchlistItem(
                symbol=r["symbol"],
                display_name=r["display_name"],
                origin=r["origin"],
                created_at=parse_iso_datetime(r["created_at"]),
                updated_at=parse_iso_datetime(r["updated_at"]),
                last_research_at=_dt(r["last_research_at"]),
            )
            for r in rows
        ]

    def watchlist_contains(self, symbol: str) -> bool:
        with self.db.read() as conn:
            return conn.execute("SELECT 1 FROM watchlist_items WHERE symbol = ?", (symbol,)).fetchone() is not None

    def list_saved_briefs(self, *, limit: int = 20, symbol: str | None = None) -> list[SavedBriefSummary]:
        query = "SELECT * FROM saved_briefs"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        with self.db.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._summary(r) for r in rows]

    def get_saved_brief(self, brief_id: str) -> SavedBrief | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT snapshot_json FROM saved_briefs WHERE brief_id = ?", (brief_id,)).fetchone()
        return SavedBrief.model_validate_json(row["snapshot_json"]) if row else None

    @staticmethod
    def _summary(row: sqlite3.Row) -> SavedBriefSummary:
        return SavedBriefSummary(
            brief_id=row["brief_id"],
            run_id=row["run_id"],
            title=row["title"],
            symbol=row["symbol"],
            created_at=parse_iso_datetime(row["created_at"]),
            report_status=row["report_status"],
            data_mode=row["data_mode"],
            is_fixture=bool(row["is_fixture"]),
        )


class AuditRepository:
    def __init__(self, db: AnyDatabase) -> None:
        self.db = db

    def record(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        params = (iso_z(utc_now()), event_type, run_id, action_id, _json(metadata or {}))
        sql = "INSERT INTO audit_events (occurred_at, event_type, run_id, action_id, metadata_json) VALUES (?,?,?,?,?)"
        if conn is not None:
            conn.execute(sql, params)
            return
        with self.db.write() as own:
            own.execute(sql, params)

    def list_events(self, *, limit: int = 200, event_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events"
        params: list[Any] = []
        if event_type:
            query += " WHERE event_type = ?"
            params.append(event_type)
        query += " ORDER BY event_id DESC LIMIT ?"
        params.append(limit)
        with self.db.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "event_id": r["event_id"],
                "occurred_at": r["occurred_at"],
                "event_type": r["event_type"],
                "run_id": r["run_id"],
                "action_id": r["action_id"],
                "metadata": json.loads(r["metadata_json"]),
            }
            for r in rows
        ]


class VoiceSessionRecord(DomainModel):
    room_name: str
    session_hash: str
    participant_identity: str
    created_at: dt.datetime
    expires_at: dt.datetime
    active_run_id: str | None = None
    ended_at: dt.datetime | None = None

    def is_expired(self, now: dt.datetime) -> bool:
        return self.ended_at is not None or now >= self.expires_at


class VoiceSessionRepository:
    """Voice rooms bound to the browser session (by hash) that requested them."""

    def __init__(self, db: AnyDatabase) -> None:
        self.db = db

    def create(self, record: VoiceSessionRecord) -> None:
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO voice_sessions (room_name, session_hash, participant_identity, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (
                    record.room_name,
                    record.session_hash,
                    record.participant_identity,
                    _ts(record.created_at),
                    _ts(record.expires_at),
                ),
            )

    def get(self, room_name: str) -> VoiceSessionRecord | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM voice_sessions WHERE room_name = ?", (room_name,)).fetchone()
        if row is None:
            return None
        return VoiceSessionRecord(
            room_name=row["room_name"],
            session_hash=row["session_hash"],
            participant_identity=row["participant_identity"],
            created_at=_dt(row["created_at"]),
            expires_at=_dt(row["expires_at"]),
            active_run_id=row["active_run_id"],
            ended_at=_dt(row["ended_at"]),
        )

    def count_since(self, session_hash: str, since: dt.datetime) -> int:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM voice_sessions WHERE session_hash = ? AND created_at >= ?",
                (session_hash, _ts(since)),
            ).fetchone()
        return int(row["n"])

    def set_active_run(self, room_name: str, run_id: str) -> None:
        with self.db.write() as conn:
            conn.execute("UPDATE voice_sessions SET active_run_id = ? WHERE room_name = ?", (run_id, room_name))

    def end(self, room_name: str, when: dt.datetime) -> None:
        with self.db.write() as conn:
            conn.execute(
                "UPDATE voice_sessions SET ended_at = ? WHERE room_name = ? AND ended_at IS NULL",
                (_ts(when), room_name),
            )
