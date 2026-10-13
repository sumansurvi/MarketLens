"""Speech adaptation of validated claim text never changes a value."""

from __future__ import annotations

import pytest
from app.services.voice_summary import for_speech


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        (
            "Revenue for the quarter ended 2026-06-27 (fiscal 2026 Q3) was $93.48 billion "
            "(us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, Form 10-Q filed 2026-07-31).",
            "Revenue for the quarter ended June 27, 2026 (fiscal 2026 Q3) was $93.48 billion.",
        ),
        (
            "AAPL closed at $230.42 on 2026-09-30, up 11.06% from $207.48 on 2026-06-30.",
            "AAPL closed at $230.42 on September 30, 2026, up 11.06% from $207.48 on June 30, 2026.",
        ),
        ("Cites (ev_news_1234567890ab) a dated item.", "Cites a dated item."),
        ("Invalid date 2026-02-30 stays as written.", "Invalid date 2026-02-30 stays as written."),
    ],
)
def test_for_speech_only_reformats_dates_and_drops_identifiers(text: str, spoken: str) -> None:
    assert for_speech(text) == spoken
