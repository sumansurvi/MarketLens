"""Server-side symbol normalization and syntax validation.

Symbols are never concatenated into SQL or URL paths before passing this check, and no
fuzzy matching is performed: an unknown but well-formed ticker is reported as unknown,
never replaced by a similar one.
"""

from __future__ import annotations

import re

from marketlens_core.errors import ErrorCode, MarketLensError

# 1-5 letters with an optional share-class suffix (BRK.B / BRK-B).
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$")
MAX_RAW_SYMBOL_LENGTH = 16


def normalize_symbol(raw: object) -> str:
    """Return the canonical uppercase symbol or raise ``INVALID_SYMBOL`` with guidance."""
    if not isinstance(raw, str):
        raise MarketLensError(ErrorCode.INVALID_SYMBOL, "Symbol must be a text value such as 'AAPL'.")
    candidate = raw.strip().upper()
    if not candidate:
        raise MarketLensError(ErrorCode.INVALID_SYMBOL, "Enter a ticker symbol such as 'AAPL'.")
    if len(raw) > MAX_RAW_SYMBOL_LENGTH or not SYMBOL_RE.fullmatch(candidate):
        raise MarketLensError(
            ErrorCode.INVALID_SYMBOL,
            "Symbols must be 1–5 letters with an optional class suffix (e.g. AAPL, BRK.B). "
            "Check the ticker and try again.",
            details={"pattern": SYMBOL_RE.pattern},
        )
    return candidate


def is_valid_symbol(raw: object) -> bool:
    try:
        normalize_symbol(raw)
    except MarketLensError:
        return False
    return True
