"""The LiveKit voice agent: conversation only, with exactly three research tools.

There is no save, watchlist, trading or MCP tool here (decision D-23): the agent can start
research for its room and read status and validated summaries through the backend's internal
voice API. Writes stay behind the on-screen confirmation flow.
"""

from __future__ import annotations

from livekit.agents import Agent, RunContext, ToolError, function_tool

from marketlens_voice.progress import ProgressWatcher
from marketlens_voice.prompts import INSTRUCTIONS
from marketlens_voice.tools import Focus, Horizon, ResearchVoiceTools, VoiceToolError


class ResearchAssistant(Agent):
    def __init__(self, tools: ResearchVoiceTools, watcher: ProgressWatcher | None = None) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.research = tools
        self.watcher = watcher

    @function_tool()
    async def run_company_research(
        self, context: RunContext, symbol: str, question: str, horizon: Horizon | None = None
    ) -> str:
        """Start evidence-grounded research on one US-listed company. Call once per user request, after the ticker
        is confirmed. Returns immediately; results arrive later through get_brief_summary.

        Args:
            symbol: The stock ticker confirmed with the user, for example AAPL.
            question: The user's research question in their own words.
            horizon: Optional window: 1m, 3m, 6m or 1y. Omit it to use the default three months.
        """
        try:
            text = await self.research.run_company_research(symbol, question, horizon)
        except VoiceToolError as exc:
            raise ToolError(exc.message) from exc
        if self.watcher is not None and self.research.active_run_id:
            self.watcher.watch(self.research.active_run_id)
        return text

    @function_tool()
    async def get_research_run_status(self, context: RunContext, run_id: str | None = None) -> str:
        """Report whether the current research run is still working, complete, partial or failed.

        Args:
            run_id: Optional run to check; omit it for the run started in this conversation.
        """
        try:
            return await self.research.get_research_run_status(run_id)
        except VoiceToolError as exc:
            raise ToolError(exc.message) from exc

    @function_tool()
    async def get_brief_summary(self, context: RunContext, focus: Focus = "overview", run_id: str | None = None) -> str:
        """Get validated, speakable findings from the finished report, for one focus area.

        Args:
            focus: overview, market, financials, risks, news or unknowns.
            run_id: Optional run; omit it for the run started in this conversation.
        """
        try:
            return await self.research.get_brief_summary(run_id, focus)
        except VoiceToolError as exc:
            raise ToolError(exc.message) from exc
