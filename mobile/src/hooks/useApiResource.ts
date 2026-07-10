import { useCallback, useEffect, useState } from "react";

import { describeError, isConnectivityError } from "@/api/client";

interface ResourceState<T> {
  data: T | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  /** True when `data` is bundled demo data rather than a live API response. */
  isDemo: boolean;
}

/**
 * Fetches a resource on mount, supports pull-to-refresh, and — only for
 * connectivity-shaped failures (unconfigured backend, network error,
 * timeout) — falls back to bundled demo data so the screen stays useful
 * without a running backend. HTTP errors (e.g. bad API key, 500s) are
 * surfaced as real error states instead of being papered over with demo
 * data, since silently showing fake data there would hide a real
 * misconfiguration.
 */
export function useApiResource<T>(fetcher: () => Promise<T>, demoData: T) {
  const [state, setState] = useState<ResourceState<T>>({
    data: null,
    loading: true,
    refreshing: false,
    error: null,
    isDemo: false,
  });

  const load = useCallback(
    async (isRefresh: boolean) => {
      setState((s) => ({ ...s, loading: !isRefresh, refreshing: isRefresh, error: null }));
      try {
        const data = await fetcher();
        setState({ data, loading: false, refreshing: false, error: null, isDemo: false });
      } catch (err) {
        const message = describeError(err);
        if (isConnectivityError(err)) {
          setState({ data: demoData, loading: false, refreshing: false, error: message, isDemo: true });
        } else {
          setState((s) => ({ ...s, loading: false, refreshing: false, error: message }));
        }
      }
    },
    // fetcher/demoData are expected to be stable (module-level or useCallback'd by the caller)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  useEffect(() => {
    load(false);
  }, [load]);

  return {
    data: state.data,
    loading: state.loading,
    refreshing: state.refreshing,
    error: state.error,
    isDemo: state.isDemo,
    reload: () => load(false),
    refresh: () => load(true),
  };
}
