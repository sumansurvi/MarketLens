"""In-process provider capability tracking for status tools (no secrets, no payloads)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.timeutil import utc_now


@dataclass
class CapabilityTracker:
    last_success: dict[str, dt.datetime] = field(default_factory=dict)
    last_error: dict[str, ErrorCode] = field(default_factory=dict)
    rate_limited_until: dt.datetime | None = None

    def success(self, capability: str) -> None:
        self.last_success[capability] = utc_now()
        self.last_error.pop(capability, None)

    def failure(self, capability: str, error: MarketLensError) -> None:
        self.last_error[capability] = error.code
        if error.code == ErrorCode.RATE_LIMITED:
            wait = error.retry_after_seconds or 60
            self.rate_limited_until = utc_now() + dt.timedelta(seconds=wait)

    def available(self, capability: str) -> bool | None:
        """True after a success, False after a capability/auth failure, None if unknown."""
        if capability in self.last_success and capability not in self.last_error:
            return True
        code = self.last_error.get(capability)
        if code in (ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.PROVIDER_AUTH_FAILED):
            return False
        return None

    @property
    def latest_success(self) -> dt.datetime | None:
        return max(self.last_success.values(), default=None)

    def rate_limit_state(self) -> tuple[bool, float | None]:
        if self.rate_limited_until and self.rate_limited_until > utc_now():
            return True, round((self.rate_limited_until - utc_now()).total_seconds(), 1)
        return False, None

    @property
    def most_recent_error(self) -> ErrorCode | None:
        return next(reversed(self.last_error.values()), None) if self.last_error else None
