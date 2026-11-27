"use client";

/**
 * Settings (Story 6.4): everything about the manager's assistant on one page: how they
 * work and the report, approval levels and limits, the projects followed, and pause or
 * resume. Setting a level to "without approval" is confirmed first.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { ApiError, messageOf } from "@/lib/assistant/client";
import { AssistantProvider, useAssistant, type PageProps } from "@/lib/assistant/context";
import { useResource } from "@/lib/assistant/refresh";
import type { ApprovalLevel, ApprovalLevelView, AssistantView, ProjectView } from "@/lib/assistant/types";
import {
  Badge,
  Btn,
  Dialog,
  Empty,
  ErrorLine,
  Field,
  FormActions,
  Input,
  List,
  ListRow,
  Panel,
  SecHead,
  Select,
  Textarea,
} from "./parts";
import { PreferencesFields, draftFrom, formFrom, type PreferencesDraft } from "./PreferencesFields";
import { usePeople } from "./people";
import { NO_ASSISTANT } from "./AssistantPage";

export const LEVEL_WORDS: Record<ApprovalLevel, string> = {
  not_allowed: "Not allowed",
  with_approval: "With approval",
  without_approval: "Without approval",
};

/** A short line that says a change was saved, read out when it shows. */
function Saved({ shown }: { shown: boolean }) {
  return (
    <span role="status" className="text-ui-sm text-brand-ink-muted">
      {shown ? "Saved." : ""}
    </span>
  );
}

// --- Pause and resume ---------------------------------------------------------------------

function PauseBlock({ assistant, onChanged }: { assistant: AssistantView; onChanged: (a: AssistantView) => void }) {
  const { client } = useAssistant();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const paused = assistant.state === "paused";

  const run = async (call: () => Promise<AssistantView>) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await call());
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel as="section" aria-labelledby="pause-heading" className="flex flex-col gap-3">
      <SecHead
        id="pause-heading"
        title="Pause"
        description={
          paused
            ? "Your assistant is paused. Nothing is started, sent or carried out."
            : !assistant.running
              ? "Your organisation has paused all virtual employees."
              : "Your assistant is working."
        }
        action={
          paused ? (
            <Btn variant="primary" size="sm" disabled={busy} onClick={() => void run(client.resume)}>
              Resume
            </Btn>
          ) : (
            <Btn size="sm" disabled={busy || assistant.state === "new"} onClick={() => void run(client.pause)}>
              Pause
            </Btn>
          )
        }
      />
      {error && <ErrorLine>{error}</ErrorLine>}
    </Panel>
  );
}

// --- Preferences and the report -----------------------------------------------------------

function PreferencesBlock() {
  const { client } = useAssistant();
  const load = useCallback(() => client.preferences(), [client]);
  const { data, error, reload } = useResource(load, { refresh: false });
  const [draft, setDraft] = useState<PreferencesDraft | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data && !draft) setDraft(draftFrom(data.form));
  }, [data, draft]);

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setSaved(false);
    setSaveError(null);
    try {
      await client.savePreferences(formFrom(draft));
      setSaved(true);
      await reload();
    } catch (failure) {
      setSaveError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel as="section" aria-labelledby="preferences-heading">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <SecHead
          id="preferences-heading"
          title="How you work and the report"
          description="What you leave empty keeps the usual setting."
        />
        {error && !data && <ErrorLine>{messageOf(error)}</ErrorLine>}
        {draft && <PreferencesFields draft={draft} onChange={setDraft} defaults={data?.settings ?? null} />}
        {saveError && <ErrorLine>{saveError}</ErrorLine>}
        <FormActions>
          <Btn variant="primary" type="submit" disabled={busy || !draft}>
            Save
          </Btn>
          <Saved shown={saved} />
        </FormActions>
      </form>
    </Panel>
  );
}

// --- Approval levels ----------------------------------------------------------------------

function LevelRow({ level, onSaved }: { level: ApprovalLevelView; onSaved: (view: ApprovalLevelView) => void }) {
  const { client } = useAssistant();
  const ids = { level: useId(), people: useId(), perDay: useId() };
  const [choice, setChoice] = useState<ApprovalLevel>(level.level);
  const [people, setPeople] = useState((level.limits.people ?? []).map((p) => p.display_name ?? "").join("\n"));
  const [perDay, setPerDay] = useState(level.limits.per_day === null ? "" : String(level.limits.per_day));
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setConfirming(false);
    setBusy(true);
    setError(null);
    setSaved(false);
    const names = people
      .split(/\n|,/)
      .map((n) => n.trim())
      .filter(Boolean);
    try {
      const view = await client.changeApprovalLevel(level.kind, {
        level: choice,
        limits: {
          people: level.allowed_limits.people && names.length ? names : null,
          per_day: level.allowed_limits.per_day && perDay !== "" ? Number(perDay) : null,
        },
      });
      setSaved(true);
      onSaved(view);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  const submit = () => {
    // Without approval is confirmed first, every time it is chosen anew.
    if (choice === "without_approval" && level.level !== "without_approval") setConfirming(true);
    else void save();
  };

  if (level.locked) {
    return (
      <ListRow
        title={level.label}
        sub="This cannot be changed."
        right={<Badge variant="outline">{LEVEL_WORDS[level.level]}</Badge>}
      />
    );
  }

  return (
    <ListRow title={level.label} right={<Badge variant="outline">{LEVEL_WORDS[level.level]}</Badge>}>
      <form
        className="mt-2 flex flex-col gap-3"
        aria-label={level.label}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Level" htmlFor={ids.level}>
            <Select id={ids.level} value={choice} onChange={(e) => setChoice(e.target.value as ApprovalLevel)}>
              {(Object.keys(LEVEL_WORDS) as ApprovalLevel[]).map((value) => (
                <option key={value} value={value}>
                  {LEVEL_WORDS[value]}
                </option>
              ))}
            </Select>
          </Field>
          {level.allowed_limits.per_day && (
            <Field label={`Most ${level.allowed_limits.per_day_label}`} help="Leave empty for no daily number." htmlFor={ids.perDay}>
              <Input
                id={ids.perDay}
                type="number"
                inputMode="numeric"
                min={1}
                value={perDay}
                onChange={(e) => setPerDay(e.target.value)}
              />
            </Field>
          )}
          {level.allowed_limits.people && (
            <Field label="Only for these people" help="One name on each line. Leave empty for everyone." htmlFor={ids.people} className="sm:col-span-2">
              <Textarea id={ids.people} rows={2} value={people} onChange={(e) => setPeople(e.target.value)} />
            </Field>
          )}
        </div>
        {error && <ErrorLine>{error}</ErrorLine>}
        <FormActions>
          <Btn size="sm" variant="primary" type="submit" disabled={busy}>
            Save level
          </Btn>
          <Saved shown={saved} />
        </FormActions>
      </form>
      {confirming && (
        <Dialog
          title="Carry out without approval?"
          description={`The assistant will do this without asking you first, inside the limits you set: ${level.label.toLowerCase()}.`}
          onClose={() => setConfirming(false)}
          footer={
            <>
              <Btn onClick={() => setConfirming(false)}>Cancel</Btn>
              <Btn variant="primary" onClick={() => void save()}>
                Confirm
              </Btn>
            </>
          }
        />
      )}
    </ListRow>
  );
}

function LevelsBlock() {
  const { client } = useAssistant();
  const load = useCallback(() => client.approvalLevels(), [client]);
  const { data, error, setData } = useResource(load, { refresh: false });
  return (
    <Panel as="section" aria-labelledby="levels-heading" className="flex flex-col gap-3">
      <SecHead
        id="levels-heading"
        title="Approval levels"
        description="What your assistant may do for you, and the limits."
      />
      {error && !data && <ErrorLine>{messageOf(error)}</ErrorLine>}
      {data && (
        <List label="Approval levels">
          {data.map((level) => (
            <LevelRow
              key={level.kind}
              level={level}
              onSaved={(view) => setData((current) => current?.map((l) => (l.kind === view.kind ? view : l)))}
            />
          ))}
        </List>
      )}
    </Panel>
  );
}

// --- Projects followed ----------------------------------------------------------------------

function ProjectRow({ project, onChanged }: { project: ProjectView; onChanged: () => Promise<void> }) {
  const { client } = useAssistant();
  const resolvePerson = usePeople();
  const nameId = useId();
  const [choosing, setChoosing] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (call: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await call();
      setChoosing(false);
      setName("");
      await onChanged();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  const withConfirmer = async (then: (personId: string | undefined) => Promise<unknown>) => {
    if (!name.trim()) return then(undefined);
    const person = await resolvePerson(name, { peopleOnly: true });
    return then(person.id);
  };

  return (
    <ListRow
      title={project.name}
      sub={project.followed ? `Followed · ${project.confirmer?.display_name ?? "Its owner"} confirms its status` : "Not followed"}
      right={
        !choosing && (
          <>
            {project.followed ? (
              <>
                <Btn size="sm" onClick={() => setChoosing(true)}>
                  Change who confirms
                </Btn>
                <Btn size="sm" variant="ghost" disabled={busy} onClick={() => void run(() => client.unfollow(project.id))}>
                  Stop following
                </Btn>
              </>
            ) : (
              <Btn size="sm" variant="primary" onClick={() => setChoosing(true)}>
                Follow
              </Btn>
            )}
          </>
        )
      }
    >
      {choosing && (
        <form
          className="mt-2 flex flex-col gap-2 rounded-panel bg-brand-accent-soft/50 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(() =>
              withConfirmer((personId) => {
                if (project.followed) {
                  if (!personId) throw new ApiError(422, "name_needed", "Name a person.");
                  return client.changeConfirmer(project.id, personId);
                }
                return client.follow(project.id, personId);
              }),
            );
          }}
        >
          <Field
            label="Who confirms its status"
            help={project.followed ? undefined : "Leave empty for the project's owner."}
            htmlFor={nameId}
          >
            <Input id={nameId} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </Field>
          <FormActions>
            <Btn size="sm" variant="primary" type="submit" disabled={busy}>
              {project.followed ? "Change" : "Follow"}
            </Btn>
            <Btn size="sm" variant="ghost" onClick={() => setChoosing(false)}>
              Cancel
            </Btn>
          </FormActions>
        </form>
      )}
      {error && (
        <div className="mt-2">
          <ErrorLine>{error}</ErrorLine>
        </div>
      )}
    </ListRow>
  );
}

function ProjectsBlock() {
  const { client } = useAssistant();
  const load = useCallback(() => client.projects(), [client]);
  const { data, error, reload } = useResource(load);
  return (
    <Panel as="section" aria-labelledby="projects-heading" className="flex flex-col gap-3">
      <SecHead id="projects-heading" title="Projects followed" description="Their status is in your report." />
      {error && !data && <ErrorLine>{messageOf(error)}</ErrorLine>}
      {data &&
        (data.projects.length ? (
          <List label="Projects">
            {data.projects.map((project) => (
              <ProjectRow key={project.id} project={project} onChanged={reload} />
            ))}
          </List>
        ) : (
          <Empty title="You can see no projects." />
        ))}
    </Panel>
  );
}

// --- The page -------------------------------------------------------------------------------

function Settings() {
  const { client, paths } = useAssistant();
  const [assistant, setAssistant] = useState<AssistantView | null>(null);
  const [missing, setMissing] = useState(false);
  const load = useCallback(async () => {
    try {
      const found = await client.assistant();
      setAssistant(found);
      return found;
    } catch (failure) {
      if (failure instanceof ApiError && failure.notFound) setMissing(true);
      throw failure;
    }
  }, [client]);
  const { error } = useResource(load);

  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-ui-title text-brand-ink">Settings</h1>
        <Btn href={paths.assistant} size="sm" variant="ghost">
          <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
          Back to your assistant
        </Btn>
      </header>
      {missing ? (
        <Panel>
          <Empty title={NO_ASSISTANT} />
        </Panel>
      ) : error && !assistant ? (
        <ErrorLine>{messageOf(error)}</ErrorLine>
      ) : !assistant ? (
        <p className="text-ui text-brand-ink-muted">Reading your settings.</p>
      ) : (
        <>
          <PauseBlock assistant={assistant} onChanged={setAssistant} />
          <PreferencesBlock />
          <LevelsBlock />
          <ProjectsBlock />
        </>
      )}
    </>
  );
}

export function SettingsPage({ client, basePath }: PageProps) {
  return (
    <AssistantProvider client={client} basePath={basePath}>
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-3 py-4 sm:px-5 sm:py-6">
        <Settings />
      </main>
    </AssistantProvider>
  );
}
