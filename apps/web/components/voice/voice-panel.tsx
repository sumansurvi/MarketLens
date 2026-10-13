"use client";

import {
  type ReceivedMessage,
  RoomAudioRenderer,
  SessionProvider,
  type TrackReferenceOrPlaceholder,
  useAgent,
  useAudioPlayback,
  useMultibandTrackVolume,
  useSession,
  useSessionMessages,
} from "@livekit/components-react";
import { ConnectionState, TokenSource } from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api/client";
import type { RunDetail, VoiceSessionResponse, VoiceSessionState } from "@/lib/api/types";
import { usePolling } from "@/lib/use-api";

import { StopIcon, WaveformIcon } from "../icons";
import { RunProgress } from "../run/progress";
import { useStatus } from "../status-provider";
import { buttonClass, cn, Notice } from "../ui";
import { useWorkspace } from "../workspace/workspace-context";
import { liveState, STATE_COPY, type VoiceUiState } from "./voice-state";

type FailureReason = "permission_denied" | "unreachable" | "agent_failed" | "agent_left" | "rate_limited" | "other";
type Outcome = { state: "permission_denied" | "error" | "ended" | "disabled"; message: string; reason?: FailureReason } | null;

const AGENT_JOIN_TIMEOUT_MS = 20_000;
// Codes the voice worker publishes in the `marketlens.voice_error` participant attribute.
const AGENT_STOP_REASONS: Record<string, string> = {
  speech_recognition_unavailable: "The voice assistant could not reach its speech-recognition service (check LiveKit Inference and VOICE_STT_MODEL).",
  speech_output_unavailable: "The voice assistant could not reach its speech-output service (check LiveKit Inference and VOICE_TTS_MODEL).",
  voice_model_unavailable: "The voice assistant could not reach its language model (check LiveKit Inference and VOICE_LLM_MODEL).",
  voice_pipeline_error: "The voice assistant stopped after an error.",
};
const RUN_POLL_MS = 2_500;
/** The voice assistant's name, shown on its screen and in the conversation (D-89). */
export const ASSISTANT_NAME = "Lens";
const ENDED: Outcome = { state: "ended", message: "Voice session ended. Reports stay in your research history." };
const NOT_CONFIGURED: Outcome = {
  state: "disabled",
  message: "Text research works fully without it. To turn voice on, see README › Configuration › Voice.",
};

async function checkMicrophone(): Promise<Outcome> {
  if (!navigator.mediaDevices?.getUserMedia) {
    return {
      state: "error",
      message: "This browser does not allow microphone access on this page. Use a current browser on http://localhost or https.",
    };
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
    return null;
  } catch (err) {
    const name = err instanceof DOMException ? err.name : "";
    if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
      return {
        state: "permission_denied",
        message: "Microphone access was blocked. Allow it in the browser's site settings and press the voice-session (sound-wave) button again, or type your question in the research bar.",
      };
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      return { state: "error", message: "No microphone was found. Connect one and try again, or use the research bar." };
    }
    return { state: "error", message: "The microphone could not be started. Try again, or use the research bar." };
  }
}

function describe(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Could not connect to the voice service. Check that LiveKit is reachable and the voice worker is running, or use the research bar.";
}

/** Local analytics (coded outcome only; nothing else about the session is reported). */
function reportVoice(event: "voice_connection_succeeded" | "voice_connection_failed", reason?: FailureReason): void {
  void api("/api/analytics/events", { method: "POST", json: reason ? { event, reason } : { event } }).catch(() => undefined);
}

function endBackendSession(room: string | null): Promise<unknown> {
  if (!room) return Promise.resolve();
  return api(`/api/voice/session/${room}/end`, { method: "POST" }).catch(() => undefined);
}

/** Voice state shared by the mic button in the research bar and the tray above it. */
export interface VoiceControl {
  /** Null until health has loaded. */
  available: boolean | null;
  live: boolean;
  checking: boolean;
  attempt: number;
  outcome: Outcome;
  /** Bumped by the mic button to end the live session. */
  stopRequest: number;
  toggle: () => void;
  close: (outcome: Outcome) => void;
  dismiss: () => void;
}

export function useVoiceControl(): VoiceControl {
  const { health } = useStatus();
  const [attempt, setAttempt] = useState(0);
  const [live, setLive] = useState(false);
  const [checking, setChecking] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [stopRequest, setStopRequest] = useState(0);
  const available = health ? health.voice_enabled : null;

  const start = async () => {
    setOutcome(null);
    setChecking(true);
    const problem = await checkMicrophone();
    setChecking(false);
    if (problem) {
      reportVoice("voice_connection_failed", problem.state === "permission_denied" ? "permission_denied" : "other");
      setOutcome(problem);
      return;
    }
    setAttempt((n) => n + 1);
    setLive(true);
  };
  const toggle = () => {
    if (live) setStopRequest((n) => n + 1);
    else if (checking || available === null) return;
    else if (!available) setOutcome(NOT_CONFIGURED);
    else void start();
  };
  const close = useCallback((result: Outcome) => {
    setLive(false);
    setOutcome(result);
  }, []);
  const dismiss = useCallback(() => setOutcome(null), []);

  return { available, live, checking, attempt, outcome, stopRequest, toggle, close, dismiss };
}

/** The voice-session button (sound bars) in the research bar, like ChatGPT's voice mode: starts a
 * two-way conversation with the research assistant, or ends the live one. Dictation is separate. */
export function VoiceButton({ voice }: { voice: VoiceControl }) {
  const label = voice.live
    ? "End voice session"
    : voice.checking
      ? "Checking microphone…"
      : voice.available === false
        ? "Voice not configured"
        : `Talk to ${ASSISTANT_NAME}, the voice research assistant`;
  return (
    <button
      type="button"
      onClick={voice.toggle}
      aria-label={label}
      title={label}
      aria-pressed={voice.live}
      aria-disabled={voice.available !== true || voice.checking ? true : undefined}
      data-testid="voice-button"
      data-state={voice.live ? "live" : voice.available === false ? "disabled" : "ready"}
      className={cn(
        "grid h-9 w-9 shrink-0 place-items-center rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
        voice.live && "bg-accent text-plane shadow-[0_0_0_4px] shadow-accent/20 hover:opacity-90",
        !voice.live && voice.available === true && "bg-sunken text-ink hover:bg-accent-wash hover:text-accent",
        !voice.live && voice.available !== true && "cursor-not-allowed text-ink-3/60",
        voice.checking && "animate-pulse",
      )}
    >
      {voice.live ? <StopIcon /> : <WaveformIcon />}
    </button>
  );
}

/** Shown just above the research bar: the live session, or how the last attempt ended. */
export function VoiceTray({ voice }: { voice: VoiceControl }) {
  if (!voice.live && !voice.outcome) return null;
  if (voice.live) return <LiveVoiceSession key={voice.attempt} onClose={voice.close} stopRequest={voice.stopRequest} />;
  return (
    <div className="mb-2 rounded-2xl border border-line bg-surface p-3 shadow-sm">
      {
        voice.outcome && (
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <StatusLine state={voice.outcome.state} detail={voice.outcome.message} />
            </div>
            <button type="button" onClick={voice.dismiss} aria-label="Dismiss" className="rounded px-1.5 text-lg leading-none text-ink-3 hover:text-ink">
              ×
            </button>
          </div>
        )
      }
    </div>
  );
}

function StatusLine({
  state,
  detail,
  muted,
  centered = false,
  compact = false,
}: {
  state: VoiceUiState;
  detail?: string;
  muted?: boolean;
  centered?: boolean;
  compact?: boolean;
}) {
  const copy = STATE_COPY[state];
  const dot = {
    idle: "bg-baseline",
    active: "bg-accent animate-pulse",
    warning: "bg-warning",
    critical: "bg-critical",
  }[copy.tone];
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="voice-state"
      data-state={state}
      className={cn("flex gap-2.5", centered ? "flex-col items-center text-center" : "items-start")}
    >
      {!centered && <span aria-hidden="true" className={cn("mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full", dot)} />}
      <div className={centered ? "" : "text-sm"}>
        <p className={cn("font-medium text-ink", centered && "text-xl font-semibold")}>
          {copy.label}
          {muted ? " · microphone muted" : ""}
        </p>
        {!compact && (detail || copy.detail) && <p className={cn("mt-0.5 text-ink-2", centered && "max-w-md text-sm")}>{detail || copy.detail}</p>}
      </div>
    </div>
  );
}

/** The voice-started run for this room, polled while connected (null until research starts). */
function useVoiceRun(room: string | null, connected: boolean): VoiceSessionState | null {
  const [state, setState] = useState<VoiceSessionState | null>(null);
  useEffect(() => {
    if (!connected || !room) return;
    let stopped = false;
    const tick = async () => {
      try {
        const next = await api<VoiceSessionState>(`/api/voice/session/${room}`);
        if (!stopped) setState(next);
      } catch {
        // Transient; the next poll retries. The run itself lives on the server.
      }
    };
    void tick();
    const timer = setInterval(tick, RUN_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [connected, room]);
  return state;
}

function LiveVoiceSession({ onClose, stopRequest }: { onClose: (outcome: Outcome) => void; stopRequest: number }) {
  const { openRun, refresh } = useWorkspace();
  const [room, setRoom] = useState<string | null>(null);
  // One voice room per attempt: the SDK fetches credentials on mount, on start and again (forced)
  // on end or disconnect to pre-warm a new room; every fetch here returns this attempt's grant.
  const [voice] = useState(() => {
    let grant: Promise<VoiceSessionResponse> | null = null;
    return {
      tokenSource: TokenSource.custom(async () => {
        grant ??= api<VoiceSessionResponse>("/api/voice/session", { method: "POST" });
        const value = await grant;
        setRoom(value.room_name);
        return { serverUrl: value.server_url, participantToken: value.participant_token };
      }),
      /** Mark this attempt's room ended on the server (nothing to do if no room was created). */
      async end(): Promise<void> {
        const value = grant ? await grant.catch(() => null) : null;
        if (value) await endBackendSession(value.room_name);
      },
    };
  });
  const session = useSession(voice.tokenSource, { agentConnectTimeoutMilliseconds: AGENT_JOIN_TIMEOUT_MS });
  const agent = useAgent(session);
  const { messages } = useSessionMessages(session);
  const run = useVoiceRun(room, session.isConnected);
  const [muted, setMuted] = useState(false);
  const started = useRef(false);
  const connectedOnce = useRef(false);
  const closing = useRef(false);

  const finish = useCallback(
    (outcome: Outcome) => {
      if (closing.current) return;
      closing.current = true;
      if (outcome?.state === "error") reportVoice("voice_connection_failed", outcome.reason ?? "other");
      void session.end();
      void voice.end();
      onClose(outcome);
    },
    [session, voice, onClose],
  );

  // The mic button in the research bar ends the session (requests made before this one started don't count).
  const stopAtStart = useRef(stopRequest);
  useEffect(() => {
    if (stopRequest !== stopAtStart.current) finish(ENDED);
  }, [stopRequest, finish]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    session.start({ tracks: { microphone: { enabled: true } } }).catch((err: unknown) => {
      const reason = err instanceof ApiError && err.code === "RATE_LIMITED" ? "rate_limited" : "unreachable";
      finish({ state: "error", message: describe(err), reason });
    });
  }, [session, finish]);

  useEffect(() => {
    if (session.connectionState === ConnectionState.Connected) connectedOnce.current = true;
    if (connectedOnce.current && session.connectionState === ConnectionState.Disconnected) {
      finish({
        state: "error",
        message: "The voice connection was lost. Research already started keeps running; open it from your research history, or start a new voice session.",
        reason: "unreachable",
      });
    }
  }, [session.connectionState, finish]);

  const agentJoined = useRef(false);
  const agentStopCode = useRef<string | null>(null);
  useEffect(() => {
    if (agent.isConnected && !agentJoined.current) {
      agentJoined.current = true;
      reportVoice("voice_connection_succeeded");
    }
    const code = "attributes" in agent ? agent.attributes?.["marketlens.voice_error"] : undefined;
    if (code) agentStopCode.current = code;
    const left = agent.state === "disconnected" && session.connectionState === ConnectionState.Connected;
    if (agentJoined.current && (left || agentStopCode.current)) {
      const reason = AGENT_STOP_REASONS[agentStopCode.current ?? ""] ?? "The voice assistant left the session unexpectedly.";
      finish({
        state: "error",
        message: `${reason} Research already started keeps running, and text research still works.`,
        reason: "agent_left",
      });
    }
  }, [agent, session.connectionState, finish]);

  useEffect(() => {
    if (agent.state === "failed") {
      const reasons = agent.failureReasons?.join(" ") || "The voice assistant did not join.";
      finish({
        state: "error",
        message: `${reasons} Check that the voice worker is running (see README), or use the research bar.`,
        reason: "agent_failed",
      });
    }
  }, [agent.state, agent.failureReasons, finish]);

  const toggleMute = async () => {
    const next = !muted;
    await session.room.localParticipant.setMicrophoneEnabled(!next);
    setMuted(next);
  };

  const researchRunning = run?.active_run_status === "queued" || run?.active_run_status === "running";
  const state = liveState(session.connectionState, agent.state, researchRunning);

  // Audio (D-88): unlock playback as soon as the room connects; if the browser still blocks it,
  // a large button asks for the one click it needs.
  const { canPlayAudio, startAudio } = useAudioPlayback(session.room);
  useEffect(() => {
    if (session.isConnected && !canPlayAudio) startAudio().catch(() => undefined);
  }, [session.isConnected, canPlayAudio, startAudio]);

  // The run opens behind the voice screen as soon as research starts, so the brief is there when it finishes.
  const activeRunId = run?.active_run_id ?? null;
  const openedRun = useRef<string | null>(null);
  useEffect(() => {
    if (activeRunId && openedRun.current !== activeRunId) {
      openedRun.current = activeRunId;
      openRun(activeRunId);
      refresh(); // the research history lists the voice-started run right away (D-90)
    }
  }, [activeRunId, openRun, refresh]);

  // Safety net (D-89): if the assistant says it is checking but no run appears, say so instead of
  // leaving the user waiting for nothing.
  const lastAgentLine = [...messages].reverse().find((m) => speaker(m) === ASSISTANT_NAME)?.message ?? "";
  const claimsChecking = /\b(checking|looking (into|up)|researching|pulling)\b/i.test(lastAgentLine);
  const [stalledLine, setStalledLine] = useState<string | null>(null);
  useEffect(() => {
    if (!claimsChecking || activeRunId) return;
    const line = lastAgentLine; // restarts while the line is still being spoken; fires once it settles
    const timer = setTimeout(() => setStalledLine(line), NO_RUN_WARNING_MS);
    return () => clearTimeout(timer);
  }, [claimsChecking, activeRunId, lastAgentLine]);
  const researchNotStarted = !activeRunId && claimsChecking && stalledLine === lastAgentLine;

  // Big while talking and researching; shrinks to a bar above the research bar once the answer is ready.
  const finishedRun = activeRunId && run?.active_run_status && FINISHED.has(run.active_run_status) ? activeRunId : null;
  const [reopenedFor, setReopenedFor] = useState<string | null>(null);
  const expanded = !(finishedRun && reopenedFor !== finishedRun);
  const expand = () => setReopenedFor(finishedRun);

  const audioButton = !canPlayAudio && session.isConnected && (
    <button
      type="button"
      onClick={() => startAudio().catch(() => undefined)}
      className="inline-flex items-center gap-2 rounded-full bg-accent px-4 py-2 text-sm font-semibold text-plane shadow hover:opacity-90"
    >
      <SpeakerIcon /> Turn on the assistant&apos;s voice
    </button>
  );
  const muteButton = (
    <button type="button" onClick={toggleMute} aria-pressed={muted} className={buttonClass.secondarySm} disabled={!session.isConnected}>
      {muted ? "Unmute mic" : "Mute mic"}
    </button>
  );
  // One way to stop (D-90): ✕ ends the voice session, as in ChatGPT's voice mode. The screen also
  // shrinks to a bar on its own when the answer is ready.
  const endButton = (
    <button
      type="button"
      onClick={() => finish(ENDED)}
      aria-label="End voice session"
      title="End voice session"
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-ink-2 transition hover:bg-critical/10 hover:text-critical-text"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" className="h-5 w-5">
        <path d="M6 6l12 12M18 6 6 18" />
      </svg>
    </button>
  );

  return (
    <SessionProvider session={session}>
      {expanded ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-3 backdrop-blur-sm sm:p-6" role="dialog" aria-modal="true" aria-label={`${ASSISTANT_NAME}, MarketLens voice assistant`}>
          <div className="flex max-h-[92dvh] min-h-[min(36rem,92dvh)] w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-line bg-surface shadow-2xl">
            <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
              <p className="text-sm font-semibold text-ink">
                {ASSISTANT_NAME} <span className="font-normal text-ink-3">· MarketLens voice assistant</span>
              </p>
              {endButton}
            </div>
            <div className="flex flex-1 flex-col justify-center space-y-5 overflow-y-auto px-6 py-8">
              <div className="flex flex-col items-center gap-3 text-center">
                <VoiceOrb state={state} track={agent.microphoneTrack} />
                <StatusLine state={state} muted={muted} centered />
                {audioButton}
              </div>
              {researchRunning && activeRunId && <VoiceRunProgress runId={activeRunId} />}
              {researchNotStarted && (
                <Notice tone="warning" title="The research hasn't started">
                  {ASSISTANT_NAME} said it was checking, but no research began. Say “start the research” again, or type the ticker in the
                  research bar.
                </Notice>
              )}
              <Transcript messages={messages} tall />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-5 py-3">
              <p className="text-xs text-ink-3">Close with ✕ to end the session. The conversation isn&apos;t stored; the research is saved in your history.</p>
              {muteButton}
            </div>
          </div>
        </div>
      ) : (
        <div className="mb-2 flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-surface px-3 py-2 shadow-sm">
          <VoiceOrb state={state} track={agent.microphoneTrack} small />
          <span className="text-sm font-semibold text-ink">{ASSISTANT_NAME}</span>
          <div className="min-w-0 flex-1">
            <StatusLine state={state} muted={muted} compact />
          </div>
          {audioButton}
          <button type="button" onClick={expand} className={buttonClass.secondarySm}>
            Expand
          </button>
          {muteButton}
          {endButton}
        </div>
      )}
      <RoomAudioRenderer volume={1} />
    </SessionProvider>
  );
}

const FINISHED = new Set(["completed", "partial", "failed", "cancelled"]);
const NO_RUN_WARNING_MS = 15_000;

function SpeakerIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4 w-4">
      <path d="M11 5 6 9H3v6h3l5 4z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
    </svg>
  );
}

/** Bars that move with the assistant's voice (so it is visible that it is speaking), or a calm pulse while it listens or works. */
function VoiceOrb({ state, track, small = false }: { state: VoiceUiState; track?: TrackReferenceOrPlaceholder; small?: boolean }) {
  const bands = useMultibandTrackVolume(track, { bands: 5, loPass: 100, hiPass: 600 });
  const speaking = state === "speaking";
  const busy = state === "processing" || state === "research_in_progress" || state === "connecting" || state === "reconnecting";
  return (
    <div
      aria-hidden="true"
      className={cn(
        "flex items-center justify-center gap-1 rounded-full",
        small ? "h-9 w-9 bg-accent-wash" : "h-28 w-28 bg-accent-wash shadow-[0_0_0_10px] shadow-accent/10",
        busy && "animate-pulse",
      )}
    >
      {bands.map((level, i) => (
        <span
          key={i}
          className={cn("rounded-full bg-accent transition-[height] duration-75", small ? "w-0.5" : "w-2")}
          style={{ height: `${Math.round((small ? 6 : 14) + (speaking ? level : 0.08) * (small ? 18 : 60))}px` }}
        />
      ))}
    </div>
  );
}

/** The voice-started run's progress inside the voice screen, so it is clear that an answer is on its way. */
function VoiceRunProgress({ runId }: { runId: string }) {
  const isDone = useCallback((d: RunDetail) => FINISHED.has(d.run.status) && !d.is_active, []);
  const { data, reload } = usePolling<RunDetail>(`/api/research/${runId}`, isDone, 2500);
  const [cancelling, setCancelling] = useState(false);
  if (!data) {
    return (
      <div className="flex items-center gap-3 rounded-2xl border border-line bg-raised px-4 py-3 text-sm text-ink-2">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-hairline border-t-accent" aria-hidden="true" />
        Starting the research…
      </div>
    );
  }
  return (
    <RunProgress
      detail={data}
      cancelling={cancelling}
      onCancel={async () => {
        setCancelling(true);
        try {
          await api(`/api/research/${runId}/cancel`, { method: "POST" });
          reload();
        } finally {
          setCancelling(false);
        }
      }}
    />
  );
}

function speaker(message: ReceivedMessage): string {
  if (message.type === "userTranscript") return "You";
  if (message.type === "agentTranscript") return ASSISTANT_NAME;
  return message.from?.isLocal ? "You" : ASSISTANT_NAME;
}

/** The whole conversation, kept scrolled to the newest line (like a chat). */
function Transcript({ messages, tall = false }: { messages: ReceivedMessage[]; tall?: boolean }) {
  const list = useRef<HTMLOListElement>(null);
  const last = messages.at(-1);
  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, last?.message]);
  if (messages.length === 0) {
    return <p className="text-center text-sm text-ink-3">Try: “Research Apple, ticker A A P L, over the last three months.”</p>;
  }
  return (
    <ol
      ref={list}
      aria-label="Conversation"
      aria-live="polite"
      className={cn("space-y-2 overflow-y-auto rounded-xl border border-line bg-sunken px-4 py-3 text-sm", tall ? "max-h-64" : "max-h-40")}
    >
      {messages.map((message) => (
        <li key={message.id}>
          <span className="text-xs font-semibold text-ink-3">{speaker(message)}: </span>
          <span className="text-ink">{message.message}</span>
        </li>
      ))}
    </ol>
  );
}
