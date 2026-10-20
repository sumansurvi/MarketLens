"""Confirmation-gated writes (MCP_TOOLS §6 security model).

Flow: a proposal creates a ``pending_actions`` row (no artifact is written) → the user
confirms in the UI → the backend checks the session and issues a one-time, short-lived
grant → the workspace MCP server re-validates the grant (existence, expiry, session,
action type, payload hash, unconsumed) and writes the artifact *in the same transaction*
that consumes the grant. Model output is never authorization; a ``confirmed: true`` flag
is never accepted.

Payload hashes are computed server-side from authoritative state (the run's stored brief
hash, the normalized symbol) by ``action_payload`` here, used identically by the backend
and the workspace server. Grant IDs and session IDs are stored only as hashes.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any

from marketlens_core.db.connection import AnyDatabase
from marketlens_core.db.repositories import AuditRepository
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.hashing import content_hash, hash_secret, new_id, new_secret_token
from marketlens_core.models import ActionStatus, ActionType, ConfirmationGrant, PendingAction
from marketlens_core.symbols import normalize_symbol
from marketlens_core.timeutil import iso_z, parse_iso_datetime, utc_now

GRANT_TTL_SECONDS = 60
SAVEABLE_STATUSES = ("completed", "partial")


def action_payload(
    action_type: ActionType | str,
    *,
    symbol: str | None = None,
    run_id: str | None = None,
    title: str | None = None,
    brief_payload_hash: str | None = None,
) -> dict[str, Any]:
    """Canonical payload whose hash binds a confirmation to exactly one write."""
    kind = ActionType(action_type)
    if kind == ActionType.SAVE_RESEARCH_BRIEF:
        return {"action_type": kind.value, "run_id": run_id, "title": title, "brief_payload_hash": brief_payload_hash}
    return {"action_type": kind.value, "symbol": normalize_symbol(symbol)}


def payload_hash_of(payload: dict[str, Any]) -> str:
    return content_hash(payload)


def brief_hash_for_run(conn: sqlite3.Connection, run_id: str) -> tuple[str, str, str] | None:
    """(brief payload hash, report status, symbol) for a run's stored brief, if any."""
    row = conn.execute(
        "SELECT b.payload_hash, b.report_status, r.symbol FROM research_briefs b "
        "JOIN research_runs r ON r.run_id = b.run_id WHERE b.run_id = ?",
        (run_id,),
    ).fetchone()
    return (row["payload_hash"], row["report_status"], row["symbol"]) if row else None


class ActionService:
    def __init__(self, db: AnyDatabase, *, action_ttl_seconds: int) -> None:
        self.db = db
        self.action_ttl_seconds = action_ttl_seconds
        self.audit = AuditRepository(db)

    # ------------------------------------------------------------------ proposals
    def expected_payload(
        self,
        conn: sqlite3.Connection,
        action_type: ActionType,
        *,
        run_id: str | None,
        symbol: str | None,
        title: str | None,
    ) -> tuple[dict[str, Any], str | None]:
        """Authoritative payload for a proposal, plus the symbol it concerns."""
        if action_type == ActionType.SAVE_RESEARCH_BRIEF:
            if not run_id:
                raise MarketLensError(ErrorCode.INVALID_INPUT, "Saving a brief requires a run ID.")
            found = brief_hash_for_run(conn, run_id)
            if found is None:
                raise MarketLensError(ErrorCode.NOT_FOUND, "That research run has no brief to save.")
            brief_hash, status, run_symbol = found
            if status not in SAVEABLE_STATUSES:
                raise MarketLensError(ErrorCode.INVALID_INPUT, f"A {status} report cannot be saved.")
            if not title:
                raise MarketLensError(ErrorCode.INVALID_INPUT, "Saving a brief requires a title.")
            payload = action_payload(action_type, run_id=run_id, title=title, brief_payload_hash=brief_hash)
            return payload, run_symbol
        normalized = normalize_symbol(symbol)
        return action_payload(action_type, symbol=normalized), normalized

    def create(
        self,
        *,
        action_type: ActionType | str,
        run_id: str | None,
        symbol: str | None,
        title: str | None,
        display_name: str | None,
        payload_hash: str,
        session_hash: str,
        origin: str = "ui",
    ) -> PendingAction:
        kind = ActionType(action_type)
        now = utc_now()
        with self.db.write() as conn:
            payload, concerned_symbol = self.expected_payload(conn, kind, run_id=run_id, symbol=symbol, title=title)
            if payload_hash_of(payload) != payload_hash:
                raise MarketLensError(
                    ErrorCode.PAYLOAD_HASH_MISMATCH,
                    "The proposed change does not match the server's record; refresh and try again.",
                )
            if (
                kind == ActionType.REMOVE_WATCHLIST_SYMBOL
                and not conn.execute("SELECT 1 FROM watchlist_items WHERE symbol = ?", (concerned_symbol,)).fetchone()
            ):
                raise MarketLensError(ErrorCode.NOT_FOUND, f"{concerned_symbol} is not on the watchlist.")
            summary = {
                "symbol": concerned_symbol,
                "title": title,
                "display_name": display_name,
                "destination": "saved briefs" if kind == ActionType.SAVE_RESEARCH_BRIEF else "watchlist",
            }
            if kind == ActionType.SAVE_RESEARCH_BRIEF and run_id:
                row = conn.execute("SELECT data_mode FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
                summary["data_mode"] = row["data_mode"] if row else None
            action = PendingAction(
                action_id=new_id("act"),
                action_type=kind,
                run_id=run_id if kind == ActionType.SAVE_RESEARCH_BRIEF else None,
                symbol=concerned_symbol,
                title=title,
                summary=summary,
                payload_hash=payload_hash,
                status=ActionStatus.PENDING,
                origin=origin,  # type: ignore[arg-type]
                created_at=now,
                expires_at=now + dt.timedelta(seconds=self.action_ttl_seconds),
            )
            conn.execute(
                """INSERT INTO pending_actions (action_id, action_type, run_id, symbol, title, session_hash,
                payload_json, payload_hash, summary_json, status, origin, created_at, expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    action.action_id,
                    kind.value,
                    action.run_id,
                    action.symbol,
                    action.title,
                    session_hash,
                    json.dumps(payload),
                    payload_hash,
                    json.dumps(summary),
                    action.status.value,
                    origin,
                    iso_z(action.created_at),
                    iso_z(action.expires_at),
                ),
            )
            self.audit.record(
                "action_proposed",
                run_id=action.run_id,
                action_id=action.action_id,
                metadata={"action_type": kind.value, "origin": origin, "symbol": concerned_symbol},
                conn=conn,
            )
        return action

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _action(row: sqlite3.Row) -> PendingAction:
        return PendingAction(
            action_id=row["action_id"],
            action_type=row["action_type"],
            run_id=row["run_id"],
            symbol=row["symbol"],
            title=row["title"],
            summary=json.loads(row["summary_json"]),
            payload_hash=row["payload_hash"],
            status=row["status"],
            origin=row["origin"],
            created_at=parse_iso_datetime(row["created_at"]),
            expires_at=parse_iso_datetime(row["expires_at"]),
            confirmed_at=parse_iso_datetime(row["confirmed_at"]) if row["confirmed_at"] else None,
            consumed_at=parse_iso_datetime(row["consumed_at"]) if row["consumed_at"] else None,
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )

    def get_for_session(self, action_id: str, session_hash: str) -> PendingAction:
        """A pending action is only visible to the session that proposed it."""
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)).fetchone()
        if row is None or row["session_hash"] != session_hash:
            raise MarketLensError(ErrorCode.NOT_FOUND, "Action not found.")
        action = self._action(row)
        if action.status in (ActionStatus.PENDING, ActionStatus.CONFIRMED) and utc_now() > action.expires_at:
            action = action.model_copy(update={"status": ActionStatus.EXPIRED})
        return action

    # ------------------------------------------------------------------ confirmation
    def issue_grant(self, action_id: str, session_hash: str) -> tuple[str, ConfirmationGrant]:
        """Called by the backend after an explicit UI confirmation over the session cookie."""
        now = utc_now()
        with self.db.write() as conn:
            row = conn.execute("SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)).fetchone()
            if row is None or row["session_hash"] != session_hash:
                raise MarketLensError(ErrorCode.NOT_FOUND, "Action not found.")
            action = self._action(row)
            if action.status == ActionStatus.CONSUMED:
                raise MarketLensError(
                    ErrorCode.ACTION_ALREADY_CONSUMED,
                    "This action was already completed.",
                    details={"result": action.result or {}},
                )
            if action.status in (ActionStatus.CANCELLED, ActionStatus.FAILED):
                raise MarketLensError(ErrorCode.INVALID_CONFIRMATION_GRANT, f"This action was {action.status.value}.")
            if action.status == ActionStatus.EXPIRED or now > action.expires_at:
                conn.execute("UPDATE pending_actions SET status = 'expired' WHERE action_id = ?", (action_id,))
                raise MarketLensError(
                    ErrorCode.CONFIRMATION_EXPIRED, "This confirmation expired; propose the change again."
                )
            grant_id = new_secret_token()
            expires = min(now + dt.timedelta(seconds=GRANT_TTL_SECONDS), action.expires_at)
            conn.execute("DELETE FROM confirmation_grants WHERE action_id = ? AND consumed_at IS NULL", (action_id,))
            conn.execute(
                """INSERT INTO confirmation_grants (grant_hash, action_id, session_hash, payload_hash, issued_at, expires_at)
                VALUES (?,?,?,?,?,?)""",
                (hash_secret(grant_id), action_id, session_hash, action.payload_hash, iso_z(now), iso_z(expires)),
            )
            conn.execute(
                "UPDATE pending_actions SET status = 'confirmed', confirmed_at = ? WHERE action_id = ?",
                (iso_z(now), action_id),
            )
            self.audit.record(
                "action_confirmed",
                run_id=action.run_id,
                action_id=action_id,
                metadata={"action_type": action.action_type.value},
                conn=conn,
            )
        return grant_id, ConfirmationGrant(action_id=action_id, issued_at=now, expires_at=expires, grant_id=grant_id)

    def consume(
        self,
        conn: sqlite3.Connection,
        *,
        grant_id: str,
        session_hash: str,
        action_type: ActionType,
        expected_payload_hash: str,
        recomputed_payload_hash: str,
    ) -> tuple[PendingAction, str]:
        """Validate a grant inside the caller's write transaction. Returns (action, grant hash)."""
        if not grant_id:
            raise MarketLensError(ErrorCode.CONFIRMATION_REQUIRED, "A server-issued confirmation grant is required.")
        grant_hash = hash_secret(grant_id)
        grant = conn.execute("SELECT * FROM confirmation_grants WHERE grant_hash = ?", (grant_hash,)).fetchone()
        if grant is None:
            raise MarketLensError(ErrorCode.INVALID_CONFIRMATION_GRANT, "The confirmation grant is not valid.")
        row = conn.execute("SELECT * FROM pending_actions WHERE action_id = ?", (grant["action_id"],)).fetchone()
        action = self._action(row)
        if grant["consumed_at"] or action.status == ActionStatus.CONSUMED:
            raise MarketLensError(
                ErrorCode.ACTION_ALREADY_CONSUMED,
                "This confirmation was already used.",
                details={"result": action.result or {}},
            )
        if utc_now() > parse_iso_datetime(grant["expires_at"]):
            raise MarketLensError(ErrorCode.CONFIRMATION_EXPIRED, "The confirmation grant expired.")
        if grant["session_hash"] != session_hash or row["session_hash"] != session_hash:
            raise MarketLensError(ErrorCode.INVALID_CONFIRMATION_GRANT, "The grant does not belong to this session.")
        if action.action_type != action_type:
            raise MarketLensError(ErrorCode.INVALID_CONFIRMATION_GRANT, "The grant was issued for a different action.")
        if action.status != ActionStatus.CONFIRMED:
            raise MarketLensError(ErrorCode.INVALID_CONFIRMATION_GRANT, "The action has not been confirmed.")
        if expected_payload_hash != action.payload_hash or grant["payload_hash"] != action.payload_hash:
            raise MarketLensError(ErrorCode.PAYLOAD_HASH_MISMATCH, "The payload does not match the approved preview.")
        if recomputed_payload_hash != action.payload_hash:
            raise MarketLensError(
                ErrorCode.PAYLOAD_HASH_MISMATCH, "The requested write differs from what the user approved."
            )
        return action, grant_hash

    def mark_consumed(
        self, conn: sqlite3.Connection, action: PendingAction, grant_hash: str, result: dict[str, Any]
    ) -> None:
        now = iso_z(utc_now())
        conn.execute("UPDATE confirmation_grants SET consumed_at = ? WHERE grant_hash = ?", (now, grant_hash))
        conn.execute(
            "UPDATE pending_actions SET status = 'consumed', consumed_at = ?, result_json = ? WHERE action_id = ?",
            (now, json.dumps(result), action.action_id),
        )
        self.audit.record(
            "action_executed",
            run_id=action.run_id,
            action_id=action.action_id,
            metadata={"action_type": action.action_type.value, "result": result},
            conn=conn,
        )

    def record_rejection(self, action_type: str, error: MarketLensError) -> None:
        """Rejected write attempts are audited (outside the rolled-back transaction)."""
        self.audit.record("write_rejected", metadata={"action_type": action_type, "code": error.code.value})

    def cancel(self, action_id: str, session_hash: str) -> PendingAction:
        with self.db.write() as conn:
            row = conn.execute("SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)).fetchone()
            if row is None or row["session_hash"] != session_hash:
                raise MarketLensError(ErrorCode.NOT_FOUND, "Action not found.")
            if row["status"] in ("pending", "confirmed"):
                conn.execute("UPDATE pending_actions SET status = 'cancelled' WHERE action_id = ?", (action_id,))
                conn.execute(
                    "DELETE FROM confirmation_grants WHERE action_id = ? AND consumed_at IS NULL", (action_id,)
                )
                self.audit.record("action_cancelled", action_id=action_id, conn=conn)
        return self.get_for_session(action_id, session_hash)
