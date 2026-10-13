"""Market Data MCP server (server ID ``market_data``) — read-only.

Run as its own process over stdio: ``python -m mcp_market_data``. The backend's MCP
client gateway launches it with an explicit, minimal environment. Tools return the
MCP_TOOLS §2 envelope as structured content; failures set ``is_error``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from marketlens_core import __version__
from marketlens_core.mcp_support import PROVIDER_READ, configure_server_logging, run_tool, server_settings
from marketlens_core.tool_models import (
    DateArg,
    EmptyInput,
    FinancialMetricsData,
    FinancialMetricsInput,
    HistoricalPricesData,
    HistoricalPricesInput,
    MetricName,
    PeriodType,
    PriceChangeData,
    PriceChangeInput,
    PriceField,
    QuoteData,
    ServerStatusData,
    SymbolArg,
    SymbolInput,
    ToolResult,
)
from mcp.server import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from mcp_market_data.service import SERVER_ID, MarketDataService, build_market_data_service

INSTRUCTIONS = (
    "Read-only US equity market data for MarketLens. Every result carries provenance (provider, observation "
    "dates, retrieval time, evidence IDs) and is_fixture labeling. Values marked is_fixture are synthetic demo "
    "data, never current prices. This server cannot trade, place orders or access brokerage accounts."
)


def create_server(service: MarketDataService) -> MCPServer:
    mcp = MCPServer(
        "marketlens-market-data", title="MarketLens Market Data", instructions=INSTRUCTIONS, version=__version__
    )

    @mcp.tool(
        name="get_quote",
        title="Latest provider quote",
        description="Latest quote or most recent provider-supported observation for a US equity. Returns the "
        "trading date the provider reports; no intraday time or market state is inferred.",
        annotations=PROVIDER_READ,
    )
    async def get_quote(symbol: SymbolArg) -> Annotated[CallToolResult, ToolResult[QuoteData]]:
        return await run_tool(SERVER_ID, "get_quote", lambda: SymbolInput(symbol=symbol), service.get_quote)

    @mcp.tool(
        name="get_historical_prices",
        title="Daily price history",
        description="Daily OHLCV bars between start_date and end_date inclusive (max 366 days), keyed by "
        "exchange trading date. 'adjustment' reports what was actually returned, not just what was requested.",
        annotations=PROVIDER_READ,
    )
    async def get_historical_prices(
        symbol: SymbolArg,
        start_date: DateArg,
        end_date: DateArg,
        interval: Literal["daily"] = "daily",
        adjusted: bool = False,
    ) -> Annotated[CallToolResult, ToolResult[HistoricalPricesData]]:
        return await run_tool(
            SERVER_ID,
            "get_historical_prices",
            lambda: HistoricalPricesInput(
                symbol=symbol, interval=interval, start_date=start_date, end_date=end_date, adjusted=adjusted
            ),
            service.get_historical_prices,
        )

    @mcp.tool(
        name="get_financial_metrics",
        title="Provider-normalized fundamentals",
        description="Allowlisted financial metrics (annual or quarterly) from the market-data provider. Returns "
        "CAPABILITY_UNAVAILABLE when the plan lacks the endpoint; SEC XBRL facts are authoritative.",
        annotations=PROVIDER_READ,
    )
    async def get_financial_metrics(
        symbol: SymbolArg,
        metrics: Annotated[list[MetricName], Field(min_length=1, max_length=8)],
        period_type: PeriodType = "quarterly",
        limit: Annotated[int, Field(ge=1, le=12)] = 4,
    ) -> Annotated[CallToolResult, ToolResult[FinancialMetricsData]]:
        return await run_tool(
            SERVER_ID,
            "get_financial_metrics",
            lambda: FinancialMetricsInput(symbol=symbol, metrics=metrics, period_type=period_type, limit=limit),
            service.get_financial_metrics,
        )

    @mcp.tool(
        name="calculate_price_change",
        title="Deterministic price change",
        description="Percentage change from the first observation on/after start_date to the last on/before "
        "end_date: (end_value - start_value) / start_value * 100. Returns the dates actually used and the input "
        "evidence. Calculates movement only; it never attributes causes.",
        annotations=PROVIDER_READ,
    )
    async def calculate_price_change(
        symbol: SymbolArg,
        start_date: DateArg,
        end_date: DateArg,
        price_field: PriceField = "close",
    ) -> Annotated[CallToolResult, ToolResult[PriceChangeData]]:
        return await run_tool(
            SERVER_ID,
            "calculate_price_change",
            lambda: PriceChangeInput(symbol=symbol, start_date=start_date, end_date=end_date, price_field=price_field),
            service.calculate_price_change,
        )

    @mcp.tool(
        name="get_provider_status",
        title="Market-data provider status",
        description="Configured state (boolean only), data mode, capabilities, last successful retrieval and "
        "rate-limit state. Never returns secrets.",
        annotations=PROVIDER_READ,
    )
    async def get_provider_status() -> Annotated[CallToolResult, ToolResult[ServerStatusData]]:
        return await run_tool(SERVER_ID, "get_provider_status", EmptyInput, lambda _args: service.get_provider_status())

    return mcp


def main() -> None:
    settings = server_settings(SERVER_ID)
    configure_server_logging(settings, SERVER_ID)
    create_server(build_market_data_service(settings)).run()


if __name__ == "__main__":
    main()
