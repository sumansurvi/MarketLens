"""The voice agent's three research tools, independent of the LiveKit runtime.

Outputs are plain text for the voice model. Results come only from the backend: the start
tool never describes findings, the status tool reports what is running, and the summary tool
returns validated sentences with their caveats. Failures raise ``VoiceToolError`` carrying a
user-safe message so the agent can say what went wrong instead of guessing.
"""

from __future__ import annotations

from typing import Any, Literal

from marketlens_voice.backend import BackendError, VoiceBackendClient

Horizon = Literal["1m", "3m", "6m", "1y"]
Focus = Literal["overview", "market", "financials", "risks", "news", "unknowns"]
TOOL_NAMES = ("run_company_research", "get_research_run_status", "get_brief_summary")


class VoiceToolError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _error(exc: BackendError) -> VoiceToolError:
    if exc.code == "UNKNOWN_SYMBOL":
        return VoiceToolError(f"{exc.message} Ask the user for a covered ticker; do not guess another company.")
    if exc.code in ("INVALID_SYMBOL", "INVALID_INPUT"):
        return VoiceToolError(f"{exc.message} Ask the user to repeat or spell the ticker.")
    if exc.code == "VOICE_SESSION_EXPIRED":
        return VoiceToolError(exc.message)
    if exc.code == "NOT_FOUND":
        return VoiceToolError(f"{exc.message} Offer to start research if the user names a company.")
    return VoiceToolError(
        f"The research service could not complete that request ({exc.message}). Tell the user once and offer "
        "to try again or to use the text research form."
    )


class ResearchVoiceTools:
    """Session-scoped tool state: the room's backend client and the active run."""

    def __init__(self, backend: VoiceBackendClient) -> None:
        self.backend = backend
        self.active_run_id: str | None = None
        self.active_symbol: str | None = None

    async def run_company_research(self, symbol: str, question: str, horizon: Horizon | None = None) -> str:
        try:
            data = await self.backend.start_research(symbol=symbol, question=question, horizon=horizon)
        except BackendError as exc:
            raise _error(exc) from exc
        self.active_run_id, self.active_symbol = str(data["run_id"]), str(data["symbol"])
        company = data.get("company_name") or data["symbol"]
        window = "the default three-month window" if data.get("window_is_default") else "the requested window"
        lead = (
            f"Research on {company} ({data['symbol']}) is already running; no new run was started."
            if data.get("already_running")
            else f"Research started on {company} ({data['symbol']}) for {window}."
        )
        return (
            f"{lead} Status: {data['status']}. Tell the user it is underway and usually takes under a minute, and "
            "that the full report will appear on screen. Do not describe any results until get_brief_summary "
            "returns them."
        )

    async def get_research_run_status(self, run_id: str | None = None) -> str:
        data = await self._call(self.backend.status, run_id or self.active_run_id)
        return status_text(data)

    async def get_brief_summary(self, run_id: str | None = None, focus: Focus = "overview") -> str:
        target = run_id or self.active_run_id
        try:
            data = await self.backend.summary(target, focus)
        except BackendError as exc:
            raise _error(exc) from exc
        return summary_text(data)

    async def _call(self, method: Any, run_id: str | None) -> dict[str, Any]:
        try:
            return await method(run_id)
        except BackendError as exc:
            raise _error(exc) from exc


def status_text(data: dict[str, Any]) -> str:
    parts = [str(data["status_sentence"])]
    if not data.get("is_terminal"):
        step = f" Current step: {data['current_step']}" if data.get("current_step") else ""
        parts.append(
            f"Stage: {data['stage_label']} ({data['elapsed_seconds']} seconds so far).{step} Do not describe "
            "results yet; tell the user you are still checking and will report when it is ready."
        )
    elif data.get("partial_reasons"):
        parts.append("Partial because: " + " ".join(data["partial_reasons"]))
    if data.get("is_terminal") and data["status"] in ("completed", "partial"):
        parts.append("Use get_brief_summary to give the results.")
    return " ".join(parts)


def summary_text(data: dict[str, Any]) -> str:
    lines = [str(data["status_sentence"])]
    if data.get("window"):
        lines.append(f"Window: {data['window']}.")
    caveats = list(data.get("caveats") or [])
    if caveats:
        lines.append("Caveats to mention: " + " ".join(caveats))
    sentences = list(data.get("sentences") or [])
    if sentences:
        lines.append(
            f"Validated findings for '{data['focus']}' (say these in plain words; do not add or change numbers):"
        )
        lines += [f"- {s}" for s in sentences]
    topics = ", ".join(data.get("follow_up_topics") or [])
    if topics:
        lines.append(f"Offer details on: {topics}.")
    lines.append(str(data.get("details_on_screen") or ""))
    return "\n".join(line for line in lines if line)
