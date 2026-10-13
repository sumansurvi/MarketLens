"""Voice worker test configuration.

The deterministic suite never reads a ``.env`` file and never contacts LiveKit or the
backend: configuration is replaced by dummy values (so ``marketlens_voice.worker`` can be
imported) and the backend is an ``httpx.MockTransport``. Tests marked ``live`` run only with
``MARKETLENS_LIVE_TESTS=1`` and real LiveKit credentials captured before scrubbing.
"""

from __future__ import annotations

import os

import pytest

os.environ["MARKETLENS_DOTENV"] = "disabled"

from marketlens_core.config import Settings  # noqa: E402

LIVE_TESTS_ENABLED = os.environ.get("MARKETLENS_LIVE_TESTS") == "1"
_LIVE_CONFIG = (
    {name: os.environ[name.upper()] for name in Settings.model_fields if name.upper() in os.environ}
    if LIVE_TESTS_ENABLED
    else {}
)
for _name in Settings.model_fields:
    os.environ.pop(_name.upper(), None)

DUMMY_ENV = {
    "VOICE_MODE": "livekit",
    "LIVEKIT_URL": "wss://marketlens-test.livekit.cloud",
    "LIVEKIT_API_KEY": "APItestkey0001",
    "LIVEKIT_API_SECRET": "test-livekit-secret-0123456789abcdef0123456789",
    "VOICE_STT_MODEL": "test/stt-model",
    "VOICE_LLM_MODEL": "test/llm-model",
    "VOICE_TTS_MODEL": "test/tts-model",
}
os.environ.update(DUMMY_ENV)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if LIVE_TESTS_ENABLED:
        return
    skip = pytest.mark.skip(reason="live voice test: set MARKETLENS_LIVE_TESTS=1 and configure LiveKit")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def live_config() -> dict[str, str]:
    from marketlens_core.config import REPO_ROOT

    values: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        from dotenv import dotenv_values

        values.update({k.lower(): v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update(_LIVE_CONFIG)
    return {k: v for k, v in values.items() if k in Settings.model_fields}
