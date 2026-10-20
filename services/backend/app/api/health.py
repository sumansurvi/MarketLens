"""Health and data-status routes. Configuration presence only — never secret values."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from marketlens_core import __version__
from marketlens_core.policy import DEMO_FIXTURES_NOTICE, RESEARCH_ONLY_NOTICE, policy_summary
from marketlens_core.timeutil import utc_now
from marketlens_core.tool_models import ServerStatusData

from app.api.deps import get_container
from app.api.schemas import DataStatusResponse, HealthResponse, McpServerStatus, PolicyInfo
from app.container import AppContainer

router = APIRouter(prefix="/api", tags=["health"])

# A status read shouldn't hang on a server that isn't up: report it as unavailable instead.
# Startup has already waited for the servers, so a ready one answers well within this.
STATUS_WAIT_SECONDS = 8.0


@router.get("/health", response_model=HealthResponse)
async def health(container: AppContainer = Depends(get_container)) -> HealthResponse:
    s = container.settings
    return HealthResponse(
        version=__version__,
        app_env=s.app_env,
        app_mode=s.app_mode,
        data_mode=s.data_mode,
        llm_mode=s.llm_mode,
        voice_mode=s.voice_mode,
        voice_enabled=s.voice_enabled,
        capabilities=s.capabilities(),
        tool_transport=container.gateway.transport,
        mcp_servers={
            server_id: McpServerStatus.model_validate(status)
            for server_id, status in getattr(container.gateway, "status", dict)().items()
        },
        demo_notice=DEMO_FIXTURES_NOTICE if s.is_fixture_data else None,
        research_only_notice=RESEARCH_ONLY_NOTICE,
        policy=PolicyInfo.model_validate(policy_summary()),
        warnings=s.deployment_warnings(),
        server_time=utc_now(),
    )


@router.get("/data-status", response_model=DataStatusResponse)
async def data_status(container: AppContainer = Depends(get_container)) -> DataStatusResponse:
    s = container.settings
    servers: list[ServerStatusData] = []
    unavailable: list[str] = []
    checks = (("market_data", "get_provider_status"), ("research_sources", "get_research_sources_status"))
    outcomes = await asyncio.gather(
        *(asyncio.wait_for(container.gateway.call(server, tool, {}), STATUS_WAIT_SECONDS) for server, tool in checks),
        return_exceptions=True,
    )
    for (server, _), outcome in zip(checks, outcomes, strict=True):
        if isinstance(outcome, TimeoutError):
            unavailable.append(server)
            continue
        if isinstance(outcome, BaseException):
            raise outcome
        container.runs.add_tool_call(outcome.record)
        if outcome.ok:
            servers.append(ServerStatusData.model_validate(outcome.data))
        else:
            unavailable.append(server)
    first = servers[0] if servers else None
    notices = []
    if s.is_fixture_data:
        notices.append(DEMO_FIXTURES_NOTICE)
    if unavailable:
        notices.append(f"Status unavailable for: {', '.join(unavailable)}.")
    return DataStatusResponse(
        data_mode=s.data_mode,
        is_fixture=s.is_fixture_data,
        fixture_dataset_version=first.fixture_dataset_version if first else None,
        reference_time=first.reference_time if first else None,
        reference_basis=first.reference_basis if first else None,
        servers=servers,
        unavailable_servers=unavailable,
        latest_research_by_symbol=container.runs.latest_run_times(),
        capabilities=s.capabilities(),
        notices=notices,
    )
