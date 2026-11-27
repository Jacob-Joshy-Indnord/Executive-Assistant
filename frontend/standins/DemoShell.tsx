"use client";

/**
 * Wraps the demonstration's pages with the bar. Whenever the bar changes the person, the time,
 * a fault or the sample data, the page below is opened again, so it shows what follows.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useVisibleRefresh } from "@/lib/assistant/refresh";
import { createBarClient, messageOf, type BarClient, type BarState } from "./client";
import { DemoBar, type BarActions } from "./DemoBar";
import { choosePerson, currentPerson, usePerson } from "./person";

/** How often the bar quietly reads the stand-ins again, to show notices the pages caused. */
export const BAR_REFRESH_MS = 5_000;

export function DemoShell({ children, bar: given }: { children: ReactNode; bar?: BarClient }) {
  const bar = useMemo(() => given ?? createBarClient(), [given]);
  const person = usePerson();
  const [state, setState] = useState<BarState | null>(null);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const learn = useCallback((next: BarState) => {
    setState(next);
    const chosen = currentPerson();
    if (!chosen || !next.people.some((p) => p.id === chosen)) {
      const first = next.people[0];
      if (first) choosePerson(first.id);
    }
  }, []);

  const read = useCallback(() => {
    bar
      .state()
      .then((next) => {
        learn(next);
        setError(null);
      })
      .catch((failure: unknown) => setError(messageOf(failure)));
  }, [bar, learn]);

  useEffect(read, [read]);
  useVisibleRefresh(read, BAR_REFRESH_MS);

  /** Do one thing in the bar, then open the page again so that it follows. */
  const act = useCallback(
    async (change: () => Promise<BarState>) => {
      setBusy(true);
      setError(null);
      try {
        learn(await change());
        setVersion((v) => v + 1);
      } catch (failure) {
        setError(messageOf(failure));
      } finally {
        setBusy(false);
      }
    },
    [learn],
  );

  const actions: BarActions = useMemo(
    () => ({
      choosePerson: (personId) => choosePerson(personId),
      moveClock: (move) => void act(() => bar.moveClock(move)),
      setFault: (fault, on) => void act(() => bar.setFault(fault, on)),
      reset: () => void act(() => bar.reset()),
      setUp: (managerId) => void act(() => bar.setUp(managerId)),
      handOver: (flowId, newManagerId) => void act(() => bar.handOver(flowId, newManagerId)),
      end: (flowId) => void act(() => bar.end(flowId)),
      pauseAll: (paused) => void act(() => bar.pauseAll(paused)),
      changeAssignment: (assignmentId, next) => void act(() => bar.changeAssignment(assignmentId, next)),
    }),
    [act, bar],
  );

  const ready = state !== null && person !== null && state.people.some((p) => p.id === person);

  return (
    <>
      <DemoBar state={state} person={person} busy={busy} error={error} actions={actions} />
      {ready ? (
        <div key={`${person}:${version}`}>{children}</div>
      ) : (
        <p className="mx-auto w-full max-w-3xl px-3 py-4 text-ui text-brand-ink-muted sm:px-5">
          Opening the demonstration.
        </p>
      )}
    </>
  );
}
