"use client";

import { useCallback, useId, useState } from "react";
import { useAssistant } from "@/lib/assistant/context";
import { messageOf } from "@/lib/assistant/client";
import { plural } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { RequestView } from "@/lib/assistant/types";
import { Badge, Btn, ErrorLine, Field, FormActions, Input, List, ListRow, Textarea } from "../parts";
import { useInPlace, useOpenRows } from "../inPlace";
import { LoadFailed, Section } from "./Section";

type Mode = "view" | "edit" | "decline";

function RequestRow({
  request,
  error,
  open,
  onToggle,
  opener,
  onApprove,
  onDecline,
}: {
  request: RequestView;
  error?: string;
  open: boolean;
  onToggle: () => void;
  opener: (element: HTMLElement | null) => void;
  onApprove: (text?: string) => void;
  onDecline: (reason?: string) => void;
}) {
  const [mode, setMode] = useState<Mode>("view");
  const [text, setText] = useState(request.text ?? "");
  const [reason, setReason] = useState("");
  const textId = useId();
  const reasonId = useId();
  const subject = request.effect ?? request.label;

  return (
    <ListRow
      ref={opener}
      title={
        <span>
          <Badge variant="accent" className="mr-2">
            {request.label}
          </Badge>{" "}
          {subject}
        </span>
      }
      onClick={onToggle}
      expanded={open}
      right={
        mode === "view" && (
          <>
            <Btn size="sm" variant="primary" onClick={() => onApprove()}>
              Approve
            </Btn>
            {request.editable && (
              <Btn size="sm" onClick={() => setMode("edit")}>
                Edit
              </Btn>
            )}
            <Btn size="sm" variant="ghost" onClick={() => setMode("decline")}>
              Decline
            </Btn>
          </>
        )
      }
    >
      <div className="mt-2 flex flex-col gap-2 pl-0">
        {error && <ErrorLine>{error}</ErrorLine>}
        {open && mode === "view" && (
          <dl className="grid grid-cols-1 gap-x-3 gap-y-1 text-ui sm:grid-cols-[8rem_1fr]">
            {request.target && (
              <>
                <dt className="text-brand-ink-muted">To</dt>
                <dd>{request.target.display_name}</dd>
              </>
            )}
            {request.project && (
              <>
                <dt className="text-brand-ink-muted">Project</dt>
                <dd>{request.project.name}</dd>
              </>
            )}
            {request.text && (
              <>
                <dt className="text-brand-ink-muted">Text</dt>
                <dd className="whitespace-pre-wrap break-words">{request.text}</dd>
              </>
            )}
          </dl>
        )}
        {mode === "edit" && (
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              onApprove(text);
            }}
          >
            <Field label="Text" htmlFor={textId}>
              <Textarea id={textId} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} autoFocus />
            </Field>
            <FormActions>
              <Btn size="sm" variant="primary" type="submit">
                Approve
              </Btn>
              <Btn size="sm" variant="ghost" onClick={() => setMode("view")}>
                Cancel
              </Btn>
            </FormActions>
          </form>
        )}
        {mode === "decline" && (
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              onDecline(reason.trim() || undefined);
            }}
          >
            <Field label="Reason (optional)" htmlFor={reasonId}>
              <Input id={reasonId} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
            </Field>
            <FormActions>
              <Btn size="sm" variant="danger" type="submit">
                Decline
              </Btn>
              <Btn size="sm" variant="ghost" onClick={() => setMode("view")}>
                Cancel
              </Btn>
            </FormActions>
          </form>
        )}
      </div>
    </ListRow>
  );
}

/** Needs your approval: what waits for the manager, approved, edited or declined in place. */
export function ApprovalsSection() {
  const { client } = useAssistant();
  const load = useCallback(() => client.requests(), [client]);
  const { data, error, reload } = useResource(load);
  const rows = useInPlace(reload);
  const opened = useOpenRows();
  const [allError, setAllError] = useState<string | null>(null);

  const shown = (data?.requests ?? []).filter((r) => !rows.isGone(r.id));
  const toOwners = shown.filter((r) => r.to_owner);

  const approveAll = async () => {
    setAllError(null);
    const ok = await rows.act(
      toOwners.map((r) => r.id),
      () => client.approveAll(),
    );
    if (!ok) setAllError("Not all were approved. Try again.");
  };

  return (
    <Section
      title="Needs your approval"
      summary={shown.length ? `${plural(shown.length, "request")} waiting for you` : "Nothing waits for your approval."}
      count={shown.length}
      countLabel="waiting"
      actions={
        toOwners.length > 1 && (
          <Btn size="sm" onClick={() => void approveAll()}>
            Approve all
          </Btn>
        )
      }
    >
      {error && !data ? (
        <LoadFailed message={messageOf(error)} onRetry={() => void reload()} />
      ) : shown.length === 0 ? null : (
        <List label="Requests waiting for approval">
          {shown.map((request) => (
            <RequestRow
              key={request.id}
              request={request}
              error={rows.errors[request.id]}
              open={opened.openId === request.id}
              onToggle={() => opened.toggle(request.id)}
              opener={opened.register(request.id)}
              onApprove={(text) =>
                void rows.act(request.id, () =>
                  client.approve(request.id, text !== undefined && text !== request.text ? text : undefined),
                )
              }
              onDecline={(reason) => void rows.act(request.id, () => client.decline(request.id, reason))}
            />
          ))}
        </List>
      )}
      {allError && <ErrorLine>{allError}</ErrorLine>}
    </Section>
  );
}
