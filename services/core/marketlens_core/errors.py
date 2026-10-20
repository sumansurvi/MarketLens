"""Stable error codes and the error type shared by the API, MCP servers and adapters.

Messages must be safe to show to a user: no stack traces, secrets, headers or raw
provider payloads. Provider status codes may go into ``details`` as diagnostics.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    NOT_FOUND = "NOT_FOUND"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    MALFORMED_PROVIDER_RESPONSE = "MALFORMED_PROVIDER_RESPONSE"
    DATE_RANGE_TOO_LARGE = "DATE_RANGE_TOO_LARGE"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    MCP_SERVER_UNAVAILABLE = "MCP_SERVER_UNAVAILABLE"
    MCP_TOOL_NOT_ALLOWED = "MCP_TOOL_NOT_ALLOWED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    INVALID_CONFIRMATION_GRANT = "INVALID_CONFIRMATION_GRANT"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    ACTION_ALREADY_CONSUMED = "ACTION_ALREADY_CONSUMED"
    PAYLOAD_HASH_MISMATCH = "PAYLOAD_HASH_MISMATCH"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # API-level additions (documented in PROJECT_STATUS decision log)
    MODE_MISMATCH = "MODE_MISMATCH"
    RUN_NOT_CANCELLABLE = "RUN_NOT_CANCELLABLE"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    VOICE_NOT_CONFIGURED = "VOICE_NOT_CONFIGURED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    REQUEST_NOT_ALLOWED = "REQUEST_NOT_ALLOWED"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    STALE_DATA = "STALE_DATA"
    VOICE_SESSION_EXPIRED = "VOICE_SESSION_EXPIRED"
    RUN_IN_PROGRESS = "RUN_IN_PROGRESS"
    # The backend is running but its setup is incomplete (configuration or database, D-101).
    BACKEND_MISCONFIGURED = "BACKEND_MISCONFIGURED"


_RETRYABLE = {
    ErrorCode.RATE_LIMITED,
    ErrorCode.PROVIDER_TIMEOUT,
    ErrorCode.MCP_SERVER_UNAVAILABLE,
    ErrorCode.LLM_UNAVAILABLE,
}

_HTTP_STATUS = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.INVALID_SYMBOL: 422,
    ErrorCode.UNKNOWN_SYMBOL: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CAPABILITY_UNAVAILABLE: 409,
    ErrorCode.DATA_UNAVAILABLE: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.PROVIDER_TIMEOUT: 504,
    ErrorCode.PROVIDER_AUTH_FAILED: 502,
    ErrorCode.MALFORMED_PROVIDER_RESPONSE: 502,
    ErrorCode.DATE_RANGE_TOO_LARGE: 422,
    ErrorCode.SCHEMA_VALIDATION_FAILED: 422,
    ErrorCode.MCP_SERVER_UNAVAILABLE: 503,
    ErrorCode.MCP_TOOL_NOT_ALLOWED: 403,
    ErrorCode.CONFIRMATION_REQUIRED: 403,
    ErrorCode.INVALID_CONFIRMATION_GRANT: 403,
    ErrorCode.CONFIRMATION_EXPIRED: 410,
    ErrorCode.ACTION_ALREADY_CONSUMED: 409,
    ErrorCode.PAYLOAD_HASH_MISMATCH: 409,
    ErrorCode.PERSISTENCE_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.MODE_MISMATCH: 409,
    ErrorCode.RUN_NOT_CANCELLABLE: 409,
    ErrorCode.REQUEST_TOO_LARGE: 413,
    ErrorCode.VOICE_NOT_CONFIGURED: 409,
    ErrorCode.LLM_UNAVAILABLE: 503,
    ErrorCode.BUDGET_EXCEEDED: 409,
    ErrorCode.REQUEST_NOT_ALLOWED: 403,
    ErrorCode.CONTRADICTORY_EVIDENCE: 409,
    ErrorCode.STALE_DATA: 409,
    ErrorCode.VOICE_SESSION_EXPIRED: 410,
    ErrorCode.RUN_IN_PROGRESS: 409,
    ErrorCode.BACKEND_MISCONFIGURED: 503,
}


class ErrorInfo(BaseModel):
    """Serializable error payload (MCP_TOOLS §2 error shape, minus ``ok``/``meta``)."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    code: ErrorCode
    message: str
    retryable: bool = False
    retry_after_seconds: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MarketLensError(Exception):
    """Domain error with a stable code and a user-safe message."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = ErrorCode(code)
        self.message = message
        self.retryable = (self.code in _RETRYABLE) if retryable is None else retryable
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}

    @property
    def http_status(self) -> int:
        return http_status_for(self.code)

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            retry_after_seconds=self.retry_after_seconds,
            details=self.details,
        )

    @classmethod
    def from_info(cls, info: ErrorInfo) -> MarketLensError:
        return cls(
            info.code,
            info.message,
            retryable=info.retryable,
            retry_after_seconds=info.retry_after_seconds,
            details=dict(info.details),
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MarketLensError({self.code.value!r}, {self.message!r})"


def http_status_for(code: ErrorCode) -> int:
    return _HTTP_STATUS.get(ErrorCode(code), 500)
