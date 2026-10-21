"""ResearchWorkflowService — the single research workflow shared by text and voice.

Stages (decision D-07/D-08): validating → collecting_evidence → (calculations) →
synthesizing → evidence_review → finalizing → done. Every stage is a persisted step;
every tool call is a persisted audit record. Status rules follow decision D-09.

Phase 1 composes the brief deterministically from evidence (no LLM); Phase 3 inserts the
specialist agents, supervisor and reviewer between collection and validation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from marketlens_core.analytics import AnalyticsRepository
from marketlens_core.config import Settings
from marketlens_core.db.repositories import RunRepository
from marketlens_core.errors import ErrorCode, MarketLensError
from marketlens_core.hashing import content_hash, hash_secret, new_id
from marketlens_core.models import (
    TERMINAL_RUN_STATUSES,
    AgentStep,
    AsOfMetadata,
    CategoryStatus,
    CompanyIdentity,
    DataMode,
    DataSourceStatus,
    EvidenceItem,
    EvidenceKind,
    GapItem,
    Methodology,
    MethodologyStep,
    ResearchBrief,
    ResearchCategory,
    ResearchClaim,
    ResearchRequest,
    ResearchRun,
    ReviewSummary,
    RunError,
    RunStage,
    RunStatus,
    SourceMode,
    StepStatus,
    ToolCallRecord,
)
from marketlens_core.policy import BRIEF_DISCLAIMER, FIXTURE_BRIEF_DISCLOSURE
from marketlens_core.symbols import normalize_symbol
from marketlens_core.timeutil import parse_iso_datetime, resolve_time_horizon, utc_now
from marketlens_core.tool_models import ResolveSymbolData, ServerStatusData

from app.agents import evidence_reviewer, fundamentals_specialist, market_specialist, news_specialist, supervisor
from app.agents.contracts import MAX_FINDINGS_PER_SPECIALIST, MAX_NOTES, MAX_SYNTHESIS_CLAIMS, SpecialistOutput
from app.agents.evidence_reviewer import ReviewResult
from app.agents.fixture_responses import FixtureContext
from app.agents.runtime import AgentRuntime
from app.agents.specialist_base import SpecialistSpec
from app.agents.supervisor import ClaimAllocator, drafts_to_claims
from app.mcp.gateway import ToolGateway
from app.providers.llm.base import LLMProvider
from app.services.brief_composer import BriefComposer
from app.services.consistency import detect_contradictions
from app.services.derived_metrics import compute_derived
from app.services.evidence_collector import EvidenceBundle, EvidenceCollector
from app.services.validation import validate_claims

# Seconds kept free at the end of a run for review merging, brief assembly and persistence.
FINALIZE_RESERVE_SECONDS = 5.0

# Fixed order: results are always assembled in this order, whatever finishes first.
SPECIALISTS: tuple[SpecialistSpec, ...] = (market_specialist.SPEC, news_specialist.SPEC, fundamentals_specialist.SPEC)


@dataclass
class SpecialistRun:
    spec: SpecialistSpec
    output: SpecialistOutput
    fallback: bool
    truncated: int = 0
    error_code: ErrorCode | None = None


logger = logging.getLogger("marketlens.workflow")


@dataclass
class ResearchRunDetail:
    run: ResearchRun
    steps: list[AgentStep]
    tool_calls: list[ToolCallRecord]
    source_status: list[DataSourceStatus]
    evidence: list[EvidenceItem]
    brief: ResearchBrief | None
    brief_payload_hash: str | None


class StepRecorder:
    """Persists ordered steps for one run."""

    def __init__(self, runs: RunRepository, run_id: str) -> None:
        self.runs = runs
        self.run_id = run_id
        self.seq = 0

    def start(self, key: str, agent: str, summary: str) -> AgentStep:
        self.seq += 1
        step = AgentStep(
            run_id=self.run_id,
            seq=self.seq,
            step_key=key,
            agent=agent,
            status=StepStatus.RUNNING,
            summary=summary,
            started_at=utc_now(),
        )
        self.runs.upsert_step(step)
        return step

    def finish(
        self,
        step: AgentStep,
        status: StepStatus,
        summary: str | None = None,
        *,
        detail: dict[str, Any] | None = None,
        error_code: ErrorCode | None = None,
    ) -> AgentStep:
        ended = utc_now()
        updated = step.model_copy(
            update={
                "status": status,
                "summary": summary or step.summary,
                "ended_at": ended,
                "latency_ms": int((ended - step.started_at).total_seconds() * 1000) if step.started_at else None,
                "detail": detail or step.detail,
                "error_code": error_code,
            }
        )
        self.runs.upsert_step(updated)
        return updated


class ResearchWorkflowService:
    def __init__(
        self,
        settings: Settings,
        runs: RunRepository,
        gateway: ToolGateway,
        llm: LLMProvider,
        analytics: AnalyticsRepository | None = None,
    ) -> None:
        self.settings = settings
        self.runs = runs
        self.gateway = gateway
        self.llm = llm
        self.analytics = analytics
        # Provider status read while creating a run, reused by its plan step (one call per run).
        self._status_at_creation: dict[str, ServerStatusData] = {}

    # ------------------------------------------------------------------ creation (synchronous)
    async def create_run(
        self,
        request: ResearchRequest,
        *,
        session_id: str | None = None,
        session_hash: str | None = None,
        origin: str = "text",
        parent_run_id: str | None = None,
    ) -> ResearchRun:
        """Validate and persist a queued run bound to a session (raw cookie value or its hash)."""
        symbol = normalize_symbol(request.symbol)
        configured = "fixture" if self.settings.is_fixture_data else "live"
        if request.requested_mode != "configured" and request.requested_mode != configured:
            raise MarketLensError(
                ErrorCode.MODE_MISMATCH,
                f"The request asked for {request.requested_mode} data but MarketLens is configured for {configured} "
                "data. Nothing is substituted silently; change DATA_MODE or the request.",
            )
        if session_hash is None and session_id:
            session_hash = hash_secret(session_id)
        if request.client_request_id and session_hash:
            existing = self.runs.find_by_client_request(session_hash, request.client_request_id)
            if existing:
                return existing
        pending_records: list[ToolCallRecord] = []
        resolved = await self.gateway.call("research_sources", "resolve_symbol", {"symbol": symbol})
        pending_records.append(resolved.record)
        if not resolved.ok or resolved.error is not None:
            error = resolved.error
            raise (
                MarketLensError.from_info(error)
                if error
                else MarketLensError(ErrorCode.INTERNAL_ERROR, "Symbol check failed.")
            )
        identity = ResolveSymbolData.model_validate(resolved.data)
        status = await self.gateway.call("market_data", "get_provider_status", {})
        pending_records.append(status.record)
        if not status.ok:
            raise MarketLensError(
                ErrorCode.MCP_SERVER_UNAVAILABLE,
                "The market-data service is unavailable, so the research window cannot be determined. Try again shortly.",
            )
        server_status = ServerStatusData.model_validate(status.data)
        horizon = resolve_time_horizon(
            request.time_horizon,
            reference_date=server_status.reference_time.date(),
            basis=server_status.reference_basis,
        )
        now = utc_now()
        run = ResearchRun(
            run_id=new_id("run"),
            symbol=identity.symbol,  # as resolved (e.g. BRK.B → BRK-B), so every tool uses one spelling
            company=CompanyIdentity.model_validate(identity.model_dump()),
            question=request.question.strip(),
            time_horizon=horizon,
            focus_areas=request.focus_areas,
            status=RunStatus.QUEUED,
            stage=RunStage.CREATED,
            data_mode=DataMode.FIXTURE if self.settings.is_fixture_data else DataMode.LIVE,
            llm_mode=self.settings.llm_mode,
            origin=origin,  # type: ignore[arg-type]
            parent_run_id=parent_run_id,
            created_at=now,
            updated_at=now,
        )
        stored = self.runs.create_run(run, session_hash=session_hash, client_request_id=request.client_request_id)
        if stored.run_id == run.run_id:
            self._status_at_creation[run.run_id] = server_status
            for seq, record in enumerate(pending_records, start=1):
                self.runs.add_tool_call(record.model_copy(update={"run_id": run.run_id, "seq": seq}))
            self._track("run_started", origin=run.origin, data_mode=run.data_mode.value, llm_mode=run.llm_mode)
        return stored

    # ------------------------------------------------------------------ execution (background)
    async def execute(self, run_id: str) -> None:
        run = self.runs.get_run(run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        started = time.monotonic()
        steps = StepRecorder(self.runs, run_id)
        self.runs.update_run(run_id, status=RunStatus.RUNNING, stage=RunStage.VALIDATING, started_at=utc_now())
        # Model steps must finish early enough to leave time for validation and persistence.
        llm_deadline = started + self.settings.run_timeout_seconds - FINALIZE_RESERVE_SECONDS
        try:
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                await self._execute(run, steps, llm_deadline)
        except asyncio.CancelledError:
            self._finish_cancelled(run_id, steps, started)
            raise
        except TimeoutError:
            self._fail(
                run_id,
                steps,
                started,
                ErrorCode.PROVIDER_TIMEOUT,
                f"The run exceeded its {int(self.settings.run_timeout_seconds)}-second limit and was stopped.",
            )
        except MarketLensError as exc:
            self._fail(run_id, steps, started, exc.code, exc.message)
        except Exception:  # noqa: BLE001 - background task boundary
            logger.exception("research run %s failed unexpectedly", run_id)
            self._fail(run_id, steps, started, ErrorCode.INTERNAL_ERROR, "The research run failed unexpectedly.")
        else:
            self.runs.update_run(run_id, duration_ms=int((time.monotonic() - started) * 1000))
        finally:
            self._track_finished(run_id)

    def _track(self, event: str, **properties: Any) -> None:
        """Record a local analytics event; analytics never breaks research."""
        if self.analytics is None:
            return
        try:
            self.analytics.record(event, **properties)
        except Exception:  # noqa: BLE001 - best effort
            logger.warning("could not record analytics event %s", event)

    def _track_finished(self, run_id: str) -> None:
        run = self.runs.get_run(run_id)
        if run is None or self.analytics is None:
            return
        properties: dict[str, Any] = {
            "origin": run.origin,
            "status": run.status.value,
            "duration_ms": run.duration_ms,
            "data_mode": run.data_mode.value,
            "llm_mode": run.llm_mode,
        }
        for step in self.runs.list_steps(run_id):
            if step.step_key == "collect_evidence":
                for status in step.detail.get("source_status", []):
                    properties[f"category_{status['category']}"] = status["status"]
            if step.step_key == "finalize":
                properties["llm_calls"] = step.detail.get("llm_calls")
        stored = self.runs.get_brief(run_id)
        if stored:
            properties["withheld_claims"] = len(stored[0].withheld_claims)
        self._track("run_finished", **properties)

    def _check_cancel(self, run_id: str) -> None:
        current = self.runs.get_run(run_id)
        if current and current.cancel_requested:
            raise asyncio.CancelledError

    async def _execute(self, run: ResearchRun, steps: StepRecorder, llm_deadline: float | None = None) -> None:
        assert run.company is not None
        horizon = run.time_horizon
        plan = steps.start("plan", "research_supervisor", "Planning evidence categories and research window.")
        status = self._status_at_creation.pop(run.run_id, None)
        if status is None:  # e.g. a run created by another process: read the provider status now
            existing = len(self.runs.list_tool_calls(run.run_id))
            status_call = await self.gateway.call(
                "market_data", "get_provider_status", {}, run_id=run.run_id, seq=existing + 1
            )
            self.runs.add_tool_call(status_call.record)
            if status_call.ok and isinstance(status_call.data, dict):
                status = ServerStatusData.model_validate(status_call.data)
        dataset_version = None
        reference = parse_iso_datetime(f"{horizon.reference_date.isoformat()}T00:00:00Z")
        if status is not None:
            dataset_version = status.fixture_dataset_version
            reference = status.reference_time
        steps.finish(
            plan,
            StepStatus.COMPLETED,
            f"Plan: market, fundamentals, filings and news for {run.symbol}; window {horizon.start_date} to "
            f"{horizon.end_date} ({'default' if horizon.is_default else 'requested'}).",
            detail=supervisor.build_plan(run, self.settings, self.llm.mode),
        )
        self._check_cancel(run.run_id)
        # Collect evidence through the tool gateway.
        self.runs.update_run(run.run_id, stage=RunStage.COLLECTING_EVIDENCE)
        collect = steps.start(
            "collect_evidence", "mcp_gateway", "Collecting market, fundamentals, filings and news evidence."
        )
        existing_calls = len(self.runs.list_tool_calls(run.run_id))
        collector = EvidenceCollector(
            self.gateway, self.settings, self.runs.add_tool_call, seq_start=existing_calls + 1
        )
        bundle = EvidenceBundle(
            company=run.company,
            horizon=horizon,
            reference_time=reference,
            reference_basis=horizon.basis,
            fixture_dataset_version=dataset_version,
        )
        await collector.collect(run.run_id, bundle)
        statuses = bundle.data_source_statuses()
        collected_summary = ", ".join(f"{s.category.value}: {s.status.value}" for s in statuses)
        any_failed = any(s.status in (CategoryStatus.FAILED, CategoryStatus.PARTIAL) for s in statuses)
        steps.finish(
            collect,
            StepStatus.COMPLETED
            if not any_failed
            else StepStatus.FAILED
            if not bundle.ledger
            else StepStatus.COMPLETED,
            f"Collected {len(bundle.ledger)} evidence items via {collector.calls_made} tool calls ({collected_summary}).",
            detail={"source_status": [s.model_dump(mode="json") for s in statuses]},
        )
        self.runs.save_evidence(run.run_id, list(bundle.ledger.values()))
        self._check_cancel(run.run_id)
        market = bundle.outcomes.get(ResearchCategory.MARKET)
        fundamentals = bundle.outcomes.get(ResearchCategory.FUNDAMENTALS)
        usable = {CategoryStatus.AVAILABLE, CategoryStatus.PARTIAL}
        if (market is None or market.status not in usable) and (
            fundamentals is None or fundamentals.status not in usable
        ):
            raise MarketLensError(
                ErrorCode.DATA_UNAVAILABLE,
                "Neither market data nor company fundamentals could be retrieved, so no safe report can be produced.",
            )
        # Deterministic calculations.
        calc = steps.start(
            "calculations", "deterministic_calculator", "Computing price moves, ranges and period comparisons."
        )
        derived = compute_derived(bundle)
        self.runs.save_evidence(run.run_id, derived.evidence)
        steps.finish(
            calc,
            StepStatus.COMPLETED,
            f"Computed {len(derived.evidence)} deterministic calculations.",
            detail={"calculations": [e.title for e in derived.evidence]},
        )
        # Specialist analysis: three LLM-backed specialists, bounded concurrency, deterministic order.
        self._check_cancel(run.run_id)
        self.runs.update_run(run.run_id, stage=RunStage.SPECIALIST_ANALYSIS)
        runtime = AgentRuntime(
            self.llm,
            max_calls=self.settings.max_llm_calls_per_run,
            timeout_seconds=self.settings.openai_timeout_seconds,
            deadline=llm_deadline,
        )
        composer = BriefComposer(bundle, derived)
        window = (horizon.start_date, horizon.end_date)
        contradictions = [c.as_dict() for c in detect_contradictions(bundle, derived)]
        ctx = FixtureContext(composer=composer, ledger=bundle.ledger, window=window, contradictions=contradictions)
        semaphore = asyncio.Semaphore(self.settings.max_parallel_specialists)
        tasks = [
            asyncio.ensure_future(self._run_specialist(spec, runtime, bundle, run.question, ctx, steps, semaphore))
            for spec in SPECIALISTS
        ]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            # One failed model step ends the run (live data, D-104): the others must not keep calling the model.
            for task in tasks:
                task.cancel()
            raise
        allocator = ClaimAllocator()
        specialist_claims: list[ResearchClaim] = []
        scope_notes: list[str] = []
        for result in results:
            claims, dropped = drafts_to_claims(
                result.output.findings,
                allowed=result.spec.sections,
                produced_by="deterministic_fallback" if result.fallback else result.spec.agent,
                allocator=allocator,
            )
            specialist_claims += claims
            scope_notes += dropped
            if result.truncated:
                scope_notes.append(
                    f"{result.spec.agent} returned {result.truncated} finding(s) beyond the limit of "
                    f"{MAX_FINDINGS_PER_SPECIALIST}; they were discarded."
                )
        pre = validate_claims(specialist_claims, bundle.ledger, window=window)
        # Supervisor synthesis over validated findings only.
        self._check_cancel(run.run_id)
        self.runs.update_run(run.run_id, stage=RunStage.SYNTHESIZING)
        synth_step = steps.start(
            "supervisor_synthesis", supervisor.AGENT, "Synthesizing the validated specialist findings."
        )
        synthesis_error: MarketLensError | None = None
        try:
            synthesis, synth_truncated = await supervisor.synthesize(
                runtime, bundle, run.question, pre.kept, contradictions, ctx
            )
        except MarketLensError as exc:
            if not self.settings.is_fixture_data:
                raise self._stop_on_model_failure(steps, synth_step, "research supervisor", exc) from exc
            synthesis, synth_truncated, synthesis_error = supervisor.fallback_synthesis(ctx), 0, exc
        if synth_truncated:
            scope_notes.append(
                f"The research supervisor returned {synth_truncated} claim(s) beyond the limit of "
                f"{MAX_SYNTHESIS_CLAIMS}; they were discarded."
            )
        synth_claims, dropped = drafts_to_claims(
            synthesis.claims,
            allowed=supervisor.SYNTHESIS_SECTIONS,
            produced_by="deterministic_fallback" if synthesis_error else supervisor.AGENT,
            allocator=allocator,
        )
        scope_notes += dropped
        steps.finish(
            synth_step,
            StepStatus.FAILED if synthesis_error else StepStatus.COMPLETED,
            (
                f"Model step failed ({synthesis_error.code.value}); a clearly labeled deterministic synthesis was used."
                if synthesis_error
                else f"Drafted {len(synth_claims)} synthesis claims from {len(pre.kept)} validated findings."
            ),
            error_code=synthesis_error.code if synthesis_error else None,
            detail={"llm_calls": runtime.records_for(supervisor.SYNTHESIS_TASK), "fallback": bool(synthesis_error)},
        )
        # Evidence review: model verdicts combined with deterministic validation (stricter wins).
        self._check_cancel(run.run_id)
        self.runs.update_run(run.run_id, stage=RunStage.EVIDENCE_REVIEW)
        review_step = steps.start(
            "evidence_review", evidence_reviewer.AGENT, "Reviewing every claim against its evidence."
        )
        draft_claims = pre.kept + synth_claims
        report = validate_claims(draft_claims, bundle.ledger, window=window)
        ctx.claims = draft_claims
        review_error: MarketLensError | None = None
        try:
            review_output = await evidence_reviewer.review(runtime, bundle, draft_claims, report, contradictions, ctx)
        except MarketLensError as exc:
            if not self.settings.is_fixture_data:
                raise self._stop_on_model_failure(steps, review_step, "evidence review", exc) from exc
            review_output, review_error = None, exc
        reviewed = evidence_reviewer.apply_review(report, review_output, contradictions, bundle.ledger)
        withheld = pre.withheld + reviewed.withheld
        steps.finish(
            review_step,
            StepStatus.COMPLETED if reviewed.complete else StepStatus.FAILED,
            f"{len(reviewed.kept)} claims kept, {len(withheld)} withheld; "
            f"{pre.numbers_checked + report.numbers_checked} numbers checked."
            + ("" if reviewed.complete else " The model review did not complete; deterministic checks were applied."),
            error_code=review_error.code if review_error else None,
            detail={
                "llm_calls": runtime.records_for(evidence_reviewer.REVIEW_TASK),
                "withheld": [{"claim_id": c.claim_id, "notes": c.validation_notes} for c in withheld],
                "contradictions": contradictions + reviewed.reviewer_contradictions,
            },
        )
        # Finalize.
        self.runs.update_run(run.run_id, stage=RunStage.FINALIZING)
        final = steps.start("finalize", supervisor.AGENT, "Determining report status and assembling the brief.")
        # (agent, error code) for every model step that fell back to a deterministic summary.
        fallbacks = [(r.spec.agent, r.error_code or ErrorCode.LLM_UNAVAILABLE) for r in results if r.fallback]
        if synthesis_error:
            fallbacks.append((supervisor.AGENT, synthesis_error.code))
        all_contradictions = contradictions + reviewed.reviewer_contradictions
        status, errors = decide_status(
            bundle,
            withheld=withheld,
            fallbacks=fallbacks,
            review_complete=reviewed.complete,
            review_error=review_error.code if review_error else None,
            contradictions=all_contradictions,
        )
        extra_unknowns = agent_unknowns(
            results, synthesis.unknowns, reviewed, all_contradictions, fallbacks, review_error
        )
        brief = assemble_brief(
            run=run,
            bundle=bundle,
            composer=composer,
            kept=reviewed.kept,
            withheld=withheld,
            numbers_checked=pre.numbers_checked + report.numbers_checked,
            numbers_failed=pre.numbers_failed + report.numbers_failed,
            status=status,
            statuses=statuses,
            collector_calls=collector.calls_made,
            runtime=runtime,
            extra_unknowns=extra_unknowns,
            scope_notes=scope_notes,
            review_notes=reviewed.notes,
        )
        payload_hash = content_hash(brief.model_dump(mode="json"))
        self.runs.save_claims(run.run_id, reviewed.kept, withheld)
        self.runs.save_brief(run.run_id, brief, payload_hash)
        steps.finish(
            final,
            StepStatus.COMPLETED,
            f"Report status: {status.value}; {runtime.calls_made} language-model call(s) ({runtime.mode}).",
            detail={"payload_hash": payload_hash, "llm_calls": runtime.calls_made, "llm_mode": runtime.mode},
        )
        self.runs.update_run(
            run.run_id,
            status=status,
            stage=RunStage.DONE,
            completed_at=utc_now(),
            is_partial=status == RunStatus.PARTIAL,
            errors=errors,
        )

    def _stop_on_model_failure(
        self, steps: StepRecorder, step: object, what: str, exc: MarketLensError
    ) -> MarketLensError:
        """Live data (D-104): a model step that can't complete ends the run; nothing is summarized in its place.

        The step is recorded as failed with the provider's reason, and the run fails with it."""
        message = f"The {what} model step couldn't complete. {exc.message}"
        steps.finish(step, StepStatus.FAILED, message, error_code=exc.code)  # type: ignore[arg-type]
        return MarketLensError(exc.code, message, retryable=exc.retryable, details=exc.details)

    async def _run_specialist(
        self,
        spec: SpecialistSpec,
        runtime: AgentRuntime,
        bundle: EvidenceBundle,
        question: str,
        ctx: FixtureContext,
        steps: StepRecorder,
        semaphore: asyncio.Semaphore,
    ) -> SpecialistRun:
        async with semaphore:
            step = steps.start(spec.task, spec.agent, f"Analyzing {spec.name.replace('_', ' ')} evidence.")
            try:
                output, truncated = await spec.analyze(runtime, bundle, question, ctx)
            except MarketLensError as exc:
                if not self.settings.is_fixture_data:
                    raise self._stop_on_model_failure(steps, step, spec.agent.replace("_", " "), exc) from exc
                steps.finish(
                    step,
                    StepStatus.FAILED,
                    f"Model step failed ({exc.code.value}); a clearly labeled deterministic summary was used instead.",
                    error_code=exc.code,
                    detail={"llm_calls": runtime.records_for(spec.task), "fallback": True},
                )
                return SpecialistRun(spec, spec.fallback(ctx), fallback=True, error_code=exc.code)
            steps.finish(
                step,
                StepStatus.COMPLETED,
                f"{len(output.findings)} finding(s); status {output.status}.",
                detail={
                    "llm_calls": runtime.records_for(spec.task),
                    "missing_evidence": _safe_notes(output.missing_evidence),
                    "warnings": _safe_notes(output.warnings),
                    "discarded_beyond_limit": truncated,
                },
            )
            return SpecialistRun(spec, output, fallback=False, truncated=truncated)

    def _fail(self, run_id: str, steps: StepRecorder, started: float, code: ErrorCode, message: str) -> None:
        step = steps.start("failure", "research_supervisor", message)
        steps.finish(step, StepStatus.FAILED, message, error_code=code)
        current = self.runs.get_run(run_id)
        errors = list(current.errors) if current else []
        errors.append(
            RunError(
                code=code,
                message=message,
                retryable=code in (ErrorCode.PROVIDER_TIMEOUT, ErrorCode.MCP_SERVER_UNAVAILABLE),
            )
        )
        self.runs.update_run(
            run_id,
            status=RunStatus.FAILED,
            stage=RunStage.DONE,
            completed_at=utc_now(),
            errors=errors,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _finish_cancelled(self, run_id: str, steps: StepRecorder, started: float) -> None:
        step = steps.start("cancelled", "research_supervisor", "Cancellation acknowledged.")
        steps.finish(step, StepStatus.CANCELLED, "Run cancelled by request; no report was produced.")
        self.runs.update_run(
            run_id,
            status=RunStatus.CANCELLED,
            stage=RunStage.DONE,
            completed_at=utc_now(),
            cancel_requested=True,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------------ queries
    def get_run_detail(self, run_id: str) -> ResearchRunDetail:
        run = self.runs.get_run(run_id)
        if run is None:
            raise MarketLensError(ErrorCode.NOT_FOUND, "Research run not found.")
        steps = self.runs.list_steps(run_id)
        stored = self.runs.get_brief(run_id)
        brief, payload_hash = stored if stored else (None, None)
        source_status: list[DataSourceStatus] = []
        for step in steps:
            if step.step_key == "collect_evidence" and step.detail.get("source_status"):
                source_status = [DataSourceStatus.model_validate(s) for s in step.detail["source_status"]]
        return ResearchRunDetail(
            run=run,
            steps=steps,
            tool_calls=self.runs.list_tool_calls(run_id),
            source_status=source_status,
            evidence=self.runs.list_evidence(run_id),
            brief=brief,
            brief_payload_hash=payload_hash,
        )


# ---------------------------------------------------------------------- pure helpers
def _safe_notes(texts: list[str]) -> list[str]:
    """Model-written notes kept in the run trace: policy-clean, qualitative, at most five."""
    return [n for n in (evidence_reviewer.safe_note(t, allow_digits=False) for t in texts[:MAX_NOTES]) if n][:5]


def decide_status(
    bundle: EvidenceBundle,
    *,
    withheld: list[ResearchClaim],
    fallbacks: list[tuple[str, ErrorCode]],
    review_complete: bool,
    contradictions: list[dict[str, Any]],
    review_error: ErrorCode | None = None,
) -> tuple[RunStatus, list[RunError]]:
    """Decision D-09 (extended in Phase 3): completed only when mandatory evidence is present,
    every model step and the review completed, and no material limitation remains."""
    errors: list[RunError] = []
    outcomes = bundle.outcomes
    for category, outcome in outcomes.items():
        if outcome.status in (CategoryStatus.FAILED, CategoryStatus.PARTIAL, CategoryStatus.UNAVAILABLE):
            error = outcome.primary_error
            errors.append(
                RunError(
                    code=error.code if error else ErrorCode.DATA_UNAVAILABLE,
                    message=f"{category.value}: {error.message if error else outcome.status.value}",
                    category=category,
                    step_key="collect_evidence",
                    retryable=bool(error and error.retryable),
                )
            )

    def status_of(category: ResearchCategory) -> CategoryStatus:
        return outcomes[category].status if category in outcomes else CategoryStatus.NOT_REQUESTED

    mandatory_ok = all(
        status_of(c) == CategoryStatus.AVAILABLE
        for c in (ResearchCategory.MARKET, ResearchCategory.FUNDAMENTALS, ResearchCategory.FILINGS)
    )
    news_ok = status_of(ResearchCategory.NEWS) in (CategoryStatus.AVAILABLE, CategoryStatus.UNAVAILABLE)
    material_withheld = [c for c in withheld if c.materiality == "material"]
    if material_withheld:
        errors.append(
            RunError(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED,
                step_key="evidence_review",
                message=f"{len(material_withheld)} material claim(s) failed validation and were withheld.",
            )
        )
    for agent, code in fallbacks:
        errors.append(
            RunError(
                code=code,
                step_key=agent,
                retryable=code != ErrorCode.BUDGET_EXCEEDED,
                message=f"{agent.replace('_', ' ')}: model step failed; a deterministic summary was used.",
            )
        )
    if not review_complete:
        errors.append(
            RunError(
                code=review_error or ErrorCode.LLM_UNAVAILABLE,
                step_key="evidence_review",
                retryable=review_error != ErrorCode.BUDGET_EXCEEDED,
                message="The evidence review did not complete; only deterministic checks were applied.",
            )
        )
    material_conflicts = [c for c in contradictions if c.get("severity") == "material"]
    if material_conflicts:
        errors.append(
            RunError(
                code=ErrorCode.CONTRADICTORY_EVIDENCE,
                step_key="evidence_review",
                message=f"{len(material_conflicts)} material contradiction(s) between sources.",
            )
        )
    stale = [
        e.evidence_id
        for e in bundle.ledger.values()
        if e.freshness is not None and e.freshness.status.value == "stale" and e.kind.value in ("quote", "price_series")
    ]
    if stale:
        errors.append(
            RunError(
                code=ErrorCode.STALE_DATA,
                step_key="collect_evidence",
                message="Market data is stale relative to the reference date.",
            )
        )
    clean = mandatory_ok and news_ok and not material_withheld and not fallbacks and review_complete
    if clean and not material_conflicts and not stale:
        return RunStatus.COMPLETED, errors
    return RunStatus.PARTIAL, errors


def _category_for(evidence_ids: list[str], bundle: EvidenceBundle) -> ResearchCategory | None:
    kinds = {bundle.ledger[e].kind.value for e in evidence_ids if e in bundle.ledger}
    if kinds & {"quote", "price_series"}:
        return ResearchCategory.MARKET
    if "financial_metric" in kinds:
        return ResearchCategory.FUNDAMENTALS
    if "news" in kinds:
        return ResearchCategory.NEWS
    if "filing" in kinds:
        return ResearchCategory.FILINGS
    return None


def agent_unknowns(
    results: list[SpecialistRun],
    synthesis_unknowns: list[str],
    reviewed: ReviewResult,
    contradictions: list[dict[str, Any]],
    fallbacks: list[tuple[str, ErrorCode]],
    review_error: MarketLensError | None,
) -> list[tuple[str, str, str | None, list[str]]]:
    """(description, severity, reason_code, evidence_ids) entries contributed by the agents.

    Model-written strings must be qualitative: entries containing digits are dropped so an
    unknown can never smuggle in an unverified number."""
    entries: list[tuple[str, str, str | None, list[str]]] = []
    for c in contradictions:
        entries.append(
            (f"Contradiction: {c['description']}", c.get("severity", "info"), "CONTRADICTION", list(c["evidence_ids"]))
        )
    for agent, _ in fallbacks:
        entries.append(
            (
                f"The {agent.replace('_', ' ')} model step failed; its section uses a deterministic summary "
                "instead of model analysis.",
                "material",
                "LLM_FALLBACK",
                [],
            )
        )
    if review_error is not None or not reviewed.complete:
        entries.append(
            (
                "The model evidence review did not complete; claims were checked by deterministic rules only.",
                "material",
                "REVIEW_INCOMPLETE",
                [],
            )
        )
    texts = [m for r in results for m in r.output.missing_evidence[:MAX_NOTES]] + list(synthesis_unknowns[:MAX_NOTES])
    texts += [
        f"Evidence reviewer: no usable {m} evidence was available."
        if m in {c.value for c in ResearchCategory}
        else f"Evidence reviewer: {m}"
        for m in reviewed.missing_categories
    ]
    for text in texts:
        note = evidence_reviewer.safe_note(text, allow_digits=False)
        if note:
            entries.append((note, "info", "AGENT_NOTE", []))
    return entries


def assemble_brief(
    *,
    run: ResearchRun,
    bundle: EvidenceBundle,
    composer: BriefComposer,
    kept: list[ResearchClaim],
    withheld: list[ResearchClaim],
    numbers_checked: int,
    numbers_failed: int,
    status: RunStatus,
    statuses: list[DataSourceStatus],
    collector_calls: int,
    runtime: AgentRuntime,
    extra_unknowns: list[tuple[str, str, str | None, list[str]]],
    scope_notes: list[str],
    review_notes: list[str],
) -> ResearchBrief:
    assert run.company is not None
    ledger = bundle.ledger
    modes = {e.source_mode for e in ledger.values() if e.kind != EvidenceKind.CALCULATION}
    if modes == {SourceMode.FIXTURE}:
        data_mode = DataMode.FIXTURE
    elif modes == {SourceMode.LIVE}:
        data_mode = DataMode.LIVE
    else:
        data_mode = DataMode.MIXED if modes else run.data_mode
    is_fixture = data_mode == DataMode.FIXTURE
    filings = bundle.filings.filings if bundle.filings else []
    news_times = [e.published_at for e in ledger.values() if e.kind == EvidenceKind.NEWS and e.published_at]
    quarterly = bundle.facts_quarterly.facts if bundle.facts_quarterly else []
    retrieved = [e.retrieved_at for e in ledger.values() if e.kind != EvidenceKind.CALCULATION]
    as_of = AsOfMetadata(
        reference_time=bundle.reference_time,
        reference_basis=bundle.reference_basis,
        market_observation_date=bundle.quote.observed_date if bundle.quote else None,
        price_series_end=bundle.prices.last_date if bundle.prices else None,
        latest_filing_date=max((f.filed_date for f in filings), default=None),
        latest_fiscal_period_end=max((f.period_end for f in quarterly), default=None),
        latest_news_published_at=max(news_times) if news_times else None,
        earliest_retrieved_at=min(retrieved, default=None),
        latest_retrieved_at=max(retrieved, default=None),
        fixture_dataset_version=bundle.fixture_dataset_version if is_fixture else None,
    )
    model_label = (
        f"deterministic fixture LLM ({runtime.model}; no external model call)"
        if runtime.mode == "fixture"
        else f"{runtime.mode} model {runtime.model}"
    )
    calculations = [e.title or e.evidence_id for e in ledger.values() if e.kind == EvidenceKind.CALCULATION]
    producers = {c.produced_by for c in kept}
    steps = [
        MethodologyStep(
            name="Plan",
            agent="research_supervisor",
            description="Chose evidence categories, budgets and the research window "
            f"({run.time_horizon.label}; {run.time_horizon.basis}).",
        ),
        MethodologyStep(
            name="Collect evidence",
            agent="mcp_gateway",
            description=f"Retrieved evidence through {collector_calls} allowlisted MCP tool calls.",
            tools=sorted({t for s in statuses for t in s.tools}),
        ),
        MethodologyStep(
            name="Deterministic calculations",
            agent="deterministic_calculator",
            description="Computed price changes, daily moves, ranges, year-over-year changes and margins "
            "in decimal arithmetic from retrieved values, and checked sources for contradictions.",
        ),
        MethodologyStep(
            name="Specialist analysis",
            agent="market_data, news_events and fundamentals specialists",
            description=f"Three bounded specialists drafted findings for their own evidence category "
            f"using {model_label}.",
        ),
        MethodologyStep(
            name="Supervisor synthesis",
            agent="research_supervisor",
            description="Synthesized only findings that passed deterministic validation into the summary, "
            "positive/negative evidence, risks, interpretation and conditional scenarios.",
        ),
        MethodologyStep(
            name="Evidence review",
            agent="evidence_risk_reviewer",
            description="Model verdicts per claim combined with deterministic checks of evidence IDs, "
            "numbers, dates, causal wording, period compatibility and advice language; the stricter "
            "outcome wins and failing claims are withheld.",
        ),
    ]
    model_generated = []
    if producers & {"market_data_specialist", "news_events_specialist", "fundamentals_specialist"}:
        model_generated.append(f"Specialist findings ({model_label})")
    if "research_supervisor" in producers:
        model_generated.append(f"Executive summary, evidence balance, interpretation and scenarios ({model_label})")
    model_generated.append(f"Evidence review verdicts ({model_label})")
    if "deterministic_fallback" in producers:
        model_generated.append("Some sections use a deterministic fallback summary because a model step failed")
    limitations = [
        "Informational research only; not a recommendation or forecast.",
        "Daily bars are end-of-day values keyed by trading date; no intraday timing is implied.",
        "Filing excerpts are short quotations located by heading search, not a complete legal reading.",
        "News relevance is contextual; publication timing alone does not establish causation.",
        *scope_notes,
    ]
    if is_fixture:
        limitations.insert(0, "Built entirely from synthetic demo fixtures; values are illustrative, not market data.")
    gaps = composer.unknowns()
    seen = {g.description for g in gaps}
    for description, severity, reason, evidence_ids in extra_unknowns:
        if description not in seen:
            seen.add(description)
            gaps.append(
                GapItem(
                    category=_category_for(evidence_ids, bundle),
                    description=description,
                    reason_code=reason,
                    severity="material" if severity == "material" else "info",
                )
            )
    return ResearchBrief(
        run_id=run.run_id,
        title=f"{run.symbol} — company research brief ({run.time_horizon.start_date} to {run.time_horizon.end_date})",
        company=run.company,
        question=run.question,
        time_horizon=run.time_horizon,
        generated_at=utc_now(),
        report_status=status,
        data_mode=data_mode,
        llm_mode=run.llm_mode,
        is_fixture=is_fixture,
        as_of=as_of,
        key_metrics=composer.key_metrics(),
        recent_events=composer.event_entries(),
        claims=kept,
        withheld_claims=withheld,
        unknowns_and_gaps=gaps,
        source_quality_notes=composer.source_quality_notes() + review_notes,
        sources=composer.sources(),
        source_status=statuses,
        methodology=Methodology(
            steps=steps,
            deterministic_calculations=calculations,
            model_generated=model_generated,
            llm_mode=runtime.mode,
            llm_model=runtime.model,
            data_mode=data_mode,
            sources_used=sorted({e.source_name for e in ledger.values()}),
        ),
        limitations=limitations,
        review=ReviewSummary(
            reviewer=f"evidence_risk_reviewer ({runtime.model}) + deterministic validator",
            claims_total=len(kept) + len(withheld),
            supported=sum(1 for c in kept if c.validation_status.value == "supported"),
            needs_qualification=sum(1 for c in kept if c.validation_status.value == "needs_qualification"),
            withheld=len(withheld),
            numeric_checks_passed=numbers_checked - numbers_failed,
            numeric_checks_failed=numbers_failed,
        ),
        disclaimer=BRIEF_DISCLAIMER,
        fixture_disclosure=FIXTURE_BRIEF_DISCLOSURE if is_fixture else None,
    )
