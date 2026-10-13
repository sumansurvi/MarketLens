"""Canonical hashing, evidence IDs and server-generated identifiers."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

_KIND_PREFIX = {
    "price_series": "prices",
    "quote": "quote",
    "financial_metric": "metric",
    "filing": "filing",
    "news": "news",
    "calculation": "calc",
}


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"not canonically serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(value: Any) -> str:
    """``sha256:<hex>`` over the canonical JSON form of ``value``."""
    return "sha256:" + sha256_hex(canonical_json(value))


def evidence_id_for(kind: str, identity: dict[str, Any]) -> str:
    """Deterministic evidence ID (decision D-13): same identity + content -> same ID."""
    prefix = _KIND_PREFIX.get(str(kind), "item")
    return f"ev_{prefix}_{sha256_hex(canonical_json(identity))[:12]}"


def new_id(prefix: str) -> str:
    """Unguessable server-generated identifier such as ``run_9f2c...``."""
    return f"{prefix}_{secrets.token_hex(8)}"


def new_secret_token() -> str:
    """Bearer-style secret (session IDs, confirmation grants). Store only its hash."""
    return secrets.token_urlsafe(32)


def hash_secret(token: str) -> str:
    return sha256_hex("marketlens-secret:" + token)
