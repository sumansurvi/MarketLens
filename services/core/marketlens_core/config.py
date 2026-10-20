"""MarketLens configuration.

Settings come from environment variables and, for the backend and scripts, the
repository-root ``.env`` file. MCP server child processes are started with an explicit,
minimal environment and ``MARKETLENS_DOTENV=disabled`` so each server only sees the
settings and secrets it needs (the workspace server never receives provider keys).

Secrets are ``SecretStr`` so they never appear in ``repr``/logs; status endpoints expose
only booleans derived from them.
"""

from __future__ import annotations

import ipaddress
import os
from functools import cached_property
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root() -> Path:
    """Locate the repository root (``MARKETLENS_ROOT`` overrides discovery)."""
    override = os.environ.get("MARKETLENS_ROOT")
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pnpm-workspace.yaml").exists() and (parent / "services").is_dir():
            return parent
    return Path.cwd().resolve()


REPO_ROOT = find_repo_root()


class ConfigError(Exception):
    """Raised when configuration is invalid; ``problems`` are actionable messages."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid MarketLens configuration:\n- " + "\n- ".join(problems))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core
    app_env: Literal["development", "test", "production"] = "development"
    app_mode: Literal["demo", "live"] = "demo"
    data_mode: Literal["fixtures", "live"] = "fixtures"
    llm_mode: Literal["fixture", "openai"] = "fixture"
    voice_mode: Literal["disabled", "livekit"] = "disabled"
    database_url: str = "sqlite:///./data/marketlens.db"
    fixture_dir: str = "data/fixtures/v1"
    backend_host: str = "127.0.0.1"
    backend_port: int = Field(default=8000, ge=1, le=65535)
    frontend_origin: str = "http://localhost:3000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Hosted deployments (D-94): when set, every /api request except the health check and the
    # voice worker's internal API must carry it in X-MarketLens-Proxy-Secret. The web app's proxy
    # adds it server-side, so only the deployed web app can reach the backend.
    proxy_shared_secret: SecretStr | None = None
    # How research runs execute (D-98). inline: a background task in the backend process (local).
    # queue: the backend publishes each run to Vercel Queues and a queue-triggered function runs
    # it, because a Vercel Function can't keep working after it has answered (needs Postgres).
    run_execution: Literal["inline", "queue"] = "inline"
    research_queue_topic: str = Field(default="marketlens-research-runs", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    # Where the MCP servers' logs go. Default: data/runtime/logs. On Vercel only /tmp is writable.
    mcp_log_dir: str | None = None

    # LLM
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    # Optional: any OpenAI-compatible endpoint (e.g. local Ollama, http://127.0.0.1:11434/v1). Free, no key needed.
    openai_base_url: str | None = None
    openai_timeout_seconds: float = Field(default=45, gt=0, le=300)
    max_llm_calls_per_run: int = Field(default=8, ge=1, le=20)

    # Market data (Alpha Vantage)
    market_data_provider: Literal["alpha_vantage"] = "alpha_vantage"
    alpha_vantage_api_key: SecretStr | None = None
    alpha_vantage_base_url: str = "https://www.alphavantage.co/query"
    market_data_timeout_seconds: float = Field(default=15, gt=0, le=120)
    market_data_cache_ttl_seconds: int = Field(default=900, ge=0, le=86_400)
    market_data_quote_cache_ttl_seconds: int = Field(default=60, ge=0, le=3_600)
    market_data_max_retries: int = Field(default=2, ge=0, le=5)
    # Local politeness limit; Alpha Vantage documents 25 requests/day for free keys (no per-minute figure).
    alpha_vantage_max_requests_per_minute: int = Field(default=60, ge=1, le=1_200)

    # SEC EDGAR
    sec_user_agent: str | None = None
    sec_timeout_seconds: float = Field(default=15, gt=0, le=120)
    sec_cache_ttl_seconds: int = Field(default=3600, ge=0, le=86_400)
    sec_max_concurrent_requests: int = Field(default=2, ge=1, le=8)
    sec_max_requests_per_second: float = Field(default=5, gt=0, le=10)
    sec_data_base_url: str = "https://data.sec.gov"
    sec_www_base_url: str = "https://www.sec.gov"

    # News fallback (D-86): Alpha Vantage news is a premium endpoint; with a plan that lacks it,
    # live mode takes the latest headlines from Yahoo Finance's public per-ticker RSS feed.
    news_fallback: Literal["yahoo_rss", "none"] = "yahoo_rss"
    yahoo_news_base_url: str = "https://feeds.finance.yahoo.com/rss/2.0/headline"

    # LiveKit voice
    livekit_url: str | None = None
    livekit_api_key: SecretStr | None = None
    livekit_api_secret: SecretStr | None = None
    livekit_agent_name: str = "marketlens-voice-agent"
    voice_session_ttl_seconds: int = Field(default=600, ge=60, le=3_600)
    transcript_persistence: bool = False
    # Voice worker pipeline: LiveKit Inference model IDs (https://docs.livekit.io/agents/models/).
    # No defaults: provider model names change, so they are chosen explicitly in configuration.
    voice_stt_model: str | None = None
    voice_llm_model: str | None = None
    voice_tts_model: str | None = None
    voice_tts_voice: str | None = None
    voice_language: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    # Where the voice worker reaches the backend's internal voice API.
    voice_backend_url: str = "http://127.0.0.1:8000"

    # MCP and security limits
    mcp_transport: Literal["stdio"] = "stdio"
    mcp_tool_call_timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_research_steps: int = Field(default=12, ge=4, le=40)
    max_provider_calls: int = Field(default=20, ge=4, le=60)
    max_parallel_specialists: int = Field(default=3, ge=1, le=6)
    max_report_chars: int = Field(default=18_000, ge=2_000, le=100_000)
    action_confirmation_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    max_request_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    run_timeout_seconds: float = Field(default=120, gt=5, le=900)

    # ------------------------------------------------------------------ derived
    @property
    def database_is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgres://"))

    @cached_property
    def database_path(self) -> Path:
        """The SQLite file (local mode). Postgres URLs have no path; use ``database_url``."""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ConfigError(
                [
                    f"DATABASE_URL must start with '{prefix}' (SQLite) or 'postgresql://' (Postgres); got a different scheme."
                ]
            )
        raw = self.database_url[len(prefix) :]
        path = Path(raw)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    @cached_property
    def fixture_root(self) -> Path:
        path = Path(self.fixture_dir)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    @property
    def is_fixture_data(self) -> bool:
        return self.data_mode == "fixtures"

    @property
    def source_mode(self) -> str:
        """Record-level mode string used in evidence/meta (``fixture`` | ``live``)."""
        return "fixture" if self.is_fixture_data else "live"

    @property
    def alpha_vantage_configured(self) -> bool:
        return bool(self.alpha_vantage_api_key and self.alpha_vantage_api_key.get_secret_value().strip())

    @property
    def sec_user_agent_configured(self) -> bool:
        return _valid_sec_user_agent(self.sec_user_agent)

    @property
    def openai_base_is_loopback(self) -> bool:
        """A local endpoint (e.g. Ollama) needs no API key."""
        if not self.openai_base_url:
            return False
        host = urlparse(self.openai_base_url).hostname or ""
        return host == "localhost" or host.startswith("127.") or host == "::1"

    @property
    def openai_configured(self) -> bool:
        has_key = bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())
        return bool(self.openai_model and (has_key or self.openai_base_is_loopback))

    @property
    def livekit_configured(self) -> bool:
        return bool(
            self.livekit_url
            and self.livekit_api_key
            and self.livekit_api_key.get_secret_value().strip()
            and self.livekit_api_secret
            and self.livekit_api_secret.get_secret_value().strip()
        )

    @property
    def voice_enabled(self) -> bool:
        return self.voice_mode == "livekit" and self.livekit_configured

    def capabilities(self) -> dict[str, bool]:
        """Secret-free capability booleans for health/status endpoints."""
        return {
            "fixture_data": self.is_fixture_data,
            "live_market_data": self.data_mode == "live" and self.alpha_vantage_configured,
            "live_sec_data": self.data_mode == "live" and self.sec_user_agent_configured,
            "live_news": self.data_mode == "live" and self.alpha_vantage_configured,
            "live_llm": self.llm_mode == "openai" and self.openai_configured,
            "voice": self.voice_enabled,
            "transcript_persistence": self.transcript_persistence,
        }

    @property
    def proxy_secret(self) -> str | None:
        value = self.proxy_shared_secret.get_secret_value().strip() if self.proxy_shared_secret else ""
        return value or None

    @property
    def session_cookie_secure(self) -> bool:
        """Production is served over https (Vercel), so the session cookie gets ``Secure``."""
        return self.app_env == "production"

    def public_exposure_warnings(self) -> list[str]:
        warnings: list[str] = []
        # On Vercel Services the backend has no public route (only the web app's binding reaches
        # it, D-98), so a shared secret adds nothing there.
        if self.app_env == "production" and not self.proxy_secret and not os.environ.get("VERCEL"):
            warnings.append(
                "APP_ENV=production without PROXY_SHARED_SECRET: anyone who finds this backend's URL can "
                "run research on your API keys. Set the same PROXY_SHARED_SECRET here and on the web app."
            )
        try:
            host_is_loopback = ipaddress.ip_address(self.backend_host).is_loopback
        except ValueError:
            host_is_loopback = self.backend_host == "localhost"
        if not host_is_loopback:
            warnings.append(
                f"BACKEND_HOST={self.backend_host} is not a loopback address. MarketLens is a local "
                "single-user MVP without authentication; do not expose it to a network."
            )
        return warnings


_PLACEHOLDER_EMAILS = ("your-email@example.com", "you@example.com")


def _valid_sec_user_agent(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return "@" in value and not any(p in lowered for p in _PLACEHOLDER_EMAILS)


def validate_voice_worker_settings(settings: Settings) -> list[str]:
    """Problems that stop the LiveKit voice worker from starting (empty list = ready)."""
    problems: list[str] = []
    if settings.voice_mode != "livekit":
        problems.append("VOICE_MODE=livekit is required to run the voice worker (VOICE_MODE is 'disabled').")
    if not settings.livekit_configured:
        problems.append("LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required (LiveKit Cloud project).")
    missing = [
        name.upper()
        for name in ("voice_stt_model", "voice_llm_model", "voice_tts_model")
        if not (getattr(settings, name) or "").strip()
    ]
    if missing:
        problems.append(
            f"{', '.join(missing)} must name LiveKit Inference models (see https://docs.livekit.io/agents/models/); "
            "the LLM must support tool calling."
        )
    return problems


def validate_settings(settings: Settings) -> list[str]:
    """Return actionable problems; an empty list means the configuration is usable."""
    problems: list[str] = []
    if settings.app_mode == "demo" and (settings.data_mode != "fixtures" or settings.llm_mode != "fixture"):
        problems.append(
            "APP_MODE=demo requires DATA_MODE=fixtures and LLM_MODE=fixture so the demo never calls live "
            "providers. Set APP_MODE=live to enable live data or a live LLM."
        )
    if settings.data_mode == "live":
        if not settings.alpha_vantage_configured:
            problems.append(
                "DATA_MODE=live requires ALPHA_VANTAGE_API_KEY (free key: https://www.alphavantage.co/support/#api-key). "
                "Or set DATA_MODE=fixtures for the offline demo."
            )
        if not settings.sec_user_agent_configured:
            problems.append(
                "DATA_MODE=live requires SEC_USER_AGENT with a real contact, e.g. "
                "'MarketLens/0.1 contact=you@yourdomain.com' (SEC fair-access policy)."
            )
    if settings.llm_mode == "openai":
        has_key = bool(settings.openai_api_key and settings.openai_api_key.get_secret_value().strip())
        if not (has_key or settings.openai_base_is_loopback):
            problems.append(
                "LLM_MODE=openai requires OPENAI_API_KEY, or OPENAI_BASE_URL pointing at a local server "
                "such as Ollama (free, no key). Or set LLM_MODE=fixture."
            )
        if not settings.openai_model:
            problems.append(
                "LLM_MODE=openai requires OPENAI_MODEL (choose a currently supported model that supports "
                "structured outputs). Model IDs are not hardcoded."
            )
    if settings.proxy_secret is not None and len(settings.proxy_secret) < 32:
        problems.append(
            "PROXY_SHARED_SECRET must be at least 32 characters (for example the output of `openssl rand -hex 32`)."
        )
    if settings.voice_mode == "livekit" and not settings.livekit_configured:
        problems.append(
            "VOICE_MODE=livekit requires LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET. "
            "Or set VOICE_MODE=disabled (text research keeps working)."
        )
    problems.extend(_database_problems(settings))
    if settings.run_execution == "queue" and not settings.database_is_postgres:
        problems.append(
            "RUN_EXECUTION=queue requires a Postgres DATABASE_URL: queue-triggered functions run on other "
            "instances and can't share a local SQLite file."
        )
    if settings.is_fixture_data and not (settings.fixture_root / "manifest.json").exists():
        problems.append(
            f"Fixture dataset not found at {settings.fixture_root}. Set FIXTURE_DIR or restore data/fixtures/v1."
        )
    return problems


def load_settings(
    *, env_file: Path | None | Literal["default"] = "default", validate: bool = True, **overrides: object
) -> Settings:
    """Load settings from the environment (and ``.env`` unless disabled) and validate them.

    ``overrides`` take precedence (used by tests and the evaluation runner). MCP server
    processes pass ``validate=False`` and run ``validate_server_settings`` instead, because
    each server only receives the settings it needs.
    """
    if env_file == "default":
        dotenv_disabled = os.environ.get("MARKETLENS_DOTENV", "").lower() == "disabled"
        candidate = REPO_ROOT / ".env"
        env_file = None if dotenv_disabled or not candidate.exists() else candidate
    settings = Settings(_env_file=env_file, **overrides)  # type: ignore[call-arg]
    if validate:
        problems = validate_settings(settings)
        if problems:
            raise ConfigError(problems)
    return settings


def validate_server_settings(settings: Settings, server_id: str) -> list[str]:
    """Validation for one MCP server process, which receives only its own settings."""
    problems: list[str] = []
    if settings.app_mode == "demo" and settings.data_mode != "fixtures":
        problems.append("APP_MODE=demo requires DATA_MODE=fixtures.")
    if settings.is_fixture_data and not (settings.fixture_root / "manifest.json").exists():
        problems.append(f"Fixture dataset not found at {settings.fixture_root}.")
    if settings.data_mode == "live":
        if server_id in ("market_data", "research_sources") and not settings.alpha_vantage_configured:
            problems.append(f"{server_id}: DATA_MODE=live requires ALPHA_VANTAGE_API_KEY.")
        if server_id == "research_sources" and not settings.sec_user_agent_configured:
            problems.append("research_sources: DATA_MODE=live requires SEC_USER_AGENT with a real contact.")
    problems.extend(_database_problems(settings))
    return problems


def _database_problems(settings: Settings) -> list[str]:
    if settings.database_is_postgres:
        return []
    try:
        db_path = settings.database_path
    except ConfigError as exc:
        return exc.problems
    if db_path.exists() and db_path.is_dir():
        return [f"DATABASE_URL points to a directory ({db_path}); it must be a file path."]
    return []


def mcp_log_directory(settings: Settings) -> Path | None:
    """Where MCP server logs are written: MCP_LOG_DIR, else data/runtime/logs. ``-``: stderr."""
    if settings.mcp_log_dir == "-":
        return None
    if settings.mcp_log_dir:
        path = Path(settings.mcp_log_dir)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()
    if settings.database_is_postgres:
        return REPO_ROOT / "data" / "runtime" / "logs"
    return settings.database_path.parent / "runtime" / "logs"
