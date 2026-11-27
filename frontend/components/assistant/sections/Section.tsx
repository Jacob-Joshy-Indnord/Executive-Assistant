"use client";

import { useId, type ReactNode, type Ref } from "react";
import { Btn, Panel, SecHead } from "../parts";

export type SectionProps = {
  title: string;
  /** The one-line summary, in words. */
  summary: string;
  /** How many things are in the part; shown beside the title. */
  count: number;
  /** What the count is of, read out with it, e.g. "waiting". */
  countLabel: string;
  actions?: ReactNode;
  /** For a part shown as one line until opened: whether it is open, and its Open button. */
  openable?: { open: boolean; onToggle: () => void; opener: Ref<HTMLButtonElement> };
  children?: ReactNode;
};

/** One part of the Assistant page: its title, one-line summary and count, opened in place. */
export function Section({ title, summary, count, countLabel, actions, openable, children }: SectionProps) {
  const headingId = useId();
  const bodyId = useId();
  const shown = !openable || openable.open;
  return (
    <Panel as="section" aria-labelledby={headingId} className="flex flex-col gap-3">
      <SecHead
        id={headingId}
        title={title}
        description={summary}
        action={
          <>
            <span className="min-w-6 text-right text-ui-md font-semibold text-brand-ink" data-testid="section-count">
              {count}{" "}
              <span className="sr-only">{countLabel}</span>
            </span>
            {actions}
            {openable && (
              <Btn
                size="sm"
                ref={openable.opener}
                aria-expanded={openable.open}
                aria-controls={bodyId}
                onClick={openable.onToggle}
              >
                {openable.open ? "Close" : "Open"}{" "}
                <span className="sr-only">{title}</span>
              </Btn>
            )}
          </>
        }
      />
      {shown && children && (
        <div id={bodyId} className="flex flex-col gap-3">
          {children}
        </div>
      )}
    </Panel>
  );
}

/** The line shown in a part that could not be read, with a way to try again. */
export function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-ui text-brand-ink-muted">
      <span>{message}</span>
      <Btn size="sm" variant="ghost" onClick={onRetry}>
        Try again
      </Btn>
    </div>
  );
}
