"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { MicIcon, StopIcon } from "../icons";
import { useIsClient } from "../modal";
import { cn } from "../ui";

/*
 * Dictation (speech to text into the question box) with the browser's built-in speech
 * recognition, like the microphone in ChatGPT's composer. It is separate from a voice session:
 * nothing is spoken back. The browser's speech service does the transcription (Chrome sends the
 * audio to Google, Safari to Apple); MarketLens only ever receives the resulting text.
 */

interface RecognitionResultList {
  readonly length: number;
  [index: number]: { readonly isFinal: boolean; readonly 0: { readonly transcript: string } };
}
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { results: RecognitionResultList }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionConstructor = new () => Recognition;

function recognitionConstructor(): RecognitionConstructor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Microphone access was blocked. Allow it in the browser's site settings, then press the microphone again.",
  "service-not-allowed": "This browser doesn't allow its speech service here. Type your question instead.",
  "audio-capture": "No microphone was found. Connect one and try again.",
  "no-speech": "No speech was heard. Press the microphone and try again.",
  network: "Dictation needs a network connection to the browser's speech service.",
  "language-not-supported": "The browser's speech service doesn't support this language.",
};
const UNSUPPORTED = "Dictation isn't available in this browser (it works in Chrome, Edge and Safari). Type your question, or use a voice session.";

export interface Dictation {
  /** Null until the page knows whether the browser supports speech recognition. */
  supported: boolean | null;
  listening: boolean;
  note: string | null;
  toggle: () => void;
  stop: () => void;
  dismiss: () => void;
}

/** `onText` receives the whole transcript of the current dictation, updated as the user speaks. */
export function useDictation(onText: (spoken: string) => void): Dictation {
  const client = useIsClient();
  const supported = client ? recognitionConstructor() !== null : null;
  const [listening, setListening] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const latestOnText = useRef(onText);
  useEffect(() => {
    latestOnText.current = onText;
  }, [onText]);
  useEffect(() => () => recognition.current?.abort(), []);

  const stop = useCallback(() => recognition.current?.stop(), []);
  const dismiss = useCallback(() => setNote(null), []);

  const toggle = () => {
    if (listening) {
      stop();
      return;
    }
    const Constructor = recognitionConstructor();
    if (!Constructor) {
      setNote(UNSUPPORTED);
      return;
    }
    const rec = new Constructor();
    rec.lang = navigator.language || "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (event) => {
      let spoken = "";
      for (let i = 0; i < event.results.length; i++) spoken += event.results[i][0].transcript;
      latestOnText.current(spoken.trim());
    };
    rec.onerror = (event) => {
      if (event.error !== "aborted") setNote(ERRORS[event.error] ?? "Dictation stopped after an error. Try again.");
    };
    rec.onend = () => {
      recognition.current = null;
      setListening(false);
    };
    recognition.current = rec;
    setNote(null);
    try {
      rec.start();
      setListening(true);
    } catch {
      recognition.current = null;
      setNote("Dictation couldn't start. Try again.");
    }
  };

  return { supported, listening, note, toggle, stop, dismiss };
}

/** The dictation microphone in the research bar. */
export function DictationButton({ dictation, onToggle, disabled }: { dictation: Dictation; onToggle: () => void; disabled?: boolean }) {
  const unavailable = dictation.supported === false || disabled;
  const label = dictation.listening
    ? "Stop dictation"
    : disabled
      ? "Dictation is off during a voice session"
      : dictation.supported === false
        ? "Dictation isn't available in this browser"
        : "Dictate your question";
  return (
    <button
      type="button"
      onClick={() => {
        if (!disabled) onToggle();
      }}
      aria-label={label}
      title={label}
      aria-pressed={dictation.listening}
      aria-disabled={unavailable && !dictation.listening ? true : undefined}
      data-testid="dictation-button"
      data-state={dictation.listening ? "listening" : unavailable ? "unavailable" : "ready"}
      className={cn(
        "grid h-9 w-9 shrink-0 place-items-center rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
        dictation.listening && "animate-pulse bg-critical text-white",
        !dictation.listening && !unavailable && "text-ink-2 hover:bg-sunken hover:text-ink",
        !dictation.listening && unavailable && "cursor-not-allowed text-ink-3/60",
      )}
    >
      {dictation.listening ? <StopIcon /> : <MicIcon />}
    </button>
  );
}
