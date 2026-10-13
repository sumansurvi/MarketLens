"""Deterministic contradiction detection across retrieved evidence.

Contradictions are surfaced, never resolved silently: the brief lists them as unknowns,
claims citing contradicted evidence are qualified, and a material contradiction keeps the
report from being marked completed.

Checks: (1) provider quote vs the daily close for the same trading date; (2) SEC facts vs
provider-normalized fundamentals for the same metric and period; (3) a heuristic for
opposing headlines published within three days of each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from marketlens_core.models import EvidenceKind
from marketlens_core.untrusted import INJECTION_FLAG

from app.services.derived_metrics import DerivedMetrics
from app.services.evidence_collector import EvidenceBundle

RELATIVE_TOLERANCE = Decimal("0.005")
_OPPOSING = [
    ("raise", "cut"),
    ("beat", "miss"),
    ("approve", "reject"),
    ("approve", "block"),
    ("surge", "plunge"),
    ("expand", "halt"),
    ("increase", "decrease"),
    ("upgrade", "downgrade"),
]


@dataclass
class Contradiction:
    kind: str
    description: str
    evidence_ids: list[str]
    severity: str = "material"

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "description": self.description,
            "evidence_ids": self.evidence_ids,
            "severity": self.severity,
        }


def _stem_present(stem: str, text: str) -> bool:
    return re.search(rf"\b{stem}(?:s|d|ed|es|ing)?\b", text, re.IGNORECASE) is not None


def detect_contradictions(bundle: EvidenceBundle, derived: DerivedMetrics) -> list[Contradiction]:
    found: list[Contradiction] = []
    quote, prices = bundle.quote, bundle.prices
    if quote and prices and prices.bars and quote.observed_date == prices.bars[-1].trading_date:
        close = prices.bars[-1].close
        if close and abs(quote.price - close) / close > Decimal("0.0005"):
            found.append(
                Contradiction(
                    "quote_vs_close",
                    f"The provider quote for {quote.observed_date.isoformat()} does not match that day's close in the "
                    "daily series; neither value is preferred.",
                    [quote.evidence_id, prices.evidence_id],
                )
            )
    if bundle.metrics_fallback and bundle.facts_quarterly:
        sec = {(f.metric_name, f.period_end): f for f in bundle.facts_quarterly.facts}
        for metric in bundle.metrics_fallback.metrics:
            other = sec.get((metric.metric_name, metric.period_end))
            if other and other.value and abs(metric.value - other.value) / abs(other.value) > RELATIVE_TOLERANCE:
                found.append(
                    Contradiction(
                        "cross_source_fundamentals",
                        f"{metric.label} for the period ended {metric.period_end.isoformat()} differs between SEC company "
                        "facts and the provider-normalized metrics.",
                        [other.evidence_id, metric.evidence_id],
                    )
                )
    news = [
        e
        for e in bundle.ledger.values()
        if e.kind == EvidenceKind.NEWS and e.published_date and INJECTION_FLAG not in e.flags
    ]
    news.sort(key=lambda e: e.published_date)
    for i, first in enumerate(news):
        for second in news[i + 1 :]:
            if (second.published_date - first.published_date).days > 3:
                break
            a, b = (first.title or "").lower(), (second.title or "").lower()
            if any(
                (_stem_present(x, a) and _stem_present(y, b)) or (_stem_present(y, a) and _stem_present(x, b))
                for x, y in _OPPOSING
            ):
                found.append(
                    Contradiction(
                        "conflicting_headlines",
                        f"Two dated news items ({first.published_date.isoformat()} and {second.published_date.isoformat()}) "
                        "describe events in opposing terms; the evidence does not resolve which account is accurate.",
                        [first.evidence_id, second.evidence_id],
                    )
                )
    return found
