"""Market Data Specialist: observed price/volume behavior from quotes, series and calculations.

Scope: market_movement only. Cannot explain causes, forecast, or infer market state.
"""

from __future__ import annotations

from marketlens_core.models import BriefSection

from app.agents import fixture_responses
from app.agents.prompts import MARKET_SYSTEM, market_package
from app.agents.specialist_base import SpecialistSpec

SPEC = SpecialistSpec(
    task="market_specialist",
    agent="market_data_specialist",
    name="market_data",
    system_prompt=MARKET_SYSTEM,
    sections=frozenset({BriefSection.MARKET_MOVEMENT}),
    build_package=market_package,
    deterministic=fixture_responses.market_specialist,
)
