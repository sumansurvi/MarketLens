#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema used to generate the web client's TypeScript types.

Usage:
    uv run python scripts/export_openapi.py            # write apps/web/openapi.json
    uv run python scripts/export_openapi.py --check    # fail if the committed schema is stale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MARKETLENS_DOTENV", "disabled")

from app.main import create_app  # noqa: E402
from marketlens_core.config import REPO_ROOT, Settings  # noqa: E402

TARGET = REPO_ROOT / "apps" / "web" / "openapi.json"


def render() -> str:
    app = create_app(Settings(_env_file=None))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--target", type=Path, default=TARGET)
    args = parser.parse_args()
    schema = render()
    if args.check:
        current = args.target.read_text(encoding="utf-8") if args.target.exists() else ""
        if current != schema:
            print(
                f"{args.target} is stale; run: uv run python scripts/export_openapi.py && pnpm --dir apps/web gen:api"
            )
            return 1
        print("openapi schema current")
        return 0
    args.target.write_text(schema, encoding="utf-8")
    print(f"wrote {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
