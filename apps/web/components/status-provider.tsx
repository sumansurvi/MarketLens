"use client";

import { createContext, useContext } from "react";

import type { ApiError } from "@/lib/api/client";
import type { Health } from "@/lib/api/types";
import { useApi } from "@/lib/use-api";

interface StatusValue {
  health: Health | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

const StatusContext = createContext<StatusValue>({ health: null, error: null, loading: true, reload: () => {} });

export function StatusProvider({ children }: { children: React.ReactNode }) {
  const { data, error, loading, reload } = useApi<Health>("/api/health");
  return <StatusContext.Provider value={{ health: data, error, loading, reload }}>{children}</StatusContext.Provider>;
}

export function useStatus(): StatusValue {
  return useContext(StatusContext);
}
