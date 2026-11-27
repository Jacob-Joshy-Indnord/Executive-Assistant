"use client";

/**
 * Work done where it is shown: a row leaves at once when the manager acts on it, so its
 * count changes at once; if the action fails, the row comes back with the reason.
 */

import { useCallback, useRef, useState } from "react";
import { messageOf } from "@/lib/assistant/client";

export type InPlace = {
  /** Rows taken out while their action is on its way or after it worked. */
  gone: ReadonlySet<string>;
  /** The reason shown on a row whose action failed. */
  errors: Readonly<Record<string, string>>;
  isGone: (id: string) => boolean;
  /** Take the rows out, run the action, and put them back with the reason if it fails. */
  act: (ids: string | string[], action: () => Promise<unknown>) => Promise<boolean>;
  clearError: (id: string) => void;
};

export function useInPlace(afterSuccess?: () => void | Promise<void>): InPlace {
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const after = useRef(afterSuccess);
  after.current = afterSuccess;

  const act = useCallback(async (ids: string | string[], action: () => Promise<unknown>) => {
    const list = Array.isArray(ids) ? ids : [ids];
    setGone((current) => new Set([...current, ...list]));
    setErrors((current) => {
      const next = { ...current };
      for (const id of list) delete next[id];
      return next;
    });
    try {
      await action();
    } catch (failure) {
      const reason = messageOf(failure);
      setGone((current) => new Set([...current].filter((id) => !list.includes(id))));
      setErrors((current) => ({ ...current, ...Object.fromEntries(list.map((id) => [id, reason])) }));
      return false;
    }
    await after.current?.();
    return true;
  }, []);

  const clearError = useCallback((id: string) => {
    setErrors((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
  }, []);

  const isGone = useCallback((id: string) => gone.has(id), [gone]);

  return { gone, errors, isGone, act, clearError };
}

/**
 * Something that opens in place. Closing it moves the focus back to the control that
 * opened it, wherever the close was chosen from.
 */
export function useDisclosure<T extends HTMLElement = HTMLButtonElement>(initial = false) {
  const [open, setOpen] = useState(initial);
  const opener = useRef<T>(null);
  const close = useCallback(() => {
    setOpen(false);
    // After React has put the opener back in its closed state.
    window.setTimeout(() => opener.current?.focus(), 0);
  }, []);
  const toggle = useCallback(() => {
    if (open) close();
    else setOpen(true);
  }, [open, close]);
  return { open, setOpen, toggle, close, opener };
}

/** Several rows of a list, each opened in place; closing one returns focus to its opener. */
export function useOpenRows() {
  const [openId, setOpenId] = useState<string | null>(null);
  const openers = useRef(new Map<string, HTMLElement | null>());
  const register = useCallback(
    (id: string) => (element: HTMLElement | null) => {
      openers.current.set(id, element);
    },
    [],
  );
  const close = useCallback((id: string) => {
    setOpenId((current) => (current === id ? null : current));
    window.setTimeout(() => openers.current.get(id)?.focus(), 0);
  }, []);
  const toggle = useCallback(
    (id: string) => {
      if (openId === id) close(id);
      else setOpenId(id);
    },
    [openId, close],
  );
  return { openId, toggle, close, register };
}
