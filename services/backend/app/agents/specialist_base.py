"""What every specialist has in common: a scope, a package builder and a deterministic fallback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from marketlens_core.models import BriefSection

from app.agents.contracts import MAX_FINDINGS_PER_SPECIALIST, SpecialistOutput
from app.agents.fixture_responses import FixtureContext
from app.agents.runtime import AgentRuntime
from app.services.evidence_collector import EvidenceBundle


@dataclass(frozen=True)
class SpecialistSpec:
    task: str
    agent: str
    name: str
    system_prompt: str
    sections: frozenset[BriefSection]
    build_package: Callable[[EvidenceBundle, str], dict[str, Any]]
    deterministic: Callable[[dict[str, Any], FixtureContext], Any]

    async def analyze(
        self, runtime: AgentRuntime, bundle: EvidenceBundle, question: str, ctx: FixtureContext
    ) -> tuple[SpecialistOutput, int]:
        """The specialist's typed output and how many findings beyond the limit were discarded."""
        output = await runtime.run(
            task=self.task,
            system_prompt=self.system_prompt,
            payload=self.build_package(bundle, question),
            output_model=SpecialistOutput,
            fixture_context=ctx,
        )
        dropped = max(0, len(output.findings) - MAX_FINDINGS_PER_SPECIALIST)
        output.findings = output.findings[:MAX_FINDINGS_PER_SPECIALIST]
        return output, dropped

    def fallback(self, ctx: FixtureContext) -> SpecialistOutput:
        """Clearly labeled deterministic summary used only when the model step fails."""
        return self.deterministic({}, ctx)
