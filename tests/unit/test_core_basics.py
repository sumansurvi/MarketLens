"""Symbols, time horizons, calculations, hashing and configuration guards."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from marketlens_core.calculations import largest_moves, percent_change, price_change, quantize_like
from marketlens_core.config import ConfigError, Settings, load_settings, validate_settings
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.hashing import canonical_json, content_hash, evidence_id_for, hash_secret
from marketlens_core.models import PriceBar, TimeHorizonInput
from marketlens_core.symbols import normalize_symbol
from marketlens_core.timeutil import resolve_time_horizon, subtract_months


# --------------------------------------------------------------------------- symbols
@pytest.mark.parametrize("raw, expected", [("aapl", "AAPL"), ("  msft ", "MSFT"), ("brk.b", "BRK.B"), ("BF-B", "BF-B")])
def test_symbol_normalization(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "AAPL; DROP TABLE x",
        "../etc/passwd",
        "TOOLONGSYM",
        "AA PL",
        "$AAPL",
        "AAPL/",
        "A1",
        123,
        None,
        "x" * 40,
    ],
)
def test_symbol_rejects_unsafe_or_malformed_input(raw: object) -> None:
    with pytest.raises(MarketLensError) as err:
        normalize_symbol(raw)
    assert err.value.code == ErrorCode.INVALID_SYMBOL


# --------------------------------------------------------------------------- horizons
def test_subtract_months_clamps_day() -> None:
    assert subtract_months(dt.date(2026, 5, 31), 3) == dt.date(2026, 2, 28)
    assert subtract_months(dt.date(2026, 9, 30), 3) == dt.date(2026, 6, 30)
    assert subtract_months(dt.date(2026, 1, 15), 1) == dt.date(2025, 12, 15)


def test_default_horizon_is_disclosed_as_default() -> None:
    horizon = resolve_time_horizon(None, reference_date=dt.date(2026, 9, 30), basis="fixture as-of")
    assert (horizon.start_date, horizon.end_date) == (dt.date(2026, 6, 30), dt.date(2026, 9, 30))
    assert horizon.is_default and "default" in horizon.label.lower()


def test_horizon_rejects_reversed_future_and_oversized_ranges() -> None:
    ref = dt.date(2026, 9, 30)
    with pytest.raises(MarketLensError) as reversed_err:
        resolve_time_horizon(
            TimeHorizonInput(start_date=dt.date(2026, 9, 1), end_date=dt.date(2026, 8, 1)),
            reference_date=ref,
            basis="b",
        )
    assert reversed_err.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(MarketLensError) as future_err:
        resolve_time_horizon(
            TimeHorizonInput(start_date=dt.date(2026, 9, 1), end_date=dt.date(2026, 12, 1)),
            reference_date=ref,
            basis="b",
        )
    assert future_err.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(MarketLensError) as big_err:
        resolve_time_horizon(
            TimeHorizonInput(start_date=dt.date(2024, 1, 1), end_date=dt.date(2026, 9, 1)),
            reference_date=ref,
            basis="b",
        )
    assert big_err.value.code == ErrorCode.DATE_RANGE_TOO_LARGE


# --------------------------------------------------------------------------- calculations
def _bar(day: str, close: str) -> PriceBar:
    c = Decimal(close)
    return PriceBar(trading_date=dt.date.fromisoformat(day), open=c, high=c, low=c, close=c, volume=1)


def test_percent_change_formula_and_rounding() -> None:
    value, reason = percent_change(Decimal("200.00"), Decimal("220.00"))
    assert value == Decimal("10.0000") and reason is None
    value, _ = percent_change(Decimal("207.48"), Decimal("230.42"))
    assert value == Decimal("11.0565")  # (230.42-207.48)/207.48*100 = 11.05648...


@pytest.mark.parametrize("start, end", [(Decimal(0), Decimal(5)), (None, Decimal(5)), (Decimal(5), None)])
def test_percent_change_undefined_cases_return_reason_not_infinity(start, end) -> None:
    value, reason = percent_change(start, end)
    assert value is None and reason


def test_price_change_uses_first_on_or_after_and_last_on_or_before() -> None:
    bars = [_bar("2026-06-29", "100"), _bar("2026-07-01", "110"), _bar("2026-07-15", "121"), _bar("2026-08-03", "130")]
    result = price_change(bars, dt.date(2026, 6, 30), dt.date(2026, 8, 1))
    assert result.start_date == dt.date(2026, 7, 1) and result.end_date == dt.date(2026, 7, 15)
    assert result.percentage_change == Decimal("10.0000") and result.absolute_change == Decimal("11")
    assert any("First available observation" in w for w in result.warnings)


def test_price_change_with_no_bars_or_zero_start_is_unavailable() -> None:
    assert price_change([], dt.date(2026, 1, 1), dt.date(2026, 2, 1)).unavailable_reason
    zero = price_change([_bar("2026-01-02", "0"), _bar("2026-01-05", "3")], dt.date(2026, 1, 1), dt.date(2026, 2, 1))
    assert zero.percentage_change is None and "zero" in (zero.unavailable_reason or "")


def test_largest_moves_skips_missing_days_without_fabricating() -> None:
    # 2026-08-14 (a Friday trading day) has no bar: the 08-13 -> 08-17 change spans two sessions, so it is
    # not a single-day move and is skipped rather than interpolated (decision D-67).
    bars = [_bar("2026-08-12", "100"), _bar("2026-08-13", "102"), _bar("2026-08-17", "96.9")]
    declines, gains = largest_moves(bars)
    assert declines == []
    assert gains[0].percentage_change == Decimal("2.0000") and gains[0].previous_date == dt.date(2026, 8, 12)


def test_quantize_like_preserves_source_precision() -> None:
    assert quantize_like(Decimal("1.23456"), Decimal("230.4200")) == Decimal("1.2346")


# --------------------------------------------------------------------------- hashing
def test_evidence_ids_are_deterministic_and_content_sensitive() -> None:
    a = evidence_id_for("quote", {"symbol": "AAPL", "content_hash": content_hash({"p": "1"})})
    b = evidence_id_for("quote", {"symbol": "AAPL", "content_hash": content_hash({"p": "1"})})
    c = evidence_id_for("quote", {"symbol": "AAPL", "content_hash": content_hash({"p": "2"})})
    assert a == b != c and a.startswith("ev_quote_")
    assert canonical_json({"b": Decimal("1.50"), "a": dt.date(2026, 1, 2)}) == '{"a":"2026-01-02","b":"1.50"}'
    assert hash_secret("x") != "x" and len(hash_secret("x")) == 64


# --------------------------------------------------------------------------- configuration
def test_default_settings_are_offline_demo() -> None:
    settings = load_settings(env_file=None)
    assert (settings.app_mode, settings.data_mode, settings.llm_mode, settings.voice_mode) == (
        "demo",
        "fixtures",
        "fixture",
        "disabled",
    )
    assert not any(v for k, v in settings.capabilities().items() if k.startswith("live_") or k == "voice")


def test_demo_mode_cannot_be_combined_with_live_providers() -> None:
    with pytest.raises(ConfigError) as err:
        load_settings(env_file=None, data_mode="live")
    assert any("APP_MODE=demo" in p for p in err.value.problems)


def test_live_modes_require_credentials_with_actionable_messages() -> None:
    settings = Settings(
        _env_file=None,
        app_mode="live",
        data_mode="live",
        llm_mode="openai",
        voice_mode="livekit",
        sec_user_agent="MarketLens/0.1 contact=your-email@example.com",
    )
    problems = " ".join(validate_settings(settings))
    for needle in ("ALPHA_VANTAGE_API_KEY", "SEC_USER_AGENT", "OPENAI_API_KEY", "OPENAI_MODEL", "LIVEKIT_URL"):
        assert needle in problems


def test_secrets_never_appear_in_settings_repr_or_capabilities() -> None:
    settings = Settings(
        _env_file=None, app_mode="live", alpha_vantage_api_key="AVSECRET123", openai_api_key="sk-secret"
    )
    assert "AVSECRET123" not in repr(settings) and "sk-secret" not in repr(settings)
    assert "AVSECRET123" not in str(settings.capabilities())


def test_relative_database_path_resolves_against_repo_root() -> None:
    settings = Settings(_env_file=None, database_url="sqlite:///./data/x.db")
    assert settings.database_path.parent.name == "data"
    assert settings.database_path.is_absolute()
    with pytest.raises(ConfigError):
        _ = Settings(_env_file=None, database_url="postgres://x").database_path


def test_public_exposure_warning_for_non_loopback_host() -> None:
    assert Settings(_env_file=None, backend_host="0.0.0.0").public_exposure_warnings()  # noqa: S104
    assert not Settings(_env_file=None).public_exposure_warnings()
