"""Tool gateway contract: allowlist, call outcome type and safe audit records.

The only implementation is ``app.mcp.client_gateway.MCPClientGateway``, which reaches the
three MCP servers over stdio. (Phase 1's temporary in-process gateway was removed in
Phase 2, decision D-29, so production code cannot bypass the MCP protocol.)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from marketlens_core.errors import ErrorCode, ErrorInfo
from marketlens_core.hashing import new_id
from marketlens_core.models import ToolCallRecord
from marketlens_core.policy import PROHIBITED_TOOL_FRAGMENTS
from marketlens_core.timeutil import utc_now
from marketlens_core.tool_models import (
    ToolResult,
)

ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "market_data": frozenset(
        {"get_quote", "get_historical_prices", "get_financial_metrics", "calculate_price_change", "get_provider_status"}
    ),
    "research_sources": frozenset(
        {
            "resolve_symbol",
            "search_symbols",
            "search_company_filings",
            "get_sec_company_facts",
            "search_company_news",
            "get_filing_excerpt",
            "get_research_sources_status",
        }
    ),
    "research_workspace": frozenset(
        {
            "list_watchlist",
            "add_watchlist_symbol",
            "remove_watchlist_symbol",
            "save_research_brief",
            "list_saved_briefs",
            "get_saved_brief",
            "create_pending_action",
        }
    ),
}
WRITE_TOOLS = frozenset(
    {"add_watchlist_symbol", "remove_watchlist_symbol", "save_research_brief", "create_pending_action"}
)

# Defense in depth: the contract must never contain trading-like tool names. An explicit
# check (not ``assert``) so it also runs under ``python -O``.
_TRADING_LIKE = sorted(
    tool for tools in ALLOWED_TOOLS.values() for tool in tools if any(f in tool for f in PROHIBITED_TOOL_FRAGMENTS)
)
if _TRADING_LIKE:
    raise RuntimeError(f"trading-like tool names in the MCP allowlist: {_TRADING_LIKE}")


@dataclass
class ToolCallOutcome:
    result: ToolResult[Any]
    record: ToolCallRecord

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def data(self) -> Any:
        return self.result.data

    @property
    def error(self) -> ErrorInfo | None:
        return self.result.error


class ToolGateway(Protocol):
    transport: Literal["mcp_stdio", "in_process"]

    async def call(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
        seq: int | None = None,
    ) -> ToolCallOutcome: ...


def check_allowed(server_id: str, tool_name: str) -> ErrorInfo | None:
    if tool_name not in ALLOWED_TOOLS.get(server_id, frozenset()):
        return ErrorInfo(
            code=ErrorCode.MCP_TOOL_NOT_ALLOWED,
            message=f"Tool '{tool_name}' on server '{server_id}' is not in the MarketLens allowlist.",
        )
    return None


def summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Safe, bounded input summary for audit logs (grant/session values are masked)."""
    summary: dict[str, Any] = {}
    for key, value in list(arguments.items())[:12]:
        if key in ("confirmation_grant_id", "session_id"):
            summary[key] = "[redacted]"
        elif isinstance(value, str):
            summary[key] = value if len(value) <= 160 else value[:157] + "..."
        elif isinstance(value, (int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, list):
            summary[key] = [str(v)[:40] for v in value[:10]]
        else:
            summary[key] = str(value)[:80]
    return summary


def summarize_result(result: ToolResult[Any]) -> dict[str, Any]:
    if not result.ok:
        return {"ok": False, "error_code": result.error.code.value if result.error else None}
    data = result.data
    evidence = data.get("evidence", []) if isinstance(data, dict) else []
    return {
        "ok": True,
        "evidence_count": len(evidence),
        "is_fixture": result.meta.is_fixture,
        "warnings": len(result.meta.warnings),
    }


def build_record(
    *,
    server_id: str,
    tool_name: str,
    transport: Literal["mcp_stdio", "in_process"],
    arguments: dict[str, Any],
    result: ToolResult[Any],
    run_id: str | None,
    seq: int | None,
    started_monotonic: float,
    started_at: Any,
    idempotency_key: str | None = None,
) -> ToolCallRecord:
    return ToolCallRecord(
        call_id=new_id("call"),
        run_id=run_id,
        seq=seq,
        server_id=server_id,
        tool_name=tool_name,
        transport=transport,
        input_summary=summarize_arguments(arguments),
        status="ok" if result.ok else "error",
        error_code=result.error.code if result.error else None,
        latency_ms=int((time.monotonic() - started_monotonic) * 1000),
        output_summary=summarize_result(result),
        started_at=started_at,
        ended_at=utc_now(),
        idempotency_key=idempotency_key,
    )
