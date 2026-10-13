"""Research Workspace MCP server (server ID ``research_workspace``).

Read tools plus narrowly scoped writes (watchlist, saved briefs). A write only succeeds
with a valid, unexpired, session-bound, unconsumed confirmation grant whose payload hash
matches what the user approved; LLM tool invocation is never consent. This process never
receives provider API keys.

Run as its own process over stdio: ``python -m mcp_research_workspace``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from marketlens_core import __version__
from marketlens_core.mcp_support import (
    CONFIRMED_REMOVE,
    CONFIRMED_WRITE,
    LOCAL_READ,
    PROPOSAL,
    configure_server_logging,
    run_tool,
    server_settings,
)
from marketlens_core.tool_models import (
    ActionTypeArg,
    BriefIdArg,
    CreatePendingActionInput,
    EmptyInput,
    GetSavedBriefInput,
    GrantArg,
    ListSavedBriefsInput,
    PayloadHashArg,
    PayloadSummary,
    PendingActionData,
    RunIdArg,
    SaveBriefData,
    SaveBriefInput,
    SavedBriefData,
    SavedBriefListData,
    SessionArg,
    SymbolArg,
    TitleArg,
    ToolResult,
    WatchlistData,
    WatchlistWriteData,
    WatchlistWriteInput,
)
from mcp.server import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from mcp_research_workspace.service import SERVER_ID, WorkspaceService, build_workspace_service

INSTRUCTIONS = (
    "App-local MarketLens workspace: watchlist and saved research briefs. Write tools require a one-time "
    "confirmation grant issued by the backend after the user confirms in the UI; requests without a valid grant "
    "are rejected. There are no trading, order or brokerage tools."
)


def create_server(service: WorkspaceService) -> MCPServer:
    mcp = MCPServer(
        "marketlens-research-workspace",
        title="MarketLens Research Workspace",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    @mcp.tool(
        name="list_watchlist",
        title="List watchlist",
        description="Watchlist symbols with display names, timestamps and latest research time.",
        annotations=LOCAL_READ,
    )
    async def list_watchlist() -> Annotated[CallToolResult, ToolResult[WatchlistData]]:
        return await run_tool(SERVER_ID, "list_watchlist", EmptyInput, service.list_watchlist)

    @mcp.tool(
        name="add_watchlist_symbol",
        title="Add watchlist symbol (confirmed)",
        description="Add a symbol to the app-local watchlist. Requires a valid one-time confirmation grant. "
        "Idempotent: an existing symbol returns already_exists=true without a duplicate row.",
        annotations=CONFIRMED_WRITE,
    )
    async def add_watchlist_symbol(
        symbol: SymbolArg,
        confirmation_grant_id: GrantArg,
        expected_payload_hash: PayloadHashArg,
        session_id: SessionArg,
    ) -> Annotated[CallToolResult, ToolResult[WatchlistWriteData]]:
        return await run_tool(
            SERVER_ID,
            "add_watchlist_symbol",
            lambda: WatchlistWriteInput(
                symbol=symbol,
                confirmation_grant_id=confirmation_grant_id,
                expected_payload_hash=expected_payload_hash,
                session_id=session_id,
            ),
            service.add_watchlist_symbol,
        )

    @mcp.tool(
        name="remove_watchlist_symbol",
        title="Remove watchlist symbol (confirmed)",
        description="Remove a symbol from the app-local watchlist. Requires a valid one-time confirmation "
        "grant. Idempotent: an absent symbol returns removed=false, already_absent=true. No fuzzy matching.",
        annotations=CONFIRMED_REMOVE,
    )
    async def remove_watchlist_symbol(
        symbol: SymbolArg,
        confirmation_grant_id: GrantArg,
        expected_payload_hash: PayloadHashArg,
        session_id: SessionArg,
    ) -> Annotated[CallToolResult, ToolResult[WatchlistWriteData]]:
        return await run_tool(
            SERVER_ID,
            "remove_watchlist_symbol",
            lambda: WatchlistWriteInput(
                symbol=symbol,
                confirmation_grant_id=confirmation_grant_id,
                expected_payload_hash=expected_payload_hash,
                session_id=session_id,
            ),
            service.remove_watchlist_symbol,
        )

    @mcp.tool(
        name="save_research_brief",
        title="Save research brief (confirmed)",
        description="Snapshot the server-side brief of a completed or partial run (with its evidence, "
        "timestamps and data mode). Requires a valid one-time confirmation grant; never overwrites an "
        "existing saved brief.",
        annotations=CONFIRMED_WRITE,
    )
    async def save_research_brief(
        run_id: RunIdArg,
        brief_title: TitleArg,
        confirmation_grant_id: GrantArg,
        expected_payload_hash: PayloadHashArg,
        session_id: SessionArg,
    ) -> Annotated[CallToolResult, ToolResult[SaveBriefData]]:
        return await run_tool(
            SERVER_ID,
            "save_research_brief",
            lambda: SaveBriefInput(
                run_id=run_id,
                brief_title=brief_title,
                confirmation_grant_id=confirmation_grant_id,
                expected_payload_hash=expected_payload_hash,
                session_id=session_id,
            ),
            service.save_research_brief,
        )

    @mcp.tool(
        name="list_saved_briefs",
        title="List saved briefs",
        description="Saved brief summaries (IDs, titles, symbols, created time, report status, data mode).",
        annotations=LOCAL_READ,
    )
    async def list_saved_briefs(
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        symbol: SymbolArg | None = None,
    ) -> Annotated[CallToolResult, ToolResult[SavedBriefListData]]:
        return await run_tool(
            SERVER_ID,
            "list_saved_briefs",
            lambda: ListSavedBriefsInput(limit=limit, symbol=symbol),
            service.list_saved_briefs,
        )

    @mcp.tool(
        name="get_saved_brief",
        title="Get saved brief",
        description="One saved brief snapshot with its source references.",
        annotations=LOCAL_READ,
    )
    async def get_saved_brief(brief_id: BriefIdArg) -> Annotated[CallToolResult, ToolResult[SavedBriefData]]:
        return await run_tool(
            SERVER_ID, "get_saved_brief", lambda: GetSavedBriefInput(brief_id=brief_id), service.get_saved_brief
        )

    @mcp.tool(
        name="create_pending_action",
        title="Propose a confirmed write",
        description="Record a pending action for the UI confirmation panel. It does not perform the write. "
        "The payload hash is recomputed from server-side state and must match.",
        annotations=PROPOSAL,
    )
    async def create_pending_action(
        action_type: ActionTypeArg,
        payload_summary: PayloadSummary,
        payload_hash: PayloadHashArg,
        session_id: SessionArg,
        run_id: RunIdArg | None = None,
        origin: Literal["ui", "voice", "agent"] = "ui",
    ) -> Annotated[CallToolResult, ToolResult[PendingActionData]]:
        return await run_tool(
            SERVER_ID,
            "create_pending_action",
            lambda: CreatePendingActionInput(
                action_type=action_type,
                run_id=run_id,
                payload_summary=payload_summary,
                payload_hash=payload_hash,
                session_id=session_id,
                origin=origin,
            ),
            service.create_pending_action,
        )

    return mcp


def main() -> None:
    settings = server_settings(SERVER_ID)
    configure_server_logging(settings, SERVER_ID)
    create_server(build_workspace_service(settings)).run()


if __name__ == "__main__":
    main()
