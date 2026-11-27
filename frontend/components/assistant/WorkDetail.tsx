"use client";

import { useCallback, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import { formatMoment } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { WorkState, WorkView } from "@/lib/assistant/types";
import { Badge, Block, Btn, ErrorLine } from "./parts";

export const WORK_STATE_WORDS: Record<WorkState, string> = {
  waiting: "Waiting",
  running: "Working on it",
  done: "Done",
  refused: "Refused",
  failed: "Failed",
  cancelled: "Cancelled",
};

const WORK_BADGE: Record<WorkState, "default" | "success" | "accent" | "info" | "outline"> = {
  waiting: "outline",
  running: "info",
  done: "success",
  refused: "default",
  failed: "default",
  cancelled: "outline",
};

export function WorkBadge({ state }: { state: WorkState }) {
  return (
    <Badge variant={WORK_BADGE[state]} dot>
      {WORK_STATE_WORDS[state]}
    </Badge>
  );
}

/** A piece of work: its state, its reply, and anything refused with the reasons. */
export function WorkDetail({ workId, onRetried }: { workId: string; onRetried?: () => void }) {
  const { client } = useAssistant();
  const load = useCallback(() => client.work(workId), [client, workId]);
  const { data, error, reload } = useResource<WorkView>(load);
  const [retryError, setRetryError] = useState<string | null>(null);

  if (error && !data) return <ErrorLine>{messageOf(error)}</ErrorLine>;
  if (!data) return <p className="text-ui text-brand-ink-muted">Reading the work.</p>;

  const retry = async () => {
    setRetryError(null);
    try {
      await client.retryWork(workId);
      await reload();
      onRetried?.();
    } catch (failure) {
      setRetryError(messageOf(failure));
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-panel bg-brand-accent-soft/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <WorkBadge state={data.state} />
        {data.finished_at && <span className="text-ui-sm text-brand-ink-muted">{formatMoment(data.finished_at)}</span>}
      </div>
      {data.reason && <p className="text-ui text-brand-ink">{data.reason}</p>}
      <Block label="Reply">
        <p className="whitespace-pre-wrap break-words text-ui">
          {data.reply ?? <span className="text-brand-ink-muted">No reply.</span>}
        </p>
      </Block>
      {data.refused_count > 0 && (
        <Block label={`Refused (${data.refused_count})`}>
          <ul className="list-disc pl-5 text-ui">
            {data.refused.map((reason, index) => (
              <li key={index} className="break-words">
                {reason}
              </li>
            ))}
          </ul>
        </Block>
      )}
      {data.can_retry && (
        <div>
          <Btn size="sm" onClick={() => void retry()}>
            Try again
          </Btn>
        </div>
      )}
      {retryError && <ErrorLine>{retryError}</ErrorLine>}
    </div>
  );
}
