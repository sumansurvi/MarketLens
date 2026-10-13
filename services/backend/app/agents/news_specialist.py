"""News & Events Specialist: dated event chronology and possible (never causal) relevance.

Scope: recent_events and interpretation. Treats every news text as untrusted data.
"""

from __future__ import annotations

from marketlens_core.models import BriefSection

from app.agents import fixture_responses
from app.agents.prompts import NEWS_SYSTEM, news_package
from app.agents.specialist_base import SpecialistSpec

SPEC = SpecialistSpec(
    task="news_specialist",
    agent="news_events_specialist",
    name="news_events",
    system_prompt=NEWS_SYSTEM,
    sections=frozenset({BriefSection.RECENT_EVENTS, BriefSection.INTERPRETATION}),
    build_package=news_package,
    deterministic=fixture_responses.news_specialist,
)
