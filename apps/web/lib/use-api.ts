"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "./api/client";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch a JSON resource on mount (and when `path` changes). `null` path = idle. */
export function useApi<T>(path: string | null): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    if (path === null) return;
    let active = true;
    api<T>(path)
      .then((result) => {
        if (!active) return;
        setData(result);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof ApiError ? err : new ApiError("UNKNOWN", "Request failed.", 0));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, version]);

  return { data, error, loading, reload };
}

/** Poll a resource until `isDone(data)` returns true; keeps the last good data on errors. */
export function usePolling<T>(path: string, isDone: (data: T) => boolean, intervalMs = 800): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const doneRef = useRef(isDone);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    doneRef.current = isDone;
  }, [isDone]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const tick = () => {
      api<T>(path)
        .then((result) => {
          if (!active) return;
          failures = 0;
          setData(result);
          setError(null);
          setLoading(false);
          if (!doneRef.current(result)) timer = setTimeout(tick, intervalMs);
        })
        .catch((err: unknown) => {
          if (!active) return;
          failures += 1;
          setError(err instanceof ApiError ? err : new ApiError("UNKNOWN", "Request failed.", 0));
          setLoading(false);
          const retryable = err instanceof ApiError && (err.retryable || err.status === 0);
          if (retryable && failures < 8) timer = setTimeout(tick, Math.min(8000, intervalMs * 2 ** failures));
        });
    };
    tick();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [path, intervalMs, version]);

  return { data, error, loading, reload };
}
