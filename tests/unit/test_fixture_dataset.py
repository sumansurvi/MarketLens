"""The synthetic fixture dataset is labeled, reproducible and internally consistent."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from decimal import Decimal

from marketlens_core.config import REPO_ROOT

from tests.helpers import FIXTURE_ROOT, load_fixture

HOLIDAYS = {"2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07"}


def test_every_json_fixture_is_labeled_synthetic() -> None:
    files = [p for p in FIXTURE_ROOT.rglob("*.json") if p.name != "manifest.json"]
    assert len(files) >= 25
    for path in files:
        data = json.loads(path.read_text())
        block = data.get("_fixture")
        assert block and block["is_fixture"] is True and block["source_mode"] == "fixture", path
        assert "SYNTHETIC" in block["label"]


def test_fixture_documents_are_labeled_and_urls_use_documentation_domain() -> None:
    docs = list((FIXTURE_ROOT / "sec" / "archives").rglob("*.htm"))
    assert docs
    for doc in docs:
        assert "SYNTHETIC FIXTURE" in doc.read_text()
    blob = "".join(p.read_text() for p in FIXTURE_ROOT.rglob("*.json"))
    assert "sec.gov/Archives" not in blob  # no fabricated real-looking filing URLs
    for url in (u for u in blob.split('"') if u.startswith("http")):
        assert url.startswith("https://example.com/marketlens-fixture"), url


def test_generator_reproduces_committed_files_exactly() -> None:
    result = subprocess.run(  # noqa: S603 - fixed local script
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_fixtures.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_daily_series_skips_weekends_and_holidays_and_ohlc_is_consistent() -> None:
    for symbol in ("AAPL", "MSFT", "NVDA"):
        series = load_fixture(f"alpha_vantage/TIME_SERIES_DAILY/{symbol}.json")["Time Series (Daily)"]
        assert len(series) == 100
        for day, row in series.items():
            date = dt.date.fromisoformat(day)
            assert date.weekday() < 5 and day not in HOLIDAYS
            o, h, low, c = (Decimal(row[k]) for k in ("1. open", "2. high", "3. low", "4. close"))
            assert low <= min(o, c) <= max(o, c) <= h


def test_quote_matches_last_bar() -> None:
    for symbol in ("AAPL", "MSFT", "NVDA"):
        series = load_fixture(f"alpha_vantage/TIME_SERIES_DAILY/{symbol}.json")["Time Series (Daily)"]
        last_day = max(series)
        quote = load_fixture(f"alpha_vantage/GLOBAL_QUOTE/{symbol}.json")["Global Quote"]
        assert quote["07. latest trading day"] == last_day
        assert quote["05. price"] == series[last_day]["4. close"]


def test_manifest_describes_scenarios_and_hashes_match() -> None:
    import hashlib

    manifest = load_fixture("manifest.json")
    assert manifest["as_of"] == "2026-09-30T20:00:00Z"
    assert {s["symbol"] for s in manifest["symbols"]} == {"AAPL", "MSFT", "NVDA"}
    for relative, digest in manifest["files"].items():
        text = (FIXTURE_ROOT / relative).read_text(encoding="utf-8")
        assert "sha256:" + hashlib.sha256(text.encode()).hexdigest() == digest, relative
