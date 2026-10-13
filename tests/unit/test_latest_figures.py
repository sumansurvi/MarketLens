"""Latest or nothing for financial figures (D-85): superseded concepts and overdue periods are not offered."""

from __future__ import annotations

import datetime as dt

import pytest
from marketlens_core.config import Settings
from marketlens_core.fixtures import ReferenceClock
from marketlens_core.models import FreshnessStatus, SourceMode
from marketlens_core.providers import sec_edgar as sec
from marketlens_core.providers.base import ProviderPayload, reporting_period_freshness
from marketlens_core.tool_models import CompanyFactsInput
from mcp_research_sources.service import ResearchSourcesService

pytestmark = pytest.mark.anyio

NOW = dt.datetime(2026, 10, 12, 16, 0, tzinfo=dt.UTC)


def test_quarterly_allowance_covers_the_fourth_quarter_gap() -> None:
    q3 = dt.date(2026, 3, 31)  # e.g. Microsoft's fiscal Q3; Q4 (June) is only in the 10-K
    assert (
        reporting_period_freshness(q3, NOW, period_type="quarterly", fiscal_period="Q3", what="x").status
        == FreshnessStatus.FRESH
    )
    assert (
        reporting_period_freshness(q3, NOW, period_type="quarterly", fiscal_period="Q2", what="x").status
        == FreshnessStatus.STALE
    )
    assert (
        reporting_period_freshness(dt.date(2026, 6, 27), NOW, period_type="quarterly", what="x").status
        == FreshnessStatus.FRESH
    )
    assert (
        reporting_period_freshness(dt.date(2025, 9, 27), NOW, period_type="annual", what="x").status
        == FreshnessStatus.FRESH
    )
    assert (
        reporting_period_freshness(dt.date(2025, 3, 31), NOW, period_type="annual", what="x").status
        == FreshnessStatus.STALE
    )


def _entry(start: str, end: str, val: int, fp: str, filed: str) -> dict:
    return {
        "start": start,
        "end": end,
        "val": val,
        "fy": 2026,
        "fp": fp,
        "form": "10-Q",
        "filed": filed,
        "accn": f"0000-{end}",
    }


def _facts(latest_fp: str) -> dict:
    latest = _entry("2026-01-01", "2026-03-31", 70_000, latest_fp, "2026-04-29")
    return {
        "entityName": "Example Corp",
        "facts": {
            "us-gaap": {
                # Last reported under this label in 2010; the company switched labels since.
                "Revenues": {"units": {"USD": [_entry("2010-10-01", "2010-12-31", 19_000, "Q2", "2011-01-27")]}},
                "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [latest]}},
                "NetIncomeLoss": {"units": {"USD": [{**latest, "val": 25_000}]}},
            }
        },
    }


class FakeSec:
    def __init__(self, facts: dict) -> None:
        self.facts = facts

    async def ticker_map(self) -> dict[str, sec.TickerRecord]:
        return {
            "EXMP": sec.TickerRecord(symbol="EXMP", cik10="0000000001", company_name="Example Corp", exchange="Nasdaq")
        }

    async def companyfacts(self, cik: str) -> ProviderPayload:
        return ProviderPayload(
            data=self.facts, source_mode=SourceMode.LIVE, retrieved_at=NOW, request_label="companyfacts"
        )

    def concept_url(self, cik: str, concept: str) -> str:
        return f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"


def _service(facts: dict) -> ResearchSourcesService:
    clock = ReferenceClock(now=NOW, basis="system clock", is_fixture=False)
    return ResearchSourcesService(Settings(_env_file=None), FakeSec(facts), None, lambda: clock)  # type: ignore[arg-type]


CONCEPTS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss"]


async def test_superseded_concepts_are_dropped_and_latest_period_kept() -> None:
    data = await _service(_facts("Q3")).get_sec_company_facts(
        CompanyFactsInput(symbol="EXMP", concepts=CONCEPTS, period_type="quarterly")
    )
    assert {f.concept for f in data.facts} == {"RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss"}
    assert all(f.period_end == dt.date(2026, 3, 31) for f in data.facts)
    assert "Revenues" in data.missing_concepts and any("superseded" in n for n in data.notes)
    assert all(e.freshness and e.freshness.status == FreshnessStatus.FRESH for e in data.evidence)


async def test_overdue_figures_are_not_offered_at_all() -> None:
    data = await _service(_facts("Q2")).get_sec_company_facts(
        CompanyFactsInput(symbol="EXMP", concepts=CONCEPTS, period_type="quarterly")
    )
    assert data.facts == [] and data.evidence == []
    assert set(CONCEPTS) <= set(data.missing_concepts)
    assert any("not provided" in n and "filing deadlines" in n for n in data.notes)
