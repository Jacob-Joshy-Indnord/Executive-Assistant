"use client";

/**
 * Quiet refresh: while the page is open and visible it reloads its data every 30 seconds.
 * The data is replaced in place, so what is open, typed or focused stays where it was.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./client";

export const REFRESH_MS = 30_000;

/** Calls `refresh` every `intervalMs` while the document is visible, and at once when it
 * becomes visible again after being hidden. */
export function useVisibleRefresh(refresh: () => void, intervalMs: number = REFRESH_MS): void {
  const latest = useRef(refresh);
  useEffect(() => {
    latest.current = refresh;
  }, [refresh]);

  useEffect(() => {
    let hiddenSince: number | null = null;
    const tick = () => {
      if (document.visibilityState === "visible") latest.current();
    };
    const timer = window.setInterval(tick, intervalMs);
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        hiddenSince = Date.now();
      } else if (hiddenSince !== null) {
        const away = Date.now() - hiddenSince;
        hiddenSince = null;
        if (away >= intervalMs) latest.current();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs]);
}

export type Resource<T> = {
  data: T | undefined;
  /** The failure of the first load; later refreshes keep the last data instead. */
  error: ApiError | null;
  loading: boolean;
  reload: () => Promise<void>;
  /** Change the data in place, e.g. to take out a row at once (an optimistic change). */
  setData: (update: (current: T | undefined) => T | undefined) => void;
};

/**
 * Loads `load()` once and on every quiet refresh. A refresh that fails keeps what is on the
 * screen; one that answers after a newer load started is ignored.
 */
export function useResource<T>(load: () => Promise<T>, options: { refresh?: boolean } = {}): Resource<T> {
  const { refresh = true } = options;
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  const generation = useRef(0);
  const hasData = useRef(false);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  const reload = useCallback(async () => {
    const mine = ++generation.current;
    try {
      const next = await loadRef.current();
      if (mine !== generation.current) return;
      hasData.current = true;
      setDataState(next);
      setError(null);
    } catch (failure) {
      if (mine !== generation.current) return;
      if (!hasData.current) {
        setError(failure instanceof ApiError ? failure : new ApiError(0, "error", "Something went wrong. Try again."));
      }
    } finally {
      if (mine === generation.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const quiet = useCallback(() => {
    if (refresh) void reload();
  }, [refresh, reload]);
  useVisibleRefresh(quiet);

  const setData = useCallback((update: (current: T | undefined) => T | undefined) => {
    generation.current++; // a load already on its way would undo this change
    setDataState((current) => update(current));
  }, []);

  return { data, error, loading, reload, setData };
}
