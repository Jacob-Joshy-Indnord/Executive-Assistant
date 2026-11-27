"use client";

/**
 * Requests for you (Story 6.3): everything asked of an owner in one place, each request
 * saying who asks, what is needed and by when, answered in place. Their items and their
 * confirmed statements follow, with Update and Correct.
 */

import { useCallback, useId, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { AssistantProvider, useAssistant, type PageProps } from "@/lib/assistant/context";
import { formatDate, formatMoment, plural, todayIso } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type {
  ColourMeaning,
  ItemView,
  StatementAnswer,
  StatementView,
  StatusRequestView,
  UpdateWord,
} from "@/lib/assistant/types";
import {
  Badge,
  Block,
  Btn,
  ChoiceGroup,
  ColourName,
  ErrorLine,
  Field,
  FormActions,
  Input,
  List,
  ListRow,
  SecHead,
  Panel,
  Textarea,
} from "./parts";
import { useDisclosure, useInPlace } from "./inPlace";
import { StatementForm } from "./StatementForm";
import { ITEM_STATE_WORDS, UpdateLine } from "./sections/ItemsSection";

const WORDS: { value: UpdateWord; label: string }[] = [
  { value: "on_track", label: "On track" },
  { value: "done", label: "Done" },
  { value: "blocked", label: "Blocked" },
];

const POINTS = { colour: "colour", reason: "reason", what_changed: "what changed", what_comes_next: "what comes next" };

/** The owner's update in one step: a word, a note, and another date if they need one. */
function UpdateForm({ item, onDone, onCancel }: { item: ItemView; onDone: () => Promise<void>; onCancel: () => void }) {
  const { client } = useAssistant();
  const name = useId();
  const noteId = useId();
  const dateId = useId();
  const [word, setWord] = useState<UpdateWord | null>(null);
  const [note, setNote] = useState("");
  const [date, setDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [wordError, setWordError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setWordError(null);
    if (!word) return setWordError("Choose On track, Done or Blocked.");
    setBusy(true);
    try {
      await client.giveUpdate(item.id, {
        word,
        ...(note.trim() ? { note } : {}),
        ...(date ? { date_asked_for: date } : {}),
      });
      await onDone();
    } catch (failure) {
      setError(messageOf(failure));
      setBusy(false);
    }
  };

  return (
    <form
      className="mt-2 flex flex-col gap-3 rounded-panel bg-brand-accent-soft/50 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <ChoiceGroup<UpdateWord> legend="How is it going" name={name} value={word} onChange={setWord} options={WORDS} error={wordError} />
      <Field label="Note (optional)" htmlFor={noteId}>
        <Textarea id={noteId} rows={2} value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <Field label="Another date (optional)" htmlFor={dateId}>
        <Input id={dateId} type="date" min={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} />
      </Field>
      {error && <ErrorLine>{error}</ErrorLine>}
      <FormActions>
        <Btn size="sm" variant="primary" type="submit" disabled={busy}>
          Send update
        </Btn>
        <Btn size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Btn>
      </FormActions>
    </form>
  );
}

type ItemAnswer = "view" | "propose" | "decline" | "update";

/** A request about an action item: accept, propose another date or decline; or an update. */
function ItemRequestRow({
  item,
  error,
  onAnswer,
  onUpdated,
}: {
  item: ItemView;
  error?: string;
  onAnswer: (call: () => Promise<unknown>) => void;
  onUpdated: () => Promise<void>;
}) {
  const { client } = useAssistant();
  const [mode, setMode] = useState<ItemAnswer>("view");
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const dateId = useId();
  const reasonId = useId();
  const who = item.for?.display_name ?? "Your manager";
  const accepting = item.can.includes("accept");
  const needed = accepting ? "to accept this action item" : "for an update";
  const byWhen = item.ask?.answer_by ? formatMoment(item.ask.answer_by) : null;

  return (
    <ListRow
      title={
        <>
          <span className="text-brand-ink-muted">
            {who} asks you {needed}:
          </span>{" "}
          <span className="font-medium">{item.text}</span>
        </>
      }
      sub={`Due ${formatDate(item.due_date)}${byWhen ? ` · Answer by ${byWhen}` : ""}`}
    >
      <div className="mt-2 flex flex-col gap-2">
        {error && <ErrorLine>{error}</ErrorLine>}
        {mode === "view" && (
          <div className="flex flex-wrap gap-2">
            {accepting ? (
              <>
                <Btn size="sm" variant="primary" onClick={() => onAnswer(() => client.acceptItem(item.id))}>
                  Accept
                </Btn>
                <Btn size="sm" onClick={() => setMode("propose")}>
                  Propose another date
                </Btn>
                <Btn size="sm" variant="ghost" onClick={() => setMode("decline")}>
                  Decline
                </Btn>
              </>
            ) : (
              <Btn size="sm" variant="primary" onClick={() => setMode("update")}>
                Update
              </Btn>
            )}
          </div>
        )}
        {(mode === "propose" || mode === "decline") && (
          <form
            className="flex flex-col gap-2 rounded-panel bg-brand-accent-soft/50 p-3"
            onSubmit={(event) => {
              event.preventDefault();
              setFormError(null);
              if (mode === "propose" && !date) return setFormError("Choose the date you can do it by.");
              if (!reason.trim()) return setFormError("Give a reason.");
              onAnswer(() =>
                mode === "propose" ? client.proposeDate(item.id, date, reason) : client.declineItem(item.id, reason),
              );
            }}
          >
            {mode === "propose" && (
              <Field label="Date you can do it by" htmlFor={dateId}>
                <Input id={dateId} type="date" min={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} required autoFocus />
              </Field>
            )}
            <Field label="Reason" htmlFor={reasonId}>
              <Input
                id={reasonId}
                value={reason}
                maxLength={500}
                onChange={(e) => setReason(e.target.value)}
                required
                autoFocus={mode === "decline"}
              />
            </Field>
            {formError && <ErrorLine>{formError}</ErrorLine>}
            <FormActions>
              <Btn size="sm" variant={mode === "decline" ? "danger" : "primary"} type="submit">
                {mode === "propose" ? "Propose date" : "Decline"}
              </Btn>
              <Btn size="sm" variant="ghost" onClick={() => setMode("view")}>
                Cancel
              </Btn>
            </FormActions>
          </form>
        )}
        {mode === "update" && <UpdateForm item={item} onDone={onUpdated} onCancel={() => setMode("view")} />}
      </div>
    </ListRow>
  );
}

/** A request about a project's status: confirm the draft, or give the status. */
function StatusRequestRow({
  request,
  error,
  onAnswer,
}: {
  request: StatusRequestView;
  error?: string;
  onAnswer: (answer: StatementAnswer) => void;
}) {
  const who = request.for.display_name ?? "Your manager";
  const project = request.project.name ?? "a project";
  const needed = request.start === "draft" ? `to confirm the status of ${project}` : `for the status of ${project}`;
  return (
    <ListRow
      title={
        <>
          <span className="text-brand-ink-muted">{who} asks you</span> <span className="font-medium">{needed}</span>
        </>
      }
      sub={`Needed by ${formatMoment(request.needed_by)}${request.source ? ` · Source: ${request.source}` : ""}`}
    >
      <div className="mt-2 flex flex-col gap-2">
        {request.start === "draft" && (
          <p className="text-ui text-brand-ink-muted">The assistant drafted this. Choose the colour, and change any words that are not right.</p>
        )}
        {error && <ErrorLine>{error}</ErrorLine>}
        <StatementForm
          colours={request.colours}
          colour={request.colour}
          wording={request.wording}
          submitLabel={(changed) => (request.start === "draft" && !changed ? "Confirm" : request.start === "draft" ? "Confirm with changes" : "Give status")}
          onSubmit={async (answer) => {
            onAnswer(answer);
            return null;
          }}
        />
      </div>
    </ListRow>
  );
}

function StatementSummary({ statement, label }: { statement: StatementView; label?: string }) {
  return (
    <div className="flex flex-col gap-0.5 text-ui">
      {label && <span className="text-ui-xs font-semibold uppercase text-brand-ink-muted">{label}</span>}
      <p>
        <ColourName colour={statement.colour} />. {statement.wording.reason}
      </p>
      {statement.wording.what_changed && <p className="text-brand-ink-muted">What changed: {statement.wording.what_changed}</p>}
      {statement.wording.what_comes_next && (
        <p className="text-brand-ink-muted">What comes next: {statement.wording.what_comes_next}</p>
      )}
      <p className="text-ui-sm text-brand-ink-muted">
        {statement.mark_words}, {formatMoment(statement.created_at)}
        {statement.author.display_name ? ` · ${statement.author.display_name}` : ""}
      </p>
    </div>
  );
}

/** The earlier version, read when asked for: both versions are kept (BR-3). */
function EarlierVersion({ id }: { id: string }) {
  const { client } = useAssistant();
  const load = useCallback(() => client.statement(id), [client, id]);
  const { data, error } = useResource(load, { refresh: false });
  if (error) return <ErrorLine>{messageOf(error)}</ErrorLine>;
  if (!data) return <p className="text-ui text-brand-ink-muted">Reading the earlier version.</p>;
  return <StatementSummary statement={data} label="Earlier version" />;
}

function StatementRow({
  statement,
  colours,
  onCorrected,
}: {
  statement: StatementView;
  colours: ColourMeaning[];
  onCorrected: (corrected: StatementView) => void;
}) {
  const { client } = useAssistant();
  const correcting = useDisclosure();
  const earlier = useDisclosure();
  const [justCorrected, setJustCorrected] = useState(false);
  const project = statement.project_name ?? "A project";
  const points = statement.correction?.points.map((p) => POINTS[p]) ?? [];

  return (
    <ListRow
      title={
        <>
          <span className="font-medium">{project}.</span> <ColourName colour={statement.colour} />. {statement.wording.reason}
        </>
      }
      sub={`${statement.mark_words}, ${formatMoment(statement.created_at)}${statement.for?.display_name ? ` · For ${statement.for.display_name}` : ""}`}
      right={
        !correcting.open && (
          <Btn size="sm" ref={correcting.opener} onClick={correcting.toggle}>
            Correct
          </Btn>
        )
      }
    >
      <div className="mt-2 flex flex-col gap-2">
        {justCorrected && (
          <p role="status" className="text-ui font-medium text-brand-ink">
            Corrected. Both versions are kept.
          </p>
        )}
        {statement.previous_version_id && (
          <>
            {points.length > 0 && <p className="text-ui-sm text-brand-ink-muted">Changed: {points.join(", ")}.</p>}
            <div>
              <Btn size="sm" variant="ghost" ref={earlier.opener} aria-expanded={earlier.open} onClick={earlier.toggle}>
                {earlier.open ? "Hide earlier version" : "Show earlier version"}
              </Btn>
            </div>
            {earlier.open && (
              <div className="rounded-panel border border-brand-border p-3 dark:border-brand-border/15">
                <EarlierVersion id={statement.previous_version_id} />
              </div>
            )}
          </>
        )}
        {correcting.open && (
          <StatementForm
            colours={colours}
            colour={statement.colour}
            wording={statement.wording}
            submitLabel={() => "Save correction"}
            onCancel={correcting.close}
            onSubmit={async (answer) => {
              try {
                const corrected = await client.correctStatement(statement.id, answer);
                correcting.close();
                setJustCorrected(true);
                earlier.setOpen(true);
                onCorrected({ ...corrected, project_name: statement.project_name, for: statement.for });
                return null;
              } catch (failure) {
                return messageOf(failure);
              }
            }}
          />
        )}
      </div>
    </ListRow>
  );
}

function Owner() {
  const { client } = useAssistant();
  const loadItems = useCallback(() => client.myItems(), [client]);
  const loadAsks = useCallback(() => client.statusRequests(), [client]);
  const loadStatements = useCallback(() => client.myStatements(), [client]);
  const loadColours = useCallback(() => client.colours(), [client]);
  const items = useResource(loadItems);
  const asks = useResource(loadAsks);
  const statements = useResource(loadStatements);
  const colours = useResource(loadColours);
  const rows = useInPlace(async () => {
    await Promise.all([items.reload(), asks.reload(), statements.reload()]);
  });
  const [updating, setUpdating] = useState<string | null>(null);

  const allItems = items.data?.items ?? [];
  const itemAsks = allItems.filter(
    (i) => !rows.isGone(i.id) && (i.can.includes("accept") || (i.ask?.asks === "update" && i.can.includes("update"))),
  );
  const statusAsks = (asks.data?.requests ?? []).filter((r) => !rows.isGone(r.id));
  const requestCount = itemAsks.length + statusAsks.length;
  const askedIds = new Set(itemAsks.map((i) => i.id));
  const myItems = allItems.filter((i) => !askedIds.has(i.id) && (i.state === "open" || i.state === "waiting_for_owner"));
  const loadError = items.error ?? asks.error;

  return (
    <>
      <h1 className="text-ui-title text-brand-ink">Requests for you</h1>

      <Panel as="section" aria-labelledby="requests-heading" className="flex flex-col gap-3">
        <SecHead
          id="requests-heading"
          title="Asked of you"
          description={
            !items.data || !asks.data
              ? "Reading what is asked of you."
              : requestCount
                ? `${plural(requestCount, "request")} to answer`
                : "Nothing is asked of you."
          }
        />
        {loadError && !(items.data && asks.data) && <ErrorLine>{messageOf(loadError)}</ErrorLine>}
        {requestCount > 0 && (
          <List label="Requests for you">
            {itemAsks.map((item) => (
              <ItemRequestRow
                key={item.id}
                item={item}
                error={rows.errors[item.id]}
                onAnswer={(call) => void rows.act(item.id, call)}
                onUpdated={async () => {
                  await rows.act(item.id, async () => undefined);
                }}
              />
            ))}
            {statusAsks.map((request) => (
              <StatusRequestRow
                key={request.id}
                request={request}
                error={rows.errors[request.id]}
                onAnswer={(answer) => void rows.act(request.id, () => client.answerStatus(request.id, answer))}
              />
            ))}
          </List>
        )}
      </Panel>

      <Panel as="section" aria-labelledby="items-heading" className="flex flex-col gap-3">
        <SecHead
          id="items-heading"
          title="Your action items"
          description={!items.data ? "Reading your items." : myItems.length ? `${myItems.length} open` : "You have no open action items."}
        />
        {myItems.length > 0 && (
          <List label="Your action items">
            {myItems.map((item) => (
              <ListRow
                key={item.id}
                title={item.text}
                sub={`For ${item.for?.display_name ?? "your manager"} · Due ${formatDate(item.due_date)}${item.last_update ? ` · ${item.last_update.label}` : ""}`}
                right={
                  <>
                    <Badge variant="outline">{ITEM_STATE_WORDS[item.state]}</Badge>
                    {item.can.includes("update") && updating !== item.id && (
                      <Btn size="sm" onClick={() => setUpdating(item.id)}>
                        Update
                      </Btn>
                    )}
                  </>
                }
              >
                {item.updates.length > 0 && (
                  <Block label="Your updates" className="mt-2">
                    <ul className="flex flex-col gap-1">
                      {item.updates.slice(-3).map((update) => (
                        <UpdateLine key={update.id} update={update} />
                      ))}
                    </ul>
                  </Block>
                )}
                {updating === item.id && (
                  <UpdateForm
                    item={item}
                    onCancel={() => setUpdating(null)}
                    onDone={async () => {
                      setUpdating(null);
                      await items.reload();
                    }}
                  />
                )}
              </ListRow>
            ))}
          </List>
        )}
      </Panel>

      <Panel as="section" aria-labelledby="statements-heading" className="flex flex-col gap-3">
        <SecHead
          id="statements-heading"
          title="Your confirmed statements"
          description={
            !statements.data
              ? "Reading your statements."
              : statements.data.count
                ? "A confirmed statement cannot be changed. A correction is added as a new version, and both are kept."
                : "You have no confirmed statements."
          }
        />
        {statements.data && statements.data.statements.length > 0 && (
          <List label="Your confirmed statements">
            {statements.data.statements.map((statement) => (
              <StatementRow
                key={`${statement.project_id}-${statement.for?.id ?? ""}`}
                statement={statement}
                colours={colours.data ?? []}
                onCorrected={(corrected) =>
                  statements.setData((current) =>
                    current && {
                      ...current,
                      statements: current.statements.map((s) => (s.id === statement.id ? corrected : s)),
                    },
                  )
                }
              />
            ))}
          </List>
        )}
      </Panel>
    </>
  );
}

export function RequestsForYouPage({ client, basePath }: PageProps) {
  return (
    <AssistantProvider client={client} basePath={basePath}>
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-3 py-4 sm:px-5 sm:py-6">
        <Owner />
      </main>
    </AssistantProvider>
  );
}
