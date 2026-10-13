"""Fixture dataset access and the reference clock.

The fixture dataset (``data/fixtures/v1``) is versioned, synthetic and provider-shaped.
Every JSON file must carry a ``_fixture`` block with ``is_fixture: true``; files that
are not labeled are rejected rather than served.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from marketlens_core.config import Settings
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.timeutil import parse_iso_datetime, utc_now


class FixtureDataset:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        manifest_path = self.root / "manifest.json"
        if not manifest_path.exists():
            raise MarketLensError(ErrorCode.INTERNAL_ERROR, f"Fixture manifest missing at {manifest_path}.")
        self.manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        return str(self.manifest["dataset_version"])

    @property
    def label(self) -> str:
        return str(self.manifest["label"])

    @cached_property
    def as_of(self) -> dt.datetime:
        return parse_iso_datetime(self.manifest["as_of"])

    @property
    def reference_date(self) -> dt.date:
        return dt.date.fromisoformat(self.manifest["reference_date"])

    @property
    def url_base(self) -> str:
        return str(self.manifest["fixture_url_base"]).rstrip("/")

    @cached_property
    def symbols(self) -> dict[str, dict[str, Any]]:
        return {entry["symbol"]: entry for entry in self.manifest.get("symbols", [])}

    def path_for(self, relative: str) -> Path:
        """Resolve ``relative`` inside the dataset; rejects traversal outside the root."""
        candidate = (self.root / relative).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise MarketLensError(ErrorCode.INVALID_INPUT, "Fixture path escapes the dataset root.")
        return candidate

    def read_text(self, relative: str) -> str | None:
        path = self.path_for(relative)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class ReferenceClock:
    """The instant used as "now" for default horizons and freshness (decision D-10)."""

    now: dt.datetime
    basis: str
    is_fixture: bool

    @property
    def date(self) -> dt.date:
        return self.now.date()


def reference_clock(settings: Settings, dataset: FixtureDataset | None = None) -> ReferenceClock:
    if settings.is_fixture_data:
        ds = dataset or FixtureDataset(settings.fixture_root)
        return ReferenceClock(
            now=ds.as_of,
            basis=f"fixture dataset as-of {ds.reference_date.isoformat()} (synthetic {ds.version})",
            is_fixture=True,
        )
    return ReferenceClock(now=utc_now(), basis="current UTC date (live mode)", is_fixture=False)
