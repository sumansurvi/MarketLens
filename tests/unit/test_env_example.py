"""`.env.example` stays aligned with the configuration parser and is safe by default."""

from __future__ import annotations

import re

from marketlens_core.config import REPO_ROOT, Settings, load_settings

ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _variables() -> dict[str, str]:
    values = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


def test_every_setting_is_documented_and_nothing_extra() -> None:
    documented = set(_variables())
    expected = {name.upper() for name in Settings.model_fields}
    assert documented == expected, {"missing": expected - documented, "unknown": documented - expected}


def test_example_defaults_load_as_the_offline_demo() -> None:
    settings = load_settings(env_file=ENV_EXAMPLE)
    assert (settings.app_mode, settings.data_mode, settings.llm_mode, settings.voice_mode) == (
        "demo",
        "fixtures",
        "fixture",
        "disabled",
    )
    assert settings.transcript_persistence is False


def test_example_contains_no_credentials() -> None:
    values = _variables()
    for key in ("OPENAI_API_KEY", "ALPHA_VANTAGE_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        assert values[key] == "", key
