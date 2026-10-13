"""Seed the local workspace with demo research (synthetic fixtures only). Idempotent.

Usage: ``uv run python scripts/seed_demo.py``

What it does, through the same API and MCP servers the app uses (nothing is written directly to
the database, and every write goes through the server-issued confirmation grant flow):

1. Runs research for AAPL, NVDA and MSFT unless a completed or partial run already exists.
2. Saves the AAPL brief unless a saved brief for AAPL already exists (API only; the web app no longer shows saved briefs).
3. Adds NVDA and MSFT to the watchlist (re-adding is a no-op; API only, like saved briefs).

It refuses to run unless DATA_MODE=fixtures and LLM_MODE=fixture, so it can never call a live
provider or model or mix synthetic data into live research. Safe to run again at any time.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from marketlens_core.config import ConfigError, load_settings  # noqa: E402

DEMO = [
    (
        "AAPL",
        "Research AAPL over the last 3 months; explain notable movement; review recent news and latest available filings; list risks and unknowns.",
    ),
    ("NVDA", "Give me a research briefing on Nvidia and explain what changed recently."),
    (
        "MSFT",
        "Review Microsoft's latest reported fundamentals and filings, and state clearly what evidence is missing.",
    ),
]
WATCHLIST = ("NVDA", "MSFT")
HEADERS = {"X-MarketLens-Client": "seed-demo"}


def _wait(client, run_id: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/research/{run_id}").json()
        if detail["run"]["status"] not in ("queued", "running") and not detail.get("is_active"):
            return detail
        time.sleep(0.2)
    raise TimeoutError(f"research run {run_id} did not finish in {timeout:.0f}s")


def _confirm(client, proposal: dict) -> dict:
    action = proposal["action"]
    confirmed = client.post(f"/api/actions/{action['action_id']}/confirm")
    confirmed.raise_for_status()
    return confirmed.json()


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if settings.data_mode != "fixtures" or settings.llm_mode != "fixture":
        print(
            "seed_demo.py only seeds synthetic demo data: set DATA_MODE=fixtures and LLM_MODE=fixture "
            f"(current: DATA_MODE={settings.data_mode}, LLM_MODE={settings.llm_mode}).",
            file=sys.stderr,
        )
        return 2
    from app.main import create_app
    from starlette.testclient import TestClient

    print(f"Seeding demo data into {settings.database_path} (synthetic fixtures; no network calls).")
    with TestClient(create_app(settings), headers=HEADERS) as client:
        listed = client.get("/api/research", params={"limit": 100})
        listed.raise_for_status()
        runs = listed.json()["runs"]
        latest: dict[str, str] = {}
        for run in runs:  # newest first
            if run["status"] in ("completed", "partial") and run["symbol"] not in latest:
                latest[run["symbol"]] = run["run_id"]
        for symbol, question in DEMO:
            if symbol in latest:
                print(f"  research {symbol}: already present ({latest[symbol]})")
                continue
            created = client.post("/api/research", json={"symbol": symbol, "question": question})
            created.raise_for_status()
            detail = _wait(client, created.json()["run"]["run_id"])
            latest[symbol] = detail["run"]["run_id"]
            print(f"  research {symbol}: {detail['run']['status']} ({latest[symbol]})")

        listed = client.get("/api/briefs", params={"limit": 50})
        listed.raise_for_status()
        briefs = listed.json()["briefs"]
        if any(b["symbol"] == "AAPL" for b in briefs):
            print("  saved brief AAPL: already present")
        else:
            proposal = client.post(
                "/api/actions", json={"action_type": "save_research_brief", "run_id": latest["AAPL"]}
            )
            proposal.raise_for_status()
            result = _confirm(client, proposal.json())["result"]
            print(f"  saved brief AAPL: {result['brief_id']}")

        present = {item["symbol"] for item in client.get("/api/watchlist").json()["items"]}
        for symbol in WATCHLIST:
            if symbol in present:
                print(f"  watchlist {symbol}: already present")
                continue
            proposal = client.post("/api/watchlist", json={"symbol": symbol})
            proposal.raise_for_status()
            _confirm(client, proposal.json())
            print(f"  watchlist {symbol}: added")
    print(
        "Done. Start the app and open http://localhost:3000 — the runs are in the research history "
        "(the saved brief and watchlist are available through the API only)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
