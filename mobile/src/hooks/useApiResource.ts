import { useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";

import { describeError } from "@/api/client";
import { useConnection } from "@/store/connection";

interface ResourceState<T> {
  data: T | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
}

/**
 * Fetch a resource when the screen gains focus (and optionally every
 * `pollMs` while focused), with pull-to-refresh. Errors are shown as-is;
 * there is no demo/offline data, so the phone never shows made-up state.
 */
export function useApiResource<T>(fetcher: () => Promise<T>, deps: unknown[] = [], pollMs?: number) {
  const [state, setState] = useState<ResourceState<T>>({ data: null, loading: true, refreshing: false, error: null });
  const baseUrl = useConnection((s) => s.baseUrl);
  const apiKey = useConnection((s) => s.apiKey);
  const ready = useConnection((s) => s.ready);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async (mode: "initial" | "refresh" | "silent") => {
    setState((s) => ({ ...s, loading: mode === "initial" && s.data === null, refreshing: mode === "refresh", error: mode === "silent" ? s.error : null }));
    try {
      const data = await fetcherRef.current();
      setState({ data, loading: false, refreshing: false, error: null });
    } catch (err) {
      setState((s) => ({ ...s, loading: false, refreshing: false, error: describeError(err) }));
    }
  }, []);

  // Runs when the screen gains focus and whenever the connection or `deps` change.
  useFocusEffect(
    useCallback(() => {
      if (!ready) return undefined;
      void load("initial");
      if (!pollMs) return undefined;
      const timer = setInterval(() => void load("silent"), pollMs);
      return () => clearInterval(timer);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [ready, baseUrl, apiKey, load, pollMs, ...deps])
  );

  return {
    ...state,
    setData: (data: T) => setState((s) => ({ ...s, data })),
    reload: () => load("initial"),
    refresh: () => load("refresh"),
  };
}
