"""Research Workspace business logic (MCP_TOOLS §6): watchlist and saved briefs.

Reads are open; every write requires a server-issued one-time confirmation grant that is
validated and consumed in the same SQLite transaction that persists the artifact, so a
write can never happen without a consumed grant and a grant can never be used twice.
This process never receives provider credentials.
"""

from __future__ import annotations

from marketlens_core.actions import ActionService, action_payload, brief_hash_for_run, payload_hash_of
from marketlens_core.config import Settings
from marketlens_core.db import Database, initialize_database
from marketlens_core.db.repositories import WorkspaceRepository
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.hashing import new_id
from marketlens_core.models import ActionType, EvidenceItem, ResearchBrief, SavedBrief
from marketlens_core.symbols import normalize_symbol
from marketlens_core.timeutil import iso_z, utc_now
from marketlens_core.tool_models import (
    CreatePendingActionInput,
    GetSavedBriefInput,
    ListSavedBriefsInput,
    PendingActionData,
    SaveBriefData,
    SaveBriefInput,
    SavedBriefData,
    SavedBriefListData,
    WatchlistData,
    WatchlistWriteData,
    WatchlistWriteInput,
)

SERVER_ID = "research_workspace"


class WorkspaceService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.db = db
        self.repo = WorkspaceRepository(db)
        self.actions = ActionService(db, action_ttl_seconds=settings.action_confirmation_ttl_seconds)

    # ------------------------------------------------------------------ reads
    async def list_watchlist(self, _args: object) -> WatchlistData:
        return WatchlistData(items=self.repo.list_watchlist())

    async def list_saved_briefs(self, args: ListSavedBriefsInput) -> SavedBriefListData:
        symbol = normalize_symbol(args.symbol) if args.symbol else None
        return SavedBriefListData(briefs=self.repo.list_saved_briefs(limit=args.limit, symbol=symbol))

    async def get_saved_brief(self, args: GetSavedBriefInput) -> SavedBriefData:
        saved = self.repo.get_saved_brief(args.brief_id)
        if saved is None:
            raise MarketLensError(ErrorCode.NOT_FOUND, "Saved brief not found.")
        return SavedBriefData(saved=saved)

    # ------------------------------------------------------------------ proposals (no artifact written)
    async def create_pending_action(self, args: CreatePendingActionInput) -> PendingActionData:
        action = self.actions.create(
            action_type=args.action_type,
            run_id=args.run_id,
            symbol=args.payload_summary.symbol,
            title=args.payload_summary.title,
            display_name=args.payload_summary.display_name,
            payload_hash=args.payload_hash,
            session_hash=args.session_id,
            origin=args.origin,
        )
        return PendingActionData(action=action)

    # ------------------------------------------------------------------ confirmed writes
    def _rejected(self, action_type: ActionType, exc: MarketLensError) -> MarketLensError:
        self.actions.record_rejection(action_type.value, exc)
        return exc

    async def add_watchlist_symbol(self, args: WatchlistWriteInput) -> WatchlistWriteData:
        kind = ActionType.ADD_WATCHLIST_SYMBOL
        symbol = normalize_symbol(args.symbol)
        recomputed = payload_hash_of(action_payload(kind, symbol=symbol))
        try:
            with self.db.write() as conn:
                action, grant_hash = self.actions.consume(
                    conn,
                    grant_id=args.confirmation_grant_id,
                    session_hash=args.session_id,
                    action_type=kind,
                    expected_payload_hash=args.expected_payload_hash,
                    recomputed_payload_hash=recomputed,
                )
                exists = conn.execute("SELECT 1 FROM watchlist_items WHERE symbol = ?", (symbol,)).fetchone()
                if not exists:
                    now = iso_z(utc_now())
                    conn.execute(
                        """INSERT INTO watchlist_items (symbol, display_name, origin, created_at, updated_at, last_action_id)
                        VALUES (?,?,?,?,?,?)""",
                        (
                            symbol,
                            action.summary.get("display_name"),
                            f"{action.origin}-confirmed",
                            now,
                            now,
                            action.action_id,
                        ),
                    )
                result = {"symbol": symbol, "already_exists": bool(exists)}
                self.actions.mark_consumed(conn, action, grant_hash, result)
        except MarketLensError as exc:
            raise self._rejected(kind, exc) from None
        item = next((i for i in self.repo.list_watchlist() if i.symbol == symbol), None)
        return WatchlistWriteData(symbol=symbol, action_id=action.action_id, item=item, already_exists=bool(exists))

    async def remove_watchlist_symbol(self, args: WatchlistWriteInput) -> WatchlistWriteData:
        kind = ActionType.REMOVE_WATCHLIST_SYMBOL
        symbol = normalize_symbol(args.symbol)
        recomputed = payload_hash_of(action_payload(kind, symbol=symbol))
        try:
            with self.db.write() as conn:
                action, grant_hash = self.actions.consume(
                    conn,
                    grant_id=args.confirmation_grant_id,
                    session_hash=args.session_id,
                    action_type=kind,
                    expected_payload_hash=args.expected_payload_hash,
                    recomputed_payload_hash=recomputed,
                )
                deleted = conn.execute("DELETE FROM watchlist_items WHERE symbol = ?", (symbol,)).rowcount
                result = {"symbol": symbol, "removed": deleted > 0, "already_absent": deleted == 0}
                self.actions.mark_consumed(conn, action, grant_hash, result)
        except MarketLensError as exc:
            raise self._rejected(kind, exc) from None
        return WatchlistWriteData(
            symbol=symbol, action_id=action.action_id, removed=deleted > 0, already_absent=deleted == 0
        )

    async def save_research_brief(self, args: SaveBriefInput) -> SaveBriefData:
        kind = ActionType.SAVE_RESEARCH_BRIEF
        try:
            with self.db.write() as conn:
                found = brief_hash_for_run(conn, args.run_id)
                if found is None:
                    raise MarketLensError(ErrorCode.NOT_FOUND, "The research run or its brief no longer exists.")
                brief_hash, report_status, symbol = found
                recomputed = payload_hash_of(
                    action_payload(kind, run_id=args.run_id, title=args.brief_title, brief_payload_hash=brief_hash)
                )
                action, grant_hash = self.actions.consume(
                    conn,
                    grant_id=args.confirmation_grant_id,
                    session_hash=args.session_id,
                    action_type=kind,
                    expected_payload_hash=args.expected_payload_hash,
                    recomputed_payload_hash=recomputed,
                )
                if action.run_id != args.run_id:
                    raise MarketLensError(ErrorCode.PAYLOAD_HASH_MISMATCH, "The grant was issued for a different run.")
                brief_row = conn.execute(
                    "SELECT brief_json FROM research_briefs WHERE run_id = ?", (args.run_id,)
                ).fetchone()
                brief = ResearchBrief.model_validate_json(brief_row["brief_json"])
                evidence = [
                    EvidenceItem.model_validate_json(r["item_json"])
                    for r in conn.execute(
                        "SELECT item_json FROM evidence_items WHERE run_id = ? ORDER BY rowid", (args.run_id,)
                    )
                ]
                now = utc_now()
                saved = SavedBrief(
                    brief_id=new_id("brief"),
                    run_id=args.run_id,
                    title=args.brief_title,
                    symbol=symbol,
                    created_at=now,
                    report_status=report_status,
                    data_mode=brief.data_mode,
                    is_fixture=brief.is_fixture,
                    payload_hash=brief_hash,
                    brief=brief,
                    evidence=evidence,
                )
                conn.execute(
                    """INSERT INTO saved_briefs (brief_id, run_id, title, symbol, report_status, data_mode, is_fixture,
                    payload_hash, snapshot_json, action_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        saved.brief_id,
                        saved.run_id,
                        saved.title,
                        saved.symbol,
                        report_status,
                        brief.data_mode.value,
                        int(brief.is_fixture),
                        brief_hash,
                        saved.model_dump_json(),
                        action.action_id,
                        iso_z(now),
                    ),
                )
                result = {"brief_id": saved.brief_id, "run_id": saved.run_id, "title": saved.title}
                self.actions.mark_consumed(conn, action, grant_hash, result)
        except MarketLensError as exc:
            raise self._rejected(kind, exc) from None
        return SaveBriefData(
            brief_id=saved.brief_id,
            run_id=saved.run_id,
            title=saved.title,
            created_at=saved.created_at,
            report_status=report_status,
            data_mode=brief.data_mode.value,
            is_fixture=brief.is_fixture,
            action_id=action.action_id,
        )


def build_workspace_service(settings: Settings) -> WorkspaceService:
    db = Database(settings.database_path)
    initialize_database(db)
    return WorkspaceService(settings, db)
