"use client";

import { useCallback, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import { formatMoment, inThisWeek } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { RequestState, RequestView } from "@/lib/assistant/types";
import { Badge, Btn, ErrorLine, List, ListRow } from "../parts";
import { useDisclosure } from "../inPlace";
import { LoadFailed, Section } from "./Section";

const DONE_WORDS: Partial<Record<RequestState, string>> = {
  done: "Done",
  undone: "Undone",
  failed: "Could not be done",
};

function DoneRow({ request, onChanged }: { request: RequestView; onChanged: () => Promise<void> }) {
  const { client } = useAssistant();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const when = request.undone_at ?? request.done_at ?? request.decided_at ?? request.created_at;

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ListRow
      title={request.effect ?? request.label}
      sub={`${formatMoment(when)} · ${request.approved_by === "manager" ? "Approved by you" : "Without approval"}`}
      right={
        <>
          <Badge variant={request.state === "done" ? "success" : "default"}>{DONE_WORDS[request.state] ?? request.state}</Badge>
          {request.can_undo && (
            <Btn size="sm" disabled={busy} onClick={() => void run(() => client.undoRequest(request.id))}>
              Undo
            </Btn>
          )}
          {request.can_retry && (
            <Btn size="sm" disabled={busy} onClick={() => void run(() => client.retryRequest(request.id))}>
              Try again
            </Btn>
          )}
        </>
      }
    >
      {(request.reason || request.undo_reason || error) && (
        <div className="mt-1 flex flex-col gap-1">
          {request.state === "failed" && request.reason && <p className="text-ui text-brand-ink">{request.reason}</p>}
          {request.undo_reason && <p className="text-ui text-brand-ink">{request.undo_reason}</p>}
          {error && <ErrorLine>{error}</ErrorLine>}
        </div>
      )}
    </ListRow>
  );
}

/** Done for you: everything carried out in the manager's name, newest first. */
export function DoneSection() {
  const { client } = useAssistant();
  const load = useCallback(() => client.done(), [client]);
  const { data, error, reload } = useResource(load);
  const disclosure = useDisclosure();

  const all = data?.requests ?? [];
  const thisWeek = all.filter((r) => r.state === "done" && r.done_at && inThisWeek(r.done_at)).length;
  const summary = !data ? "Reading what was done." : all.length === 0 ? "Nothing done for you yet." : `${thisWeek} this week`;

  return (
    <Section
      title="Done for you"
      summary={summary}
      count={thisWeek}
      countLabel="this week"
      openable={{ open: disclosure.open, onToggle: disclosure.toggle, opener: disclosure.opener }}
    >
      {error && !data ? (
        <LoadFailed message={messageOf(error)} onRetry={() => void reload()} />
      ) : all.length > 0 ? (
        <List label="Done for you">
          {all.map((request) => (
            <DoneRow key={request.id} request={request} onChanged={reload} />
          ))}
        </List>
      ) : null}
    </Section>
  );
}
