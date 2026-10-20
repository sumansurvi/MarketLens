"""Shared test fixtures.

Tests never read the developer's ``.env`` and scrub every MarketLens setting from the
process environment, so the deterministic suite always runs in fixture mode with no keys.
Tests marked ``live`` are skipped unless ``MARKETLENS_LIVE_TESTS=1``; only then is the real
configuration captured (before scrubbing) and handed to them through ``live_config``.

Set ``MARKETLENS_TEST_POSTGRES_URL=postgresql://…`` to run the suite's ``settings``/``client``
tests against Postgres (D-98): each test gets a fresh schema, dropped afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ["MARKETLENS_DOTENV"] = "disabled"

from marketlens_core.config import REPO_ROOT, Settings  # noqa: E402

LIVE_TESTS_ENABLED = os.environ.get("MARKETLENS_LIVE_TESTS") == "1"
TEST_POSTGRES_URL = os.environ.get("MARKETLENS_TEST_POSTGRES_URL", "").strip()
_LIVE_CONFIG = (
    {name: os.environ[name.upper()] for name in Settings.model_fields if name.upper() in os.environ}
    if LIVE_TESTS_ENABLED
    else {}
)
for _name in Settings.model_fields:
    os.environ.pop(_name.upper(), None)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if LIVE_TESTS_ENABLED:
        return
    skip = pytest.mark.skip(reason="live smoke test: set MARKETLENS_LIVE_TESTS=1 and configure credentials")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def live_config() -> dict[str, object]:
    """Real configuration for ``live`` tests: the repository .env file overlaid by the environment."""
    from dotenv import dotenv_values

    env_file = REPO_ROOT / ".env"
    values: dict[str, object] = {}
    if env_file.exists():
        file_values = {k.lower(): v for k, v in dotenv_values(env_file).items() if v is not None}
        values.update({k: v for k, v in file_values.items() if k in Settings.model_fields})
    values.update(_LIVE_CONFIG)
    return values


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def postgres_schema_url(schema: str) -> str:
    separator = "&" if "?" in TEST_POSTGRES_URL else "?"
    return f"{TEST_POSTGRES_URL}{separator}options=-csearch_path%3D{schema}"


@pytest.fixture
def database_url(tmp_path: Path) -> Iterator[str]:
    """A fresh database for one test: a SQLite file, or a new Postgres schema when configured."""
    if not TEST_POSTGRES_URL:
        yield f"sqlite:///{tmp_path}/marketlens.db"
        return
    import psycopg

    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_POSTGRES_URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield postgres_schema_url(schema)
    finally:
        with psycopg.connect(TEST_POSTGRES_URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(_env_file=None, database_url=database_url)


@pytest.fixture
def client(settings: Settings):
    from app.main import create_app
    from starlette.testclient import TestClient

    # The web client always sends X-MarketLens-Client (CSRF defense); tests do the same.
    with TestClient(create_app(settings), headers={"X-MarketLens-Client": "pytest"}) as test_client:
        yield test_client
