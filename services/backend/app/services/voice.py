"""Voice sessions: room-scoped LiveKit tokens and the voice worker's view of the shared workflow.

A browser asks for a voice session; the backend creates an unguessable room bound to that
browser session (by hash) and returns a short-lived token that can only join that room and
publish a microphone track. The token explicitly dispatches the MarketLens agent. The worker
then acts *for that room*: research it starts belongs to the browser session, and it can
read only runs of that session. The worker has no write access to the workspace.
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets
from dataclasses import dataclass

from livekit import api
from marketlens_core.analytics import AnalyticsRepository
from marketlens_core.config import Settings
from marketlens_core.db.repositories import RunRepository, VoiceSessionRecord, VoiceSessionRepository
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.hashing import hash_secret
from marketlens_core.models import TERMINAL_RUN_STATUSES, ResearchRequest, ResearchRun, TimeHorizonInput
from marketlens_core.symbols import normalize_symbol
from marketlens_core.timeutil import utc_now
from marketlens_core.voice import ROOM_NAME_RE, ROOM_PREFIX

from app.services import voice_summary
from app.services.research_workflow import ResearchWorkflowService
from app.services.run_manager import QueueRunManager, RunManager

logger = logging.getLogger("marketlens.voice")
MAX_SESSIONS_PER_WINDOW = 6
SESSION_WINDOW = dt.timedelta(minutes=10)
ROOM_EMPTY_TIMEOUT_SECONDS = 60
ROOM_DEPARTURE_TIMEOUT_SECONDS = 20


@dataclass
class VoiceSessionGrant:
    server_url: str
    room_name: str
    participant_identity: str
    participant_token: str
    expires_at: dt.datetime
    agent_name: str


class VoiceService:
    def __init__(
        self,
        settings: Settings,
        sessions: VoiceSessionRepository,
        runs: RunRepository,
        workflow: ResearchWorkflowService,
        run_manager: RunManager | QueueRunManager,
        analytics: AnalyticsRepository | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.runs = runs
        self.workflow = workflow
        self.run_manager = run_manager
        self.analytics = analytics

    def _track(self, event: str, **properties: str) -> None:
        if self.analytics is not None:
            try:
                self.analytics.record(event, **properties)
            except Exception:  # noqa: BLE001 - analytics never breaks voice
                logger.warning("could not record analytics event %s", event)

    # ------------------------------------------------------------------ browser side
    def require_enabled(self) -> None:
        if not self.settings.voice_enabled:
            raise MarketLensError(
                ErrorCode.VOICE_NOT_CONFIGURED,
                "Voice is not configured. Set VOICE_MODE=livekit with LIVEKIT_URL, LIVEKIT_API_KEY and "
                "LIVEKIT_API_SECRET, and start the voice worker. Text research works without voice.",
            )

    def create_session(self, session_id: str) -> VoiceSessionGrant:
        self.require_enabled()
        session_hash = hash_secret(session_id)
        now = utc_now()
        if self.sessions.count_since(session_hash, now - SESSION_WINDOW) >= MAX_SESSIONS_PER_WINDOW:
            raise MarketLensError(
                ErrorCode.RATE_LIMITED,
                "Too many voice sessions were started recently. Wait a few minutes and try again.",
                retry_after_seconds=60,
            )
        ttl = dt.timedelta(seconds=self.settings.voice_session_ttl_seconds)
        room = f"{ROOM_PREFIX}{secrets.token_urlsafe(18)}"
        identity = f"user-{secrets.token_hex(6)}"
        record = VoiceSessionRecord(
            room_name=room,
            session_hash=session_hash,
            participant_identity=identity,
            created_at=now,
            expires_at=now + ttl,
        )
        self.sessions.create(record)
        self._track("voice_session_created")
        assert self.settings.livekit_api_key and self.settings.livekit_api_secret and self.settings.livekit_url
        token = (
            api.AccessToken(
                self.settings.livekit_api_key.get_secret_value(), self.settings.livekit_api_secret.get_secret_value()
            )
            .with_identity(identity)
            .with_name("MarketLens user")
            .with_ttl(ttl)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                    can_publish_sources=["microphone"],
                )
            )
            .with_room_config(
                api.RoomConfiguration(
                    empty_timeout=ROOM_EMPTY_TIMEOUT_SECONDS,
                    departure_timeout=ROOM_DEPARTURE_TIMEOUT_SECONDS,
                    agents=[api.RoomAgentDispatch(agent_name=self.settings.livekit_agent_name)],
                )
            )
            .to_jwt()
        )
        return VoiceSessionGrant(
            server_url=self.settings.livekit_url,
            room_name=room,
            participant_identity=identity,
            participant_token=token,
            expires_at=record.expires_at,
            agent_name=self.settings.livekit_agent_name,
        )

    def session_for_browser(self, room_name: str, session_id: str) -> VoiceSessionRecord:
        record = self._record(room_name)
        if record.session_hash != hash_secret(session_id):
            raise MarketLensError(ErrorCode.NOT_FOUND, "Voice session not found.")
        return record

    def end_for_browser(self, room_name: str, session_id: str) -> VoiceSessionRecord:
        record = self.session_for_browser(room_name, session_id)
        if record.ended_at is None:
            self.sessions.end(room_name, utc_now())
            self._track("voice_session_ended", ended_by="browser")
        return self.sessions.get(room_name) or record

    # ------------------------------------------------------------------ worker side
    def live_room(self, room_name: str) -> VoiceSessionRecord:
        """The room the worker is acting for; it must exist and be within its lifetime."""
        record = self._record(room_name)
        if record.is_expired(utc_now()):
            raise MarketLensError(
                ErrorCode.VOICE_SESSION_EXPIRED,
                "This voice session has ended or expired. Start a new voice session to continue; "
                "reports already produced stay available on screen.",
            )
        return record

    async def start_research(
        self, room_name: str, *, symbol: str, question: str, horizon: str | None
    ) -> tuple[ResearchRun, bool]:
        """Start research for the room's browser session; a repeat for a running symbol is reused."""
        record = self.live_room(room_name)
        normalized = normalize_symbol(symbol)
        if record.active_run_id:
            active = self.runs.get_run(record.active_run_id)
            if active and active.status not in TERMINAL_RUN_STATUSES and active.symbol == normalized:
                return active, True
        request = ResearchRequest(
            symbol=normalized,
            question=question,
            time_horizon=TimeHorizonInput(preset=horizon) if horizon else None,  # type: ignore[arg-type]
        )
        run = await self.workflow.create_run(request, session_hash=record.session_hash, origin="voice")
        if run.status.value == "queued":
            await self.run_manager.start(run.run_id)
        self.sessions.set_active_run(room_name, run.run_id)
        return run, False

    def run_for_room(self, room_name: str, run_id: str | None) -> ResearchRun:
        record = self.live_room(room_name)
        target = run_id or record.active_run_id
        if not target:
            raise MarketLensError(ErrorCode.NOT_FOUND, "No research has been started in this voice session yet.")
        run = self.runs.get_run(target)
        if run is None or self.runs.run_session_hash(target) != record.session_hash:
            raise MarketLensError(ErrorCode.NOT_FOUND, "That research run is not part of this session.")
        return run

    def status(self, room_name: str, run_id: str | None) -> dict[str, object]:
        run = self.run_for_room(room_name, run_id)
        return voice_summary.run_status_view(run, self.runs.list_steps(run.run_id), utc_now())

    def summary(self, room_name: str, run_id: str | None, focus: str) -> dict[str, object]:
        run = self.run_for_room(room_name, run_id)
        stored = self.runs.get_brief(run.run_id)
        return voice_summary.brief_summary_view(run, stored[0] if stored else None, focus)

    def end_for_worker(self, room_name: str) -> None:
        record = self._record(room_name)
        if record.ended_at is None:
            self.sessions.end(room_name, utc_now())
            self._track("voice_session_ended", ended_by="worker")

    # ------------------------------------------------------------------ helpers
    def _record(self, room_name: str) -> VoiceSessionRecord:
        if not ROOM_NAME_RE.fullmatch(room_name):
            raise MarketLensError(ErrorCode.NOT_FOUND, "Voice session not found.")
        record = self.sessions.get(room_name)
        if record is None:
            raise MarketLensError(ErrorCode.NOT_FOUND, "Voice session not found.")
        return record
