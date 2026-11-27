"use client";

import { useCallback, useId, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import { formatDate, plural, todayIso } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { AttentionView, Decision } from "@/lib/assistant/types";
import { Badge, Btn, ErrorLine, Field, FormActions, Input, List, ListRow, Textarea } from "../parts";
import { OwnerFields, useOwnerChoice } from "../OwnerChoice";
import { usePeople } from "../people";
import { useInPlace } from "../inPlace";
import { LoadFailed, Section } from "./Section";

/** Choices that need more than a click, and what each asks for. */
const NEEDS_FORM = new Set(["extend", "reassign", "take it up", "close", "change who confirms"]);

function DecisionForm({
  entry,
  choice,
  onDecide,
  onCancel,
}: {
  entry: AttentionView;
  choice: string;
  onDecide: (decision: Decision) => void;
  onCancel: () => void;
}) {
  const key = choice.toLowerCase();
  const resolvePerson = usePeople();
  const owner = useOwnerChoice();
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [person, setPerson] = useState(key === "take it up" ? (entry.owner?.display_name ?? "") : "");
  const [message, setMessage] = useState(entry.prepared_message ?? "");
  const [error, setError] = useState<string | null>(null);
  const ids = { date: useId(), reason: useId(), person: useId(), message: useId() };

  const submit = async () => {
    setError(null);
    try {
      if (key === "extend") {
        if (!date) return setError("Choose the new due date.");
        return onDecide({ choice, due_date: date });
      }
      if (key === "reassign") {
        const chosen = await owner.resolve();
        if (chosen) onDecide({ choice, ...chosen });
        return;
      }
      if (key === "take it up") {
        const to = await resolvePerson(person, { peopleOnly: true });
        return onDecide({ choice, person_id: to.id, message });
      }
      if (key === "change who confirms") {
        const to = await resolvePerson(person, { peopleOnly: true });
        return onDecide({ choice, person_id: to.id });
      }
      return onDecide({ choice, reason: reason.trim() || undefined });
    } catch (failure) {
      setError(messageOf(failure));
    }
  };

  return (
    <form
      className="mt-2 flex flex-col gap-2 rounded-panel bg-brand-accent-soft/50 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      {key === "extend" && (
        <Field label="New due date" htmlFor={ids.date}>
          <Input id={ids.date} type="date" min={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} required autoFocus />
        </Field>
      )}
      {key === "reassign" && <OwnerFields choice={owner} label="New owner" />}
      {(key === "take it up" || key === "change who confirms") && (
        <Field label={key === "take it up" ? "To" : "Who confirms"} htmlFor={ids.person}>
          <Input id={ids.person} value={person} onChange={(e) => setPerson(e.target.value)} required autoFocus />
        </Field>
      )}
      {key === "take it up" && (
        <Field label="Message" htmlFor={ids.message}>
          <Textarea id={ids.message} rows={4} value={message} maxLength={2000} onChange={(e) => setMessage(e.target.value)} />
        </Field>
      )}
      {key === "close" && (
        <Field label="Reason (optional)" htmlFor={ids.reason}>
          <Input id={ids.reason} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
        </Field>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
      <FormActions>
        <Btn size="sm" variant="primary" type="submit">
          {key === "take it up" ? "Send" : choice}
        </Btn>
        <Btn size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Btn>
      </FormActions>
    </form>
  );
}

function entryTitle(entry: AttentionView): string {
  return entry.item?.text ?? entry.project?.name ?? entry.reason;
}

function entryDetail(entry: AttentionView): string {
  const parts: string[] = [];
  if (entry.owner?.display_name) parts.push(`${entry.owner.display_name}.`);
  if (entry.item || entry.project) parts.push(entry.reason);
  if (entry.item?.due_date) parts.push(`Due ${formatDate(entry.item.due_date)}.`);
  if (entry.asked_date) parts.push(`Asks for ${formatDate(entry.asked_date)}.`);
  if (entry.last_update) {
    parts.push(`Last update: ${entry.last_update.label}${entry.last_update.note ? `, "${entry.last_update.note}"` : ""}.`);
  }
  if (entry.item?.answer_reason) parts.push(`"${entry.item.answer_reason}"`);
  return parts.join(" ");
}

function EntryRow({
  entry,
  error,
  onDecide,
}: {
  entry: AttentionView;
  error?: string;
  onDecide: (decision: Decision) => void;
}) {
  const [choosing, setChoosing] = useState<string | null>(null);
  const detail = entryDetail(entry);
  return (
    <ListRow
      title={
        <span>
          {entry.label && (
            <>
              <Badge variant="info" className="mr-2">
                {entry.label}
              </Badge>{" "}
            </>
          )}
          {entryTitle(entry)}
        </span>
      }
      sub={detail || undefined}
    >
      <div className="mt-2 flex flex-col gap-2">
        {entry.known && <p className="whitespace-pre-wrap break-words text-ui text-brand-ink">{entry.known}</p>}
        {error && <ErrorLine>{error}</ErrorLine>}
        {choosing === null ? (
          <div className="flex flex-wrap gap-2" role="group" aria-label={`Choices for ${entryTitle(entry)}`}>
            {entry.choices.map((choice) => (
              <Btn
                key={choice}
                size="sm"
                onClick={() => (NEEDS_FORM.has(choice.toLowerCase()) ? setChoosing(choice) : onDecide({ choice }))}
              >
                {choice}
              </Btn>
            ))}
          </div>
        ) : (
          <DecisionForm entry={entry} choice={choosing} onDecide={onDecide} onCancel={() => setChoosing(null)} />
        )}
      </div>
    </ListRow>
  );
}

/** Needs your attention: what needs a decision, decided in place. */
export function AttentionSection() {
  const { client } = useAssistant();
  const load = useCallback(() => client.attention(), [client]);
  const { data, error, reload } = useResource(load);
  const rows = useInPlace(reload);
  const shown = (data?.entries ?? []).filter((e) => !rows.isGone(e.id));

  return (
    <Section
      title="Needs your attention"
      summary={shown.length ? `${plural(shown.length, "thing")} to decide` : "Nothing needs your attention."}
      count={shown.length}
      countLabel="to decide"
    >
      {error && !data ? (
        <LoadFailed message={messageOf(error)} onRetry={() => void reload()} />
      ) : shown.length === 0 ? null : (
        <List label="Things that need your decision">
          {shown.map((entry) => (
            <EntryRow
              key={entry.id}
              entry={entry}
              error={rows.errors[entry.id]}
              onDecide={(decision) => void rows.act(entry.id, () => client.decide(entry.id, decision))}
            />
          ))}
        </List>
      )}
    </Section>
  );
}
