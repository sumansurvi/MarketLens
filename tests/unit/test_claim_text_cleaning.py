"""Claim text never carries internal references (D-86)."""

from __future__ import annotations

from app.services.validation import clean_claim_text


def test_internal_references_are_removed_from_claim_text() -> None:
    text = (
        "AAPL rose from $298.97 to $336.64 (evidence_id: ev_calc_618e8b9ab2d6). The range was $275.15 to $341.07 "
        "(evidence_ids: ev_calc_319a2070451e, ev_calc_7a752e62e2ff). Revenue — us-gaap:Revenues — grew "
        "[ev_metric_ac3d8fcbcd3d] (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax)."
    )
    assert clean_claim_text(text) == (
        "AAPL rose from $298.97 to $336.64. The range was $275.15 to $341.07. Revenue grew."
    )
    assert clean_claim_text("Plain sentence, 12.5%.") == "Plain sentence, 12.5%."
