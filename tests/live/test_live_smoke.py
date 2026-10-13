"""Credential-gated smoke tests; never part of the deterministic suite.

Run with ``MARKETLENS_LIVE_TESTS=1 uv run pytest -m live tests/live -s``. Credentials come
from the environment or the repository ``.env``. Each test makes real calls: one research run
uses about five LLM calls (model test; free with a local OpenAI-compatible model) or about three
Alpha Vantage requests plus a few SEC requests (live-data test; counts against the free key's
daily quota). Each test uses its own temporary database, so do not run the live-data test while
the app is also fetching from Alpha Vantage. Results are printed so they can be recorded in
PROJECT_STATUS.md; nothing here asserts on model wording, only on integrity rules.
"""

from __future__ import annotations

import json

import pytest
from app.main import create_app
from marketlens_core.config import Settings, validate_settings
from starlette.testclient import TestClient

from tests.helpers import wait_for_run

pytestmark = pytest.mark.live
QUESTION = "Research AAPL over the last 3 months: price movement, news, filings, risks and unknowns."


def configured(live_config: dict[str, object], tmp_path, **overrides: object) -> Settings:
    settings = Settings(_env_file=None, **{**live_config, **overrides, "database_url": f"sqlite:///{tmp_path}/live.db"})
    problems = validate_settings(settings)
    if problems:
        pytest.skip("not configured: " + " ".join(problems))
    return settings


def run_once(settings: Settings, symbol: str = "AAPL") -> dict:
    with TestClient(create_app(settings), headers={"X-MarketLens-Client": "live-smoke"}) as client:
        created = client.post("/api/research", json={"symbol": symbol, "question": QUESTION})
        assert created.status_code == 202, created.text
        return wait_for_run(client, created.json()["run"]["run_id"], timeout=settings.run_timeout_seconds + 30)


def assert_integrity(detail: dict) -> None:
    run, brief = detail["run"], detail["brief"]
    assert run["status"] in ("completed", "partial"), run["errors"]
    evidence = {e["evidence_id"] for e in detail["evidence"]}
    for claim in brief["claims"]:
        assert claim["evidence_ids"] and set(claim["evidence_ids"]) <= evidence, claim
        assert claim["validation_status"] in ("supported", "needs_qualification"), claim


def llm_calls(detail: dict) -> list[dict]:
    """Per-call records from the agent steps (the finalize step stores only a total count)."""
    calls: list[dict] = []
    for step in detail["steps"]:
        value = (step.get("detail") or {}).get("llm_calls")
        if isinstance(value, list):
            calls.extend(c for c in value if isinstance(c, dict))
    return calls


def summary(detail: dict) -> str:
    brief = detail["brief"]
    calls = llm_calls(detail)
    return json.dumps(
        {
            "status": detail["run"]["status"],
            "errors": [e["code"] for e in detail["run"]["errors"]],
            "claims": len(brief["claims"]),
            "withheld": len(brief["withheld_claims"]),
            "numeric_checks_failed": brief["review"]["numeric_checks_failed"],
            "llm_calls": len(calls),
            "llm_failures": [c["error_code"] for c in calls if c["status"] != "ok"],
            "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
            "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
            "data_mode": brief["data_mode"],
            "llm": f"{brief['methodology']['llm_mode']} {brief['methodology']['llm_model']}",
        }
    )


def test_openai_agents_produce_a_validated_brief_from_fixture_data(live_config, tmp_path) -> None:
    settings = configured(live_config, tmp_path, app_mode="live", data_mode="fixtures", llm_mode="openai")
    detail = run_once(settings)
    print("\nOPENAI SMOKE", summary(detail))
    assert_integrity(detail)
    methodology = detail["brief"]["methodology"]
    assert methodology["llm_mode"] == "openai" and methodology["llm_model"] == settings.openai_model
    specialist_steps = [s for s in detail["steps"] if s["step_key"].endswith("_specialist")]
    assert len(specialist_steps) == 3
    assert detail["brief"]["is_fixture"] is True  # fixture data stays labeled even with a real model


def test_live_providers_produce_a_labeled_live_brief(live_config, tmp_path) -> None:
    settings = configured(live_config, tmp_path, app_mode="live", data_mode="live", llm_mode="fixture")
    detail = run_once(settings)
    print("\nLIVE DATA SMOKE", summary(detail))
    assert_integrity(detail)
    sourced = [e for e in detail["evidence"] if e["kind"] != "calculation"]
    assert sourced and all(e["source_mode"] == "live" and not e["is_fixture"] for e in sourced)
    assert detail["brief"]["is_fixture"] is False and detail["brief"]["fixture_disclosure"] is None
