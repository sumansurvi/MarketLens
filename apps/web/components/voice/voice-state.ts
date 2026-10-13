import type { AgentState } from "@livekit/components-react";
import { ConnectionState } from "livekit-client";

/** Voice UI states (VOICE_AND_AGENTS §7). */
export type VoiceUiState =
  | "disabled"
  | "ready"
  | "connecting"
  | "listening"
  | "processing"
  | "speaking"
  | "research_in_progress"
  | "reconnecting"
  | "permission_denied"
  | "error"
  | "ended";

/** State while a session is live: connection first, then what the agent is doing. */
export function liveState(connection: ConnectionState, agent: AgentState, researchRunning: boolean): VoiceUiState {
  if (connection === ConnectionState.Reconnecting || connection === ConnectionState.SignalReconnecting) {
    return "reconnecting";
  }
  if (connection !== ConnectionState.Connected) return "connecting";
  if (agent === "speaking") return "speaking";
  if (agent === "thinking") return "processing";
  if (agent === "listening") return researchRunning ? "research_in_progress" : "listening";
  return "connecting";
}

export const STATE_COPY: Record<VoiceUiState, { label: string; detail: string; tone: "idle" | "active" | "warning" | "critical" }> = {
  disabled: { label: "Voice not configured", detail: "Text research works fully without voice.", tone: "idle" },
  ready: {
    label: "Ready",
    detail: "The microphone turns on only after you press the voice-session (sound-wave) button. Audio goes to the voice assistant and is not recorded.",
    tone: "idle",
  },
  connecting: { label: "Connecting…", detail: "Joining the voice room and waiting for the assistant.", tone: "active" },
  listening: { label: "Listening", detail: "Speak naturally. You can interrupt the assistant at any time.", tone: "active" },
  processing: { label: "Thinking", detail: "Working on your request.", tone: "active" },
  speaking: { label: "Speaking", detail: "Start talking to interrupt.", tone: "active" },
  research_in_progress: {
    label: "Research in progress",
    detail: "Checking market data, filings and news. The assistant will tell you when the report is ready.",
    tone: "active",
  },
  reconnecting: {
    label: "Reconnecting…",
    detail: "The connection dropped. Research keeps running on the server while voice reconnects.",
    tone: "warning",
  },
  permission_denied: { label: "Microphone blocked", detail: "", tone: "warning" },
  error: { label: "Voice unavailable", detail: "", tone: "critical" },
  ended: { label: "Session ended", detail: "", tone: "idle" },
};
