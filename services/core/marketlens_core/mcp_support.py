"""Helpers shared by the three MCP server processes.

Tool results use the MCP_TOOLS §2 envelope as ``structured_content`` and set ``is_error``
when ``ok`` is false, so MCP clients see a tool error *and* the stable error code. Input
schema violations never reach a tool body: the SDK rejects them first. (Verified against
the installed MCP Python SDK 2.3.0; recorded in MCP_TOOLS.md §0.)
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError

from marketlens_core.config import ConfigError, Settings, load_settings, validate_server_settings
from marketlens_core.tool_models import ToolInput, ToolResult, execute_tool, validation_error

# Read-only tools that may reach an external provider (in live mode).
PROVIDER_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Read-only tools over app-local data.
LOCAL_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# Confirmed, idempotent app-local writes (grant required).
CONFIRMED_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
CONFIRMED_REMOVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
# Records a proposal only; never writes the requested artifact.
PROPOSAL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def to_call_tool_result(result: ToolResult) -> CallToolResult:
    payload = result.model_dump(mode="json")
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return CallToolResult(
        content=[TextContent(type="text", text=text)], structured_content=payload, is_error=not result.ok
    )


def server_settings(server_id: str) -> Settings:
    """Load this server's settings from its (minimal) environment, or exit with guidance."""
    try:
        settings = load_settings(validate=False)
    except ConfigError as exc:
        print(f"[{server_id}] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    problems = validate_server_settings(settings, server_id)
    if problems:
        print(f"[{server_id}] invalid configuration:\n- " + "\n- ".join(problems), file=sys.stderr)
        raise SystemExit(2)
    return settings


def configure_server_logging(settings: Settings, server_id: str) -> None:
    """Logs go to stderr (stdout carries the MCP protocol). Never log secrets or payloads."""
    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stderr,
        format=f"%(asctime)s %(levelname)s [{server_id}] %(name)s: %(message)s",
    )
    for noisy in ("httpx2", "httpcore2", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def run_tool(
    server_id: str,
    tool: str,
    build_input: Callable[[], ToolInput],
    operation: Callable[[Any], Awaitable[Any]],
) -> CallToolResult:
    """Validate arguments into the contract model, run the tool body, wrap the envelope."""

    async def body() -> Any:
        try:
            args = build_input()
        except ValidationError as exc:
            raise validation_error(exc) from exc
        return await operation(args)

    return to_call_tool_result(await execute_tool(server_id, tool, body))
