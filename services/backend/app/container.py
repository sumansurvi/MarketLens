"""Application container: wires settings, persistence, the MCP gateway and services."""

from __future__ import annotations

from dataclasses import dataclass

from marketlens_core.actions import ActionService
from marketlens_core.analytics import AnalyticsRepository
from marketlens_core.config import Settings
from marketlens_core.db import AnyDatabase, open_database
from marketlens_core.db.repositories import AuditRepository, RunRepository, VoiceSessionRepository

from app.mcp.client_gateway import MCPClientGateway
from app.mcp.gateway import ToolGateway
from app.providers.llm.base import LLMProvider
from app.providers.llm.factory import build_llm_provider
from app.services.research_workflow import ResearchWorkflowService
from app.services.run_manager import QueueRunManager, RequestRunManager, RunManager
from app.services.voice import VoiceService
from app.services.workspace import WorkspaceService


@dataclass
class AppContainer:
    settings: Settings
    db: AnyDatabase
    runs: RunRepository
    audit: AuditRepository
    actions: ActionService
    gateway: ToolGateway
    llm: LLMProvider
    workflow: ResearchWorkflowService
    workspace: WorkspaceService
    run_manager: RunManager | QueueRunManager | RequestRunManager
    voice: VoiceService
    analytics: AnalyticsRepository


def build_container(
    settings: Settings, gateway: ToolGateway | None = None, llm: LLMProvider | None = None
) -> AppContainer:
    db = open_database(settings)
    runs = RunRepository(db)
    actions = ActionService(db, action_ttl_seconds=settings.action_confirmation_ttl_seconds)
    tool_gateway = gateway or MCPClientGateway(settings)
    provider = llm or build_llm_provider(settings)
    analytics = AnalyticsRepository(db)
    workflow = ResearchWorkflowService(settings, runs, tool_gateway, provider, analytics)
    run_manager: RunManager | QueueRunManager | RequestRunManager
    if settings.run_execution == "queue":
        run_manager = QueueRunManager(settings, runs)
    elif settings.run_execution == "request":
        run_manager = RequestRunManager(settings, runs, workflow)
    else:
        run_manager = RunManager(workflow)
    return AppContainer(
        settings=settings,
        db=db,
        runs=runs,
        audit=AuditRepository(db),
        actions=actions,
        gateway=tool_gateway,
        llm=provider,
        workflow=workflow,
        workspace=WorkspaceService(tool_gateway, runs, actions, analytics),
        run_manager=run_manager,
        voice=VoiceService(settings, VoiceSessionRepository(db), runs, workflow, run_manager, analytics),
        analytics=analytics,
    )
