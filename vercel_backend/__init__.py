"""Entry points for the backend on Vercel (D-98): the ASGI app and the research-run queue consumer.

Vercel loads these modules from the repository root (see ``[tool.vercel]`` in ``pyproject.toml``
and the ``backend`` service in ``vercel.json``). Importing this package first makes the backend's
packages importable from their source folders, in case the build installed them elsewhere, and
sets defaults that suit a Vercel Function: no ``.env`` file, and MCP server logs on stderr (where
Vercel's runtime logs come from) because only ``/tmp`` is writable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (
    "services/core",
    "services/backend",
    "services/mcp_market_data",
    "services/mcp_research_sources",
    "services/mcp_research_workspace",
)

for _relative in SOURCES:
    _path = str(ROOT / _relative)
    if _path not in sys.path:
        sys.path.append(_path)

os.environ.setdefault("MARKETLENS_ROOT", str(ROOT))
os.environ.setdefault("MARKETLENS_DOTENV", "disabled")
os.environ.setdefault("MCP_LOG_DIR", "-")
# A Vercel Function can't run work after answering, so runs go through Vercel Queues; the site is
# served over https, so the session cookie is Secure.
os.environ.setdefault("RUN_EXECUTION", "queue")
os.environ.setdefault("APP_ENV", "production")
# The Neon integration from the Vercel Marketplace sets DATABASE_URL; accept POSTGRES_URL too.
if not os.environ.get("DATABASE_URL") and os.environ.get("POSTGRES_URL"):
    os.environ["DATABASE_URL"] = os.environ["POSTGRES_URL"]
