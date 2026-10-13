"""Fundamentals Specialist: period-labelled financial metrics, filings and quoted risk factors.

Scope: fundamentals, filings and risks. Never compares quarterly with annual values, mixes
GAAP and non-GAAP, imputes missing facts, or states consensus expectations.
"""

from __future__ import annotations

from marketlens_core.models import BriefSection

from app.agents import fixture_responses
from app.agents.prompts import FUNDAMENTALS_SYSTEM, fundamentals_package
from app.agents.specialist_base import SpecialistSpec

SPEC = SpecialistSpec(
    task="fundamentals_specialist",
    agent="fundamentals_specialist",
    name="fundamentals",
    system_prompt=FUNDAMENTALS_SYSTEM,
    sections=frozenset({BriefSection.FUNDAMENTALS, BriefSection.FILINGS, BriefSection.RISKS}),
    build_package=fundamentals_package,
    deterministic=fixture_responses.fundamentals_specialist,
)
