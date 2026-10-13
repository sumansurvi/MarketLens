"""Backend side of confirmed writes and workspace reads (all through the workspace MCP server).

propose → the backend computes the payload hash from authoritative state and asks the
workspace server to record a pending action (no artifact is written);
confirm → only from the session that proposed it: the backend issues a one-time grant and
calls the workspace write tool with it; the server re-validates and consumes the grant in
the same transaction as the write. The raw session cookie never leaves this process; MCP
tools receive only its hash.
"""

from __future__ import annotations

import logging
from typing import Any

from marketlens_core.actions import ActionService, action_payload, payload_hash_of
from marketlens_core.analytics import AnalyticsRepository
from marketlens_core.db.repositories import RunRepository
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.hashing import hash_secret
from marketlens_core.models import ActionType, PendingAction, SavedBrief, SavedBriefSummary, WatchlistItem
from marketlens_core.symbols import normalize_symbol
from marketlens_core.tool_models import (
    PendingActionData,
    ResolveSymbolData,
    SavedBriefData,
    SavedBriefListData,
    WatchlistData,
)

from app.mcp.gateway import ToolCallOutcome, ToolGateway

logger = logging.getLogger("marketlens.workspace")

WRITE_TOOL_FOR = {
    ActionType.SAVE_RESEARCH_BRIEF: "save_research_brief",
    ActionType.ADD_WATCHLIST_SYMBOL: "add_watchlist_symbol",
    ActionType.REMOVE_WATCHLIST_SYMBOL: "remove_watchlist_symbol",
}


def _raise_on_error(outcome: ToolCallOutcome) -> Any:
    if not outcome.ok or outcome.error is not None:
        if outcome.error is None:
            raise MarketLensError(ErrorCode.INTERNAL_ERROR, "The workspace tool failed.")
        raise MarketLensError.from_info(outcome.error)
    return outcome.data


class WorkspaceService:
    def __init__(
        self,
        gateway: ToolGateway,
        runs: RunRepository,
        actions: ActionService,
        analytics: AnalyticsRepository | None = None,
    ) -> None:
        self.gateway = gateway
        self.runs = runs
        self.actions = actions
        self.analytics = analytics

    async def _call(self, tool: str, arguments: dict[str, Any], *, run_id: str | None = None) -> Any:
        outcome = await self.gateway.call("research_workspace", tool, arguments, run_id=run_id)
        self.runs.add_tool_call(outcome.record)
        return _raise_on_error(outcome)

    # ------------------------------------------------------------------ reads
    async def watchlist(self) -> list[WatchlistItem]:
        return WatchlistData.model_validate(await self._call("list_watchlist", {})).items

    async def saved_briefs(self, *, limit: int, symbol: str | None) -> list[SavedBriefSummary]:
        args: dict[str, Any] = {"limit": limit}
        if symbol:
            args["symbol"] = normalize_symbol(symbol)
        return SavedBriefListData.model_validate(await self._call("list_saved_briefs", args)).briefs

    async def saved_brief(self, brief_id: str) -> SavedBrief:
        return SavedBriefData.model_validate(await self._call("get_saved_brief", {"brief_id": brief_id})).saved

    # ------------------------------------------------------------------ proposals
    async def propose(
        self,
        *,
        action_type: ActionType,
        session_id: str,
        run_id: str | None = None,
        symbol: str | None = None,
        title: str | None = None,
        origin: str = "ui",
    ) -> PendingAction:
        display_name = None
        if action_type == ActionType.SAVE_RESEARCH_BRIEF:
            if not run_id:
                raise MarketLensError(ErrorCode.INVALID_INPUT, "run_id is required to save a brief.")
            stored = self.runs.get_brief(run_id)
            if stored is None:
                raise MarketLensError(ErrorCode.NOT_FOUND, "That research run has no brief to save.")
            brief, brief_hash = stored
            title = (title or brief.title).strip()
            payload = action_payload(action_type, run_id=run_id, title=title, brief_payload_hash=brief_hash)
            symbol = brief.company.symbol
        else:
            symbol = normalize_symbol(symbol)
            if action_type == ActionType.ADD_WATCHLIST_SYMBOL:
                # Best-effort identity check: never add an unrecognized or substituted ticker.
                resolved = await self.gateway.call("research_sources", "resolve_symbol", {"symbol": symbol})
                self.runs.add_tool_call(resolved.record)
                display_name = ResolveSymbolData.model_validate(_raise_on_error(resolved)).company_name
            payload = action_payload(action_type, symbol=symbol)
        arguments: dict[str, Any] = {
            "action_type": action_type.value,
            "payload_summary": {"symbol": symbol, "title": title, "display_name": display_name},
            "payload_hash": payload_hash_of(payload),
            "session_id": hash_secret(session_id),
            "origin": origin,
        }
        if run_id and action_type == ActionType.SAVE_RESEARCH_BRIEF:
            arguments["run_id"] = run_id
        data = await self._call("create_pending_action", arguments, run_id=run_id)
        return PendingActionData.model_validate(data).action

    def get(self, action_id: str, session_id: str) -> PendingAction:
        return self.actions.get_for_session(action_id, hash_secret(session_id))

    async def confirm(self, action_id: str, session_id: str) -> tuple[PendingAction, dict[str, Any]]:
        """Issue a one-time grant for an explicit UI confirmation, then perform the MCP write."""
        session_hash = hash_secret(session_id)
        action = self.actions.get_for_session(action_id, session_hash)
        grant_id, _grant = self.actions.issue_grant(action_id, session_hash)
        common = {
            "confirmation_grant_id": grant_id,
            "expected_payload_hash": action.payload_hash,
            "session_id": session_hash,
        }
        if action.action_type == ActionType.SAVE_RESEARCH_BRIEF:
            arguments = {"run_id": action.run_id, "brief_title": action.title, **common}
        else:
            arguments = {"symbol": action.symbol, **common}
        data = await self._call(WRITE_TOOL_FOR[action.action_type], arguments, run_id=action.run_id)
        self._track_write(action, data)
        return self.actions.get_for_session(action_id, session_hash), data

    def _track_write(self, action: PendingAction, data: dict[str, Any]) -> None:
        if self.analytics is None:
            return
        try:
            if action.action_type == ActionType.SAVE_RESEARCH_BRIEF:
                self.analytics.record("brief_saved", data_mode=str(data.get("data_mode", "")))
            elif action.action_type == ActionType.ADD_WATCHLIST_SYMBOL:
                outcome = "already_present" if data.get("already_exists") else "added"
                self.analytics.record("watchlist_changed", action="add", outcome=outcome)
            else:
                outcome = "removed" if data.get("removed") else "already_absent"
                self.analytics.record("watchlist_changed", action="remove", outcome=outcome)
        except Exception:  # noqa: BLE001 - analytics never breaks a confirmed write
            logger.warning("could not record analytics for %s", action.action_type.value)

    def cancel(self, action_id: str, session_id: str) -> PendingAction:
        return self.actions.cancel(action_id, hash_secret(session_id))
