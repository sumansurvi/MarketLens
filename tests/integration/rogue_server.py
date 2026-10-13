"""A deliberately non-compliant MCP server used by the gateway fail-closed test.

It advertises the market_data tools *plus* a trading-like tool; the MarketLens gateway
must refuse to use it. Never imported by application code.
"""

from mcp.server import MCPServer

mcp = MCPServer("rogue-market-data")


@mcp.tool()
def get_quote(symbol: str) -> str:
    """Pretend quote."""
    return symbol


@mcp.tool()
def transfer_funds(amount: float) -> str:
    """A tool that must never be reachable from MarketLens."""
    return f"moved {amount}"


if __name__ == "__main__":
    mcp.run()
