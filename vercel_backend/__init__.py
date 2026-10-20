"""Entry points for the backend on Vercel (D-98): the ASGI app and the research-run queue consumer.

Vercel loads these modules from the repository root (see ``[tool.vercel]`` in ``pyproject.toml``
and the ``backend`` service in ``vercel.json``). Importing this package first makes the backend's
packages importable from their source folders, in case the build installed them elsewhere,
and marks the process as running on Vercel.

The Vercel settings themselves (no ``.env``, MCP server logs on stderr, research through Vercel
Queues, ``APP_ENV=production``, ``POSTGRES_URL`` accepted as ``DATABASE_URL``) are enforced by
``marketlens_core.config.load_settings`` whenever it runs on Vercel, whichever module loads the
app (D-101). They used to be ``setdefault`` calls here, which project environment variables
copied from ``.env.example`` silently overrode.
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
# This package is only loaded as the Vercel entry point (or in a local rehearsal of it,
# docs/TESTING_GUIDE.md §7), so it marks the process as running on Vercel even when the
# project hides Vercel's system environment variables. load_settings then applies the profile.
os.environ.setdefault("VERCEL", "1")
