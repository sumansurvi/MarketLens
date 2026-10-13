"""Fixture transports: serve labeled, provider-shaped synthetic files to the adapters.

Each JSON file's ``_fixture`` block is required (unlabeled data is never served). A
``fault`` directive raises the same structured error a live provider failure would; a
``delay_seconds`` directive sleeps before answering (used to exercise timeouts).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.fixtures import FixtureDataset
from marketlens_core.models import SourceMode
from marketlens_core.providers.base import ProviderPayload, fault_error
from marketlens_core.symbols import SYMBOL_RE
from marketlens_core.timeutil import utc_now

_FUNCTION_RE = re.compile(r"^[A-Z_]{3,40}$")


class _FixtureBase:
    source_mode = SourceMode.FIXTURE

    def __init__(self, dataset: FixtureDataset) -> None:
        self.dataset = dataset

    async def _load_json(self, relative: str, label: str) -> ProviderPayload:
        text = self.dataset.read_text(relative)
        if text is None:
            raise MarketLensError(
                ErrorCode.DATA_UNAVAILABLE,
                "The demo fixture dataset has no data for this request.",
                details={"request": label},
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MarketLensError(
                ErrorCode.MALFORMED_PROVIDER_RESPONSE,
                "The provider response could not be parsed.",
                details={"request": label, "simulated": True},
            ) from exc
        meta = data.get("_fixture") if isinstance(data, dict) else None
        if not isinstance(meta, dict) or meta.get("is_fixture") is not True:
            raise MarketLensError(
                ErrorCode.MALFORMED_PROVIDER_RESPONSE,
                "Fixture file is missing its synthetic-data label and was not served.",
                details={"request": label},
            )
        await self._apply_directives(meta)
        body = {k: v for k, v in data.items() if k != "_fixture"}
        return ProviderPayload(
            data=body,
            source_mode=SourceMode.FIXTURE,
            retrieved_at=utc_now(),
            request_label=label,
            fixture_meta=meta,
        )

    @staticmethod
    async def _apply_directives(meta: dict[str, Any]) -> None:
        delay = meta.get("delay_seconds")
        if delay:
            await asyncio.sleep(float(delay))
        fault = meta.get("fault")
        if isinstance(fault, dict):
            raise fault_error(fault)


class FixtureAlphaVantageTransport(_FixtureBase):
    """Maps ``function`` + ``symbol``/``tickers`` to ``alpha_vantage/{FUNCTION}/{SYMBOL}.json``."""

    async def query(self, params: dict[str, str]) -> ProviderPayload:
        function = params.get("function", "")
        symbol = params.get("symbol") or params.get("tickers") or ""
        if not _FUNCTION_RE.fullmatch(function) or not SYMBOL_RE.fullmatch(symbol):
            raise MarketLensError(ErrorCode.INVALID_INPUT, "Unsupported fixture request.")
        return await self._load_json(f"alpha_vantage/{function}/{symbol}.json", f"alpha_vantage:{function}:{symbol}")


class FixtureSecTransport(_FixtureBase):
    """Maps SEC resources to ``sec/...`` files; public URLs use the fixture domain."""

    @property
    def archives_base(self) -> str:
        return f"{self.dataset.url_base}/sec/Archives/edgar/data"

    @property
    def data_api_base(self) -> str:
        return f"{self.dataset.url_base}/sec/data-api"

    async def tickers_exchange(self) -> ProviderPayload:
        return await self._load_json("sec/company_tickers_exchange.json", "sec_edgar:company_tickers_exchange")

    async def submissions(self, cik10: str) -> ProviderPayload:
        return await self._load_json(f"sec/submissions/CIK{cik10}.json", f"sec_edgar:submissions:{cik10}")

    async def companyfacts(self, cik10: str) -> ProviderPayload:
        return await self._load_json(f"sec/companyfacts/CIK{cik10}.json", f"sec_edgar:companyfacts:{cik10}")

    async def document(self, cik_int: int, accession_nodash: str, filename: str) -> ProviderPayload:
        relative = f"sec/archives/{cik_int}/{accession_nodash}/{filename}"
        text = self.dataset.read_text(relative)
        label = f"sec_edgar:document:{cik_int}/{accession_nodash}/{filename}"
        if text is None:
            raise MarketLensError(
                ErrorCode.DATA_UNAVAILABLE,
                "The filing document is not available in the demo fixture dataset.",
                details={"request": label},
            )
        meta = {
            "is_fixture": True,
            "source_mode": "fixture",
            "dataset_version": self.dataset.version,
            "label": self.dataset.label,
        }
        return ProviderPayload(
            data=text,
            source_mode=SourceMode.FIXTURE,
            retrieved_at=utc_now(),
            request_label=label,
            fixture_meta=meta,
        )
