"use client";

import { useCallback, useId, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import { formatDate, formatMoment, inThisWeek, todayIso } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { ItemState, ItemView } from "@/lib/assistant/types";
import { Badge, Block, Btn, ErrorLine, Field, FormActions, Input, List, ListRow } from "../parts";
import { OwnerFields, useOwnerChoice } from "../OwnerChoice";
import { useDisclosure, useOpenRows } from "../inPlace";
import { LoadFailed, Section } from "./Section";

export const ITEM_STATE_WORDS: Record<ItemState, string> = {
  waiting_for_owner: "Waiting for the owner",
  open: "Open",
  declined: "Declined",
  done: "Done",
  closed: "Closed",
};

const ITEM_BADGE: Record<ItemState, "default" | "success" | "accent" | "info" | "outline"> = {
  waiting_for_owner: "info",
  open: "accent",
  declined: "default",
  done: "success",
  closed: "outline",
};

/** What the owner said, word for word with its time (BR-16). */
export function UpdateLine({ update }: { update: ItemView["updates"][number] }) {
  return (
    <li className="text-ui">
      <span className="font-medium">{update.label}</span>
      {update.note && <span className="whitespace-pre-wrap break-words">: &ldquo;{update.note}&rdquo;</span>}
      {update.date_asked_for && <span> Asks for {formatDate(update.date_asked_for)}.</span>}
      <span className="text-ui-sm text-brand-ink-muted"> {formatMoment(update.at)}</span>
    </li>
  );
}

type Control = "extend" | "reassign" | "close" | null;

function ItemControls({ item, onChanged }: { item: ItemView; onChanged: () => Promise<void> }) {
  const { client } = useAssistant();
  const [control, setControl] = useState<Control>(null);
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const owner = useOwnerChoice();
  const dateId = useId();
  const reasonId = useId();

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setControl(null);
      await onChanged();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  const can = new Set(item.can);
  return (
    <div className="flex flex-col gap-2">
      {control === null && (
        <div className="flex flex-wrap gap-2">
          {can.has("extend") && (
            <Btn size="sm" onClick={() => setControl("extend")}>
              Extend
            </Btn>
          )}
          {can.has("reassign") && (
            <Btn size="sm" onClick={() => setControl("reassign")}>
              Reassign
            </Btn>
          )}
          {can.has("done") && (
            <Btn size="sm" disabled={busy} onClick={() => void run(() => client.markDone(item.id))}>
              Mark done
            </Btn>
          )}
          {can.has("close") && (
            <Btn size="sm" variant="ghost" onClick={() => setControl("close")}>
              Close
            </Btn>
          )}
        </div>
      )}
      {control && (
        <form
          className="flex flex-col gap-2 rounded-panel bg-brand-accent-soft/50 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (control === "extend") {
              if (!date) return setError("Choose the new due date.");
              void run(() => client.extendItem(item.id, date));
            } else if (control === "reassign") {
              void owner.resolve().then((chosen) => chosen && run(() => client.reassignItem(item.id, chosen)));
            } else {
              void run(() => client.closeItem(item.id, reason.trim() || undefined));
            }
          }}
        >
          {control === "extend" && (
            <Field label="New due date" htmlFor={dateId}>
              <Input id={dateId} type="date" min={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} required autoFocus />
            </Field>
          )}
          {control === "reassign" && <OwnerFields choice={owner} label="New owner" />}
          {control === "close" && (
            <Field label="Reason (optional)" htmlFor={reasonId}>
              <Input id={reasonId} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
            </Field>
          )}
          <FormActions>
            <Btn size="sm" variant="primary" type="submit" disabled={busy}>
              {control === "extend" ? "Extend" : control === "reassign" ? "Reassign" : "Close item"}
            </Btn>
            <Btn size="sm" variant="ghost" onClick={() => setControl(null)}>
              Cancel
            </Btn>
          </FormActions>
        </form>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}

function AddItemForm({ onAdded, onCancel }: { onAdded: () => Promise<void>; onCancel: () => void }) {
  const { client } = useAssistant();
  const owner = useOwnerChoice();
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const textId = useId();
  const dueId = useId();

  const submit = async () => {
    setError(null);
    const chosen = await owner.resolve();
    if (!chosen) return;
    setBusy(true);
    try {
      await client.addItem({ text: text.trim(), due_date: due, ...chosen });
      await onAdded();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      aria-label="Add an action item"
      className="flex flex-col gap-2 rounded-panel bg-brand-accent-soft/50 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <Field label="What is to be done" htmlFor={textId}>
        <Input id={textId} value={text} maxLength={300} onChange={(e) => setText(e.target.value)} required autoFocus />
      </Field>
      <OwnerFields choice={owner} />
      <Field label="Due date" htmlFor={dueId}>
        <Input id={dueId} type="date" min={todayIso()} value={due} onChange={(e) => setDue(e.target.value)} required />
      </Field>
      {error && <ErrorLine>{error}</ErrorLine>}
      <FormActions>
        <Btn size="sm" variant="primary" type="submit" disabled={busy}>
          Add item
        </Btn>
        <Btn size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Btn>
      </FormActions>
    </form>
  );
}

function ItemRow({
  item,
  open,
  onToggle,
  opener,
  onChanged,
}: {
  item: ItemView;
  open: boolean;
  onToggle: () => void;
  opener: (element: HTMLElement | null) => void;
  onChanged: () => Promise<void>;
}) {
  const owner = item.owner.display_name ?? "Someone who has left";
  return (
    <ListRow
      ref={opener}
      onClick={onToggle}
      expanded={open}
      title={item.text}
      sub={`${owner} · Due ${formatDate(item.due_date)}${item.last_update ? ` · ${item.last_update.label}` : ""}`}
      right={<Badge variant={ITEM_BADGE[item.state]}>{ITEM_STATE_WORDS[item.state]}</Badge>}
    >
      {open && (
        <div className="mt-2 flex flex-col gap-2">
          {item.attention && <p className="text-ui text-brand-ink">{item.attention.reason}</p>}
          {item.answer_reason && (
            <p className="text-ui">
              <span className="text-brand-ink-muted">{owner} said: </span>&ldquo;{item.answer_reason}&rdquo;
            </p>
          )}
          {item.updates.length > 0 ? (
            <Block label="Updates">
              <ul className="flex flex-col gap-1">
                {item.updates.map((update) => (
                  <UpdateLine key={update.id} update={update} />
                ))}
              </ul>
            </Block>
          ) : (
            <p className="text-ui text-brand-ink-muted">No updates yet.</p>
          )}
          {item.follow_up_note && <p className="text-ui text-brand-ink-muted">{item.follow_up_note}</p>}
          {item.follow_up_times.length > 0 && (
            <p className="text-ui-sm text-brand-ink-muted">
              Follow-ups: {item.follow_up_times.map((t) => formatMoment(t)).join("; ")}
            </p>
          )}
          <ItemControls item={item} onChanged={onChanged} />
        </div>
      )}
    </ListRow>
  );
}

const STILL_GOING: ItemState[] = ["waiting_for_owner", "open"];

/** Action items: what owners agreed to do, opened in place. */
export function ItemsSection() {
  const { client } = useAssistant();
  const load = useCallback(() => client.items(), [client]);
  const { data, error, reload } = useResource(load);
  const disclosure = useDisclosure();
  const rows = useOpenRows();
  const adding = useDisclosure();

  const items = data?.items ?? [];
  const open = items.filter((i) => i.state === "open").length;
  const going = items.filter((i) => STILL_GOING.includes(i.state));
  const dueThisWeek = going.filter((i) => inThisWeek(i.due_date)).length;
  const summary = !data
    ? "Reading action items."
    : items.length === 0
      ? "No action items yet."
      : `${open} open, ${dueThisWeek} due this week`;

  return (
    <Section
      title="Action items"
      summary={summary}
      count={open}
      countLabel="open"
      openable={{ open: disclosure.open, onToggle: disclosure.toggle, opener: disclosure.opener }}
    >
      {error && !data ? (
        <LoadFailed message={messageOf(error)} onRetry={() => void reload()} />
      ) : (
        <>
          {items.length > 0 && (
            <List label="Action items">
              {items.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  open={rows.openId === item.id}
                  onToggle={() => rows.toggle(item.id)}
                  opener={rows.register(item.id)}
                  onChanged={reload}
                />
              ))}
            </List>
          )}
          {adding.open ? (
            <AddItemForm
              onAdded={async () => {
                adding.close();
                await reload();
              }}
              onCancel={adding.close}
            />
          ) : (
            <div>
              <Btn size="sm" ref={adding.opener} onClick={adding.toggle}>
                Add an action item
              </Btn>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
