"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { expiryRefreshDelayMs } from "./access";
import { fetchCommercialAccess } from "./api";
import type { CommercialAccessSnapshot } from "./types";

type CommercialAccessContextValue = {
  /** null means not resolved yet; consumers must fail open in that state. */
  snapshot: CommercialAccessSnapshot | null;
  loading: boolean;
  refresh: () => Promise<void>;
};

const DEFAULT_CONTEXT: CommercialAccessContextValue = {
  snapshot: null,
  loading: false,
  refresh: async () => {},
};

const CommercialAccessContext =
  createContext<CommercialAccessContextValue>(DEFAULT_CONTEXT);

export function useCommercialAccess(): CommercialAccessContextValue {
  return useContext(CommercialAccessContext);
}
export function CommercialAccessProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] =
    useState<CommercialAccessSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const inFlight = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback((): Promise<void> => {
    if (inFlight.current) return inFlight.current;

    const request = fetchCommercialAccess()
      .then((next) => {
        if (mounted.current) setSnapshot(next);
      })
      .catch(() => {
        // Preserve the last verified snapshot. With no snapshot, consumers
        // remain open so a network/proxy failure can never look like expiry.
      })
      .finally(() => {
        if (mounted.current) setLoading(false);
        inFlight.current = null;
      });
    inFlight.current = request;
    return request;
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  useEffect(() => {
    const onFocus = () => void refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  useEffect(() => {
    const delay = expiryRefreshDelayMs(snapshot);
    if (delay === null) return;
    const timer = window.setTimeout(() => void refresh(), delay);
    return () => window.clearTimeout(timer);
  }, [snapshot, refresh]);

  const value = useMemo(
    () => ({ snapshot, loading, refresh }),
    [snapshot, loading, refresh],
  );

  return (
    <CommercialAccessContext.Provider value={value}>
      {children}
    </CommercialAccessContext.Provider>
  );
}
