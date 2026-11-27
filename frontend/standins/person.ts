"use client";

/**
 * Who the demonstration bar says is asking. In Flow, sign-in names the person; here the bar
 * does, and the demonstration's fetch passes the choice on with every call.
 */

import { useSyncExternalStore } from "react";

export const PERSON_HEADER = "X-Demo-Person";
export const PERSON_KEY = "ea-demo-person";

let chosen: string | null = null;
const listeners = new Set<() => void>();

function stored(): string | null {
  try {
    return window.localStorage.getItem(PERSON_KEY);
  } catch {
    return null;
  }
}

/** The person chosen in the bar, or null before the bar has learnt who is who. */
export function currentPerson(): string | null {
  if (chosen === null && typeof window !== "undefined") chosen = stored();
  return chosen;
}

export function choosePerson(personId: string): void {
  chosen = personId;
  try {
    window.localStorage.setItem(PERSON_KEY, personId);
  } catch {
    // A browser that keeps nothing still works for this visit.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePerson(): string | null {
  return useSyncExternalStore(subscribe, currentPerson, () => null);
}

/** Forget the choice, for tests. */
export function resetPerson(): void {
  chosen = null;
  try {
    window.localStorage.removeItem(PERSON_KEY);
  } catch {
    // Nothing kept.
  }
}
