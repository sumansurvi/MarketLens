"""MCP client gateway — the backend's only path to MarketLens tools (real MCP over stdio).

Each MCP server runs as its own child process, launched with an explicit, minimal,
per-server environment (decision D-20): the workspace server never receives provider
keys, and no server reads ``.env``. A supervisor task per server owns the MCP client
session, restarts a dead server with bounded backoff, and refuses to use a server that
advertises any tool outside the contract allowlist (fail closed).

Per call: allowlist check → argument validation against the server's advertised input
schema → ``tools/call`` with a finite timeout → translation of MCP errors and the
MCP_TOOLS §2 envelope into a ``ToolResult``. Writes are never retried automatically.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from marketlens_core import __version__
from marketlens_core.config import REPO_ROOT, Settings, mcp_log_directory
from marketlens_core.errors import ErrorCode, ErrorInfo
from marketlens_core.hashing import new_id
from marketlens_core.timeutil import utc_now
from marketlens_core.tool_models import ToolMeta, ToolResult
from mcp import Client, MCPError, StdioServerParameters, stdio_client
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT, CallToolResult, Implementation, TextContent, Tool
from pydantic import ValidationError

from app.mcp.gateway import ALLOWED_TOOLS, WRITE_TOOLS, ToolCallOutcome, build_record

logger = logging.getLogger("marketlens.mcp")

SERVER_MODULES = {
    "market_data": "mcp_market_data",
    "research_sources": "mcp_research_sources",
    "research_workspace": "mcp_research_workspace",
}
STARTUP_TIMEOUT_SECONDS = 20.0
MAX_RESTART_DELAY_SECONDS = 8.0


def server_environment(settings: Settings, server_id: str) -> dict[str, str]:
    """Explicit environment for one server process: only the settings it needs."""
    env = {
        "MARKETLENS_DOTENV": "disabled",
        "MARKETLENS_ROOT": str(REPO_ROOT),
        "PYTHONUNBUFFERED": "1",
        "APP_ENV": settings.app_env,
        "APP_MODE": settings.app_mode,
        "DATA_MODE": settings.data_mode,
        "LOG_LEVEL": settings.log_level,
        "FIXTURE_DIR": str(settings.fixture_root),
        "DATABASE_URL": settings.database_url
        if settings.database_is_postgres
        else f"sqlite:///{settings.database_path}",
    }
    if server_id in ("market_data", "research_sources"):
        env.update(
            {
                "ALPHA_VANTAGE_BASE_URL": settings.alpha_vantage_base_url,
                "MARKET_DATA_TIMEOUT_SECONDS": str(settings.market_data_timeout_seconds),
                "MARKET_DATA_CACHE_TTL_SECONDS": str(settings.market_data_cache_ttl_seconds),
                "MARKET_DATA_QUOTE_CACHE_TTL_SECONDS": str(settings.market_data_quote_cache_ttl_seconds),
                "MARKET_DATA_MAX_RETRIES": str(settings.market_data_max_retries),
                "ALPHA_VANTAGE_MAX_REQUESTS_PER_MINUTE": str(settings.alpha_vantage_max_requests_per_minute),
            }
        )
        if settings.alpha_vantage_api_key:
            env["ALPHA_VANTAGE_API_KEY"] = settings.alpha_vantage_api_key.get_secret_value()
    if server_id == "research_sources":
        env.update(
            {
                "SEC_TIMEOUT_SECONDS": str(settings.sec_timeout_seconds),
                "SEC_CACHE_TTL_SECONDS": str(settings.sec_cache_ttl_seconds),
                "SEC_MAX_CONCURRENT_REQUESTS": str(settings.sec_max_concurrent_requests),
                "SEC_MAX_REQUESTS_PER_SECOND": str(settings.sec_max_requests_per_second),
                "SEC_DATA_BASE_URL": settings.sec_data_base_url,
                "SEC_WWW_BASE_URL": settings.sec_www_base_url,
                "NEWS_FALLBACK": settings.news_fallback,
                "YAHOO_NEWS_BASE_URL": settings.yahoo_news_base_url,
            }
        )
        if settings.sec_user_agent:
            env["SEC_USER_AGENT"] = settings.sec_user_agent
    if server_id == "research_workspace":
        env["ACTION_CONFIRMATION_TTL_SECONDS"] = str(settings.action_confirmation_ttl_seconds)
    if os.environ.get("VERCEL"):
        # In a Vercel Function the installed packages are found through the parent's import
        # path, not a virtual environment, so the child servers get the same path (D-98).
        env["PYTHONPATH"] = os.pathsep.join(_import_paths())
    return env


def _import_paths() -> list[str]:
    """The parent's import path without the standard library (each server has its own)."""
    stdlib = os.path.realpath(sys.base_prefix)
    return [
        entry for entry in sys.path if entry and os.path.isdir(entry) and not os.path.realpath(entry).startswith(stdlib)
    ]


class ServerUnavailable(Exception):
    pass


def _describe(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe(e) for e in exc.exceptions)[:300]
    return f"{type(exc).__name__}: {exc}"[:300]


class ServerConnection:
    """Owns one MCP server process and its client session (supervised, restartable)."""

    def __init__(
        self, server_id: str, params: StdioServerParameters, *, read_timeout: float, log_path: Path | None
    ) -> None:
        self.server_id = server_id
        self.params = params
        self.read_timeout = read_timeout
        self.log_path = log_path
        self.tools: dict[str, Tool] = {}
        self.restarts = 0
        self.last_error: str | None = None
        self._validators: dict[str, Draft202012Validator] = {}
        self._client: Client | None = None
        self._ready = asyncio.Event()
        self._wake = asyncio.Event()
        self._closing = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._supervise(), name=f"mcp:{self.server_id}")

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def _install_tools(self, tools: list[Tool]) -> None:
        names = {t.name for t in tools}
        allowed = ALLOWED_TOOLS[self.server_id]
        unexpected = sorted(names - allowed)
        if unexpected:
            raise RuntimeError(f"server advertised tools outside the allowlist: {unexpected}")
        missing = sorted(allowed - names)
        if missing:
            logger.warning("MCP server %s does not advertise expected tools %s", self.server_id, missing)
        self.tools = {t.name: t for t in tools}
        self._validators = {t.name: Draft202012Validator(t.input_schema) for t in tools}

    async def _supervise(self) -> None:
        delay = 0.25
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        while not self._closing:
            self._wake.clear()
            # No log file (MCP_LOG_DIR=-): server logs go to this process's stderr, which is
            # where Vercel's runtime logs come from.
            log = self.log_path.open("a", encoding="utf-8") if self.log_path is not None else sys.stderr
            try:
                async with Client(
                    stdio_client(self.params, errlog=log),
                    read_timeout_seconds=self.read_timeout,
                    cache=None,
                    client_info=Implementation(name="marketlens-backend", version=__version__),
                ) as client:
                    listed = await client.list_tools()
                    self._install_tools(listed.tools)
                    self._client = client
                    self.last_error = None
                    delay = 0.25
                    self._ready.set()
                    logger.info("MCP server %s ready (%d tools)", self.server_id, len(self.tools))
                    await self._wake.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - supervisor boundary: record and restart
                self.last_error = _describe(exc)
                logger.warning("MCP server %s stopped: %s", self.server_id, self.last_error)
            finally:
                self._ready.clear()
                self._client = None
                if log is not sys.stderr:
                    log.close()
            if self._closing:
                break
            self.restarts += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RESTART_DELAY_SECONDS)

    def request_restart(self) -> None:
        self._wake.set()

    async def wait_ready(self, wait_seconds: float) -> None:
        self.start()
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=wait_seconds)
        except TimeoutError as exc:
            raise ServerUnavailable(self.last_error or "server did not become ready") from exc

    def validation_problems(self, tool: str, arguments: dict[str, Any]) -> list[dict[str, str]]:
        validator = self._validators.get(tool)
        if validator is None:
            return []
        problems = []
        for error in sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))[:10]:
            problems.append(
                {"field": ".".join(str(p) for p in error.path) or "(arguments)", "issue": error.message[:200]}
            )
        return problems

    async def call(self, tool: str, arguments: dict[str, Any], timeout_seconds: float) -> CallToolResult:
        client = self._client
        if client is None:
            raise ServerUnavailable(self.last_error or "server not connected")
        return await client.call_tool(tool, arguments, read_timeout_seconds=timeout_seconds)

    async def close(self) -> None:
        self._closing = True
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()


def translate_result(server_id: str, tool: str, raw: CallToolResult) -> ToolResult[Any]:
    """Turn an MCP CallToolResult into the MCP_TOOLS §2 envelope, validating its shape."""
    content = raw.structured_content
    if isinstance(content, dict) and "ok" in content and "meta" in content:
        try:
            envelope = ToolResult[Any].model_validate(content)
        except ValidationError:
            return _error_result(
                server_id,
                tool,
                ErrorCode.SCHEMA_VALIDATION_FAILED,
                "The tool returned an envelope that failed validation.",
            )
        if envelope.meta.server != server_id or envelope.meta.tool != tool or envelope.ok == raw.is_error:
            return _error_result(
                server_id,
                tool,
                ErrorCode.SCHEMA_VALIDATION_FAILED,
                "The tool returned an inconsistent result envelope.",
            )
        return envelope
    text = next((b.text for b in raw.content if isinstance(b, TextContent)), "")
    if raw.is_error:
        if "validation error" in text or "rejected arguments" in text:
            return _error_result(
                server_id,
                tool,
                ErrorCode.INVALID_INPUT,
                "The tool rejected the arguments.",
                details={"tool_message": text[:300]},
            )
        if "Unknown tool" in text:
            return _error_result(
                server_id, tool, ErrorCode.MCP_TOOL_NOT_ALLOWED, "The server does not offer that tool."
            )
        return _error_result(server_id, tool, ErrorCode.INTERNAL_ERROR, "The tool failed unexpectedly.")
    return _error_result(server_id, tool, ErrorCode.SCHEMA_VALIDATION_FAILED, "The tool returned no structured result.")


def _error_result(
    server_id: str,
    tool: str,
    code: ErrorCode,
    message: str,
    *,
    retryable: bool | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult[Any]:
    info = ErrorInfo(
        code=code,
        message=message,
        retryable=bool(retryable)
        if retryable is not None
        else code in (ErrorCode.MCP_SERVER_UNAVAILABLE, ErrorCode.PROVIDER_TIMEOUT, ErrorCode.RATE_LIMITED),
        details=details or {},
    )
    return ToolResult(ok=False, error=info, meta=ToolMeta(request_id=new_id("req"), server=server_id, tool=tool))


class MCPClientGateway:
    transport: Literal["mcp_stdio"] = "mcp_stdio"

    def __init__(self, settings: Settings, *, log_dir: Path | None = None) -> None:
        self.settings = settings
        # Tags child command lines (ignored by the servers) so operators/tests can find them.
        self.instance_id = new_id("mcpgw")
        logs = log_dir or mcp_log_directory(settings)
        self.connections = {
            server_id: ServerConnection(
                server_id,
                StdioServerParameters(
                    command=sys.executable,
                    args=["-m", module, "--instance", self.instance_id],
                    env=server_environment(settings, server_id),
                    cwd=str(REPO_ROOT),
                ),
                read_timeout=settings.mcp_tool_call_timeout_seconds,
                log_path=logs / f"mcp_{server_id}.log" if logs is not None else None,
            )
            for server_id, module in SERVER_MODULES.items()
        }

    async def start(self, wait_seconds: float = STARTUP_TIMEOUT_SECONDS) -> None:
        for connection in self.connections.values():
            connection.start()
        results = await asyncio.gather(
            *(c.wait_ready(wait_seconds) for c in self.connections.values()), return_exceptions=True
        )
        for (server_id, _), result in zip(self.connections.items(), results, strict=True):
            if isinstance(result, Exception):
                logger.error("MCP server %s unavailable at startup: %s", server_id, result)

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            server_id: {
                "ready": c.ready,
                "tools": sorted(c.tools),
                "restarts": c.restarts,
                "last_error": c.last_error,
            }
            for server_id, c in self.connections.items()
        }

    async def call(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
        seq: int | None = None,
    ) -> ToolCallOutcome:
        started, started_at = time.monotonic(), utc_now()
        result = await self._invoke(server_id, tool_name, dict(arguments))
        record = build_record(
            server_id=server_id,
            tool_name=tool_name,
            transport=self.transport,
            arguments=arguments,
            result=result,
            run_id=run_id,
            seq=seq,
            started_monotonic=started,
            started_at=started_at,
        )
        return ToolCallOutcome(result=result, record=record)

    async def _invoke(self, server_id: str, tool: str, arguments: dict[str, Any]) -> ToolResult[Any]:
        if tool not in ALLOWED_TOOLS.get(server_id, frozenset()):
            return _error_result(
                server_id,
                tool,
                ErrorCode.MCP_TOOL_NOT_ALLOWED,
                f"Tool '{tool}' on server '{server_id}' is not in the MarketLens allowlist.",
            )
        connection = self.connections[server_id]
        try:
            await connection.wait_ready(STARTUP_TIMEOUT_SECONDS)
        except ServerUnavailable as exc:
            return _error_result(
                server_id,
                tool,
                ErrorCode.MCP_SERVER_UNAVAILABLE,
                f"The {server_id} MCP server is unavailable.",
                details={"reason": str(exc)[:200]},
            )
        if tool not in connection.tools:
            return _error_result(
                server_id, tool, ErrorCode.MCP_TOOL_NOT_ALLOWED, f"The {server_id} server does not advertise '{tool}'."
            )
        problems = connection.validation_problems(tool, arguments)
        if problems:
            return _error_result(
                server_id,
                tool,
                ErrorCode.INVALID_INPUT,
                "The tool arguments are invalid.",
                details={"fields": problems},
            )
        timeout = self.settings.mcp_tool_call_timeout_seconds
        try:
            raw = await asyncio.wait_for(connection.call(tool, arguments, timeout), timeout=timeout + 2)
        except TimeoutError:
            return self._timeout(server_id, tool, timeout)
        except MCPError as exc:
            if exc.code == REQUEST_TIMEOUT:
                return self._timeout(server_id, tool, timeout)
            if exc.code == CONNECTION_CLOSED:
                connection.request_restart()
                return _error_result(
                    server_id, tool, ErrorCode.MCP_SERVER_UNAVAILABLE, f"The {server_id} MCP server connection closed."
                )
            return _error_result(
                server_id,
                tool,
                ErrorCode.INTERNAL_ERROR,
                "The MCP server returned a protocol error.",
                details={"mcp_code": exc.code},
            )
        except ServerUnavailable as exc:
            return _error_result(
                server_id,
                tool,
                ErrorCode.MCP_SERVER_UNAVAILABLE,
                f"The {server_id} MCP server is unavailable.",
                details={"reason": str(exc)[:200]},
            )
        except Exception as exc:  # noqa: BLE001 - transport failure: restart the server, report safely
            logger.warning("MCP call %s.%s failed: %s", server_id, tool, _describe(exc))
            connection.request_restart()
            return _error_result(
                server_id, tool, ErrorCode.MCP_SERVER_UNAVAILABLE, f"The {server_id} MCP server failed during the call."
            )
        return translate_result(server_id, tool, raw)

    @staticmethod
    def _timeout(server_id: str, tool: str, timeout: float) -> ToolResult[Any]:
        note = " The write may or may not have completed; it was not retried." if tool in WRITE_TOOLS else ""
        return _error_result(
            server_id,
            tool,
            ErrorCode.PROVIDER_TIMEOUT,
            f"The {server_id} tool '{tool}' did not respond within {timeout:g} seconds.{note}",
        )

    async def aclose(self) -> None:
        await asyncio.gather(*(c.close() for c in self.connections.values()), return_exceptions=True)
