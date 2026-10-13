"""Frontend/backend contract: the committed OpenAPI schema (source of the generated
TypeScript types) must match the live FastAPI app, and the MCP tool allowlist must be
exactly the MCP_TOOLS.md contract."""

from __future__ import annotations

import re
import subprocess
import sys

from app.mcp.gateway import ALLOWED_TOOLS, WRITE_TOOLS
from marketlens_core.config import REPO_ROOT


def test_committed_openapi_schema_is_current() -> None:
    result = subprocess.run(  # noqa: S603 - fixed local script
        [sys.executable, str(REPO_ROOT / "scripts" / "export_openapi.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_typescript_types_exist_for_core_contracts() -> None:
    schema = (REPO_ROOT / "apps" / "web" / "lib" / "api" / "schema.d.ts").read_text()
    for name in ("ResearchBrief", "ResearchClaim", "EvidenceItem", "ResearchRunDetailResponse", "ErrorResponse"):
        assert f"        {name}: {{" in schema, name


def test_tool_allowlist_matches_mcp_tools_contract() -> None:
    contract = (REPO_ROOT / "MCP_TOOLS.md").read_text()
    documented = set(re.findall(r"^### Tool [ABC]\d: `([a-z_]+)`", contract, flags=re.MULTILINE))
    allowlisted = {tool for tools in ALLOWED_TOOLS.values() for tool in tools}
    assert documented == allowlisted
    assert WRITE_TOOLS <= ALLOWED_TOOLS["research_workspace"]
    assert not any(t in ALLOWED_TOOLS["market_data"] | ALLOWED_TOOLS["research_sources"] for t in WRITE_TOOLS)
