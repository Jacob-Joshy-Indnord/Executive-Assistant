"use client";

/**
 * The demonstration bar (Story 7.1). It plays Flow's part: it names the person asking, moves
 * the clock, turns the stand-ins' faults on, starts again from the sample data, and sets up,
 * hands over, ends or pauses assistants by calling the module as Flow would. It lives in
 * `standins/`, so the module's pages never include it.
 */

import { useId, useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Clock } from "lucide-react";
import { Badge, Btn, ErrorLine, Field, Select, Toggle } from "@/components/assistant/parts";
import { assistantPaths } from "@/lib/assistant/paths";
import { DEMO_BASE_PATH, type AssignmentState, type BarState, type ClockMove, type Fault } from "./client";

export const FAULT_LABELS: Record<Fault, string> = {
  assistant_faults: "Assistant returns faulty requests",
  assistant_refusing: "Assistant cannot start work",
  approval: "Approval cannot carry out",
  notices: "Notices cannot be sent",
  records: "Audit records cannot be written",
};

export const ASSIGNMENT_STATE_WORDS: Record<AssignmentState, string> = {
  in_progress: "In progress",
  blocked: "Blocked",
  waiting_for_review: "Waiting for review",
  approved: "Approved",
};

const CLOCK_MOVES: { move: ClockMove; label: string }[] = [
  { move: "hour", label: "Add an hour" },
  { move: "day", label: "Add a day" },
  { move: "next", label: "Go to next step" },
];

export function formatBarTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(iso));
}

export type BarActions = {
  choosePerson: (personId: string) => void;
  moveClock: (move: ClockMove) => void;
  setFault: (fault: Fault, on: boolean) => void;
  reset: () => void;
  setUp: (managerId: string) => void;
  handOver: (flowId: string, newManagerId: string) => void;
  end: (flowId: string) => void;
  pauseAll: (paused: boolean) => void;
  changeAssignment: (assignmentId: string, state: AssignmentState) => void;
};

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-2 rounded-panel border border-brand-border p-3 dark:border-brand-border/40">
      <legend className="px-1 text-ui-xs font-semibold uppercase tracking-wide text-brand-ink-muted">{title}</legend>
      {children}
    </fieldset>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex min-w-0 flex-wrap items-end gap-2">{children}</div>;
}

export function DemoBar({
  state,
  person,
  busy,
  error,
  actions,
}: {
  state: BarState | null;
  person: string | null;
  busy: boolean;
  error: string | null;
  actions: BarActions;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const people = state?.people ?? [];
  const assistants = state?.assistants ?? [];
  const [setUpFor, setSetUpFor] = useState("");
  const [handOverFrom, setHandOverFrom] = useState("");
  const [handOverTo, setHandOverTo] = useState("");
  const [toEnd, setToEnd] = useState("");
  const [assignment, setAssignment] = useState("");
  const [assignmentState, setAssignmentState] = useState<AssignmentState>("in_progress");
  const paths = assistantPaths(DEMO_BASE_PATH);

  // A choice that no longer exists falls back to the first one offered.
  const pick = (value: string, options: string[]) => (options.includes(value) ? value : (options[0] ?? ""));
  const setUpChoice = pick(setUpFor, people.map((p) => p.id));
  const fromChoice = pick(handOverFrom, assistants.map((a) => a.flow_id));
  const toChoice = pick(handOverTo, people.map((p) => p.id));
  const endChoice = pick(toEnd, assistants.map((a) => a.flow_id));
  const assignments = state?.assignments ?? [];
  const assignmentChoice = pick(assignment, assignments.map((a) => a.id));

  return (
    <div
      role="region"
      aria-label="Demonstration"
      className="border-b border-brand-border bg-brand-accent-soft dark:border-brand-border/40"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 px-3 py-2 sm:px-5">
        <Row>
          <Badge variant="accent">Demonstration</Badge>
          <Field label="Person" htmlFor={`${id}-person`} className="w-full sm:w-64">
            <Select
              id={`${id}-person`}
              value={person ?? ""}
              disabled={!state}
              onChange={(event) => actions.choosePerson(event.target.value)}
            >
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.role})
                </option>
              ))}
            </Select>
          </Field>
          <Btn
            size="sm"
            variant="ghost"
            aria-expanded={open}
            aria-controls={`${id}-controls`}
            onClick={() => setOpen((was) => !was)}
          >
            {open ? <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" /> : <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />}
            {open ? "Hide controls" : "Show controls"}
          </Btn>
        </Row>
        <Row>
          <p className="flex items-center gap-1.5 text-ui text-brand-ink" aria-live="polite">
            <Clock aria-hidden="true" className="h-3.5 w-3.5 text-brand-ink-muted" />
            <span>
              Time: <time dateTime={state?.now}>{state ? formatBarTime(state.now) : "not known yet"}</time>
            </span>
          </p>
          {CLOCK_MOVES.map(({ move, label }) => (
            <Btn key={move} size="sm" disabled={busy || !state} onClick={() => actions.moveClock(move)}>
              {label}
            </Btn>
          ))}
        </Row>
        <p className="text-ui-sm text-brand-ink-muted" role="status">
          {state ? `${state.notices.length} ${state.notices.length === 1 ? "notice" : "notices"} sent` : ""}
          {state?.all_paused ? ". Everything is paused." : ""}
        </p>
        {error && <ErrorLine>{error}</ErrorLine>}

        {open && state && (
          <div id={`${id}-controls`} className="grid grid-cols-1 gap-2 pb-2 sm:grid-cols-2">
            <Group title="Pages">
              <nav aria-label="Pages" className="flex flex-wrap gap-2">
                <Btn size="sm" variant="ghost" href={paths.assistant}>
                  Assistant
                </Btn>
                <Btn size="sm" variant="ghost" href={paths.requests}>
                  Requests for you
                </Btn>
                <Btn size="sm" variant="ghost" href={paths.settings}>
                  Settings
                </Btn>
                <Btn size="sm" variant="ghost" href={paths.organisation}>
                  Organisation
                </Btn>
              </nav>
            </Group>

            <Group title="Faults">
              {(Object.keys(FAULT_LABELS) as Fault[]).map((fault) => (
                <Toggle
                  key={fault}
                  label={FAULT_LABELS[fault]}
                  checked={state.faults[fault]}
                  disabled={busy}
                  onChange={(on) => actions.setFault(fault, on)}
                />
              ))}
            </Group>

            <Group title="As Flow">
              <Row>
                <Field label="Set up an assistant for" htmlFor={`${id}-setup`} className="min-w-0 flex-1">
                  <Select id={`${id}-setup`} value={setUpChoice} onChange={(e) => setSetUpFor(e.target.value)}>
                    {people.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Btn size="sm" disabled={busy || !setUpChoice} onClick={() => actions.setUp(setUpChoice)}>
                  Set up
                </Btn>
              </Row>
              <Row>
                <Field label="Hand over the assistant of" htmlFor={`${id}-from`} className="min-w-0 flex-1">
                  <Select id={`${id}-from`} value={fromChoice} onChange={(e) => setHandOverFrom(e.target.value)}>
                    {assistants.map((a) => (
                      <option key={a.flow_id} value={a.flow_id}>
                        {a.manager_name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="To" htmlFor={`${id}-to`} className="min-w-0 flex-1">
                  <Select id={`${id}-to`} value={toChoice} onChange={(e) => setHandOverTo(e.target.value)}>
                    {people.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Btn
                  size="sm"
                  disabled={busy || !fromChoice || !toChoice}
                  onClick={() => actions.handOver(fromChoice, toChoice)}
                >
                  Hand over
                </Btn>
              </Row>
              <Row>
                <Field label="End the assistant of" htmlFor={`${id}-end`} className="min-w-0 flex-1">
                  <Select id={`${id}-end`} value={endChoice} onChange={(e) => setToEnd(e.target.value)}>
                    {assistants.map((a) => (
                      <option key={a.flow_id} value={a.flow_id}>
                        {a.manager_name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Btn size="sm" variant="danger" disabled={busy || !endChoice} onClick={() => actions.end(endChoice)}>
                  End
                </Btn>
              </Row>
              <Row>
                <Btn size="sm" disabled={busy} onClick={() => actions.pauseAll(!state.all_paused)}>
                  {state.all_paused ? "Resume everything" : "Pause everything"}
                </Btn>
              </Row>
            </Group>

            <Group title="Work data">
              <Row>
                <Field label="Assignment" htmlFor={`${id}-assignment`} className="min-w-0 flex-1">
                  <Select id={`${id}-assignment`} value={assignmentChoice} onChange={(e) => setAssignment(e.target.value)}>
                    {assignments.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.title} ({ASSIGNMENT_STATE_WORDS[a.state]})
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="New state" htmlFor={`${id}-assignment-state`} className="min-w-0 flex-1">
                  <Select
                    id={`${id}-assignment-state`}
                    value={assignmentState}
                    onChange={(e) => setAssignmentState(e.target.value as AssignmentState)}
                  >
                    {(Object.keys(ASSIGNMENT_STATE_WORDS) as AssignmentState[]).map((s) => (
                      <option key={s} value={s}>
                        {ASSIGNMENT_STATE_WORDS[s]}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Btn
                  size="sm"
                  disabled={busy || !assignmentChoice}
                  onClick={() => actions.changeAssignment(assignmentChoice, assignmentState)}
                >
                  Change state
                </Btn>
              </Row>
            </Group>

            <Group title="Sent">
              {state.notices.length === 0 ? (
                <p className="text-ui text-brand-ink-muted">No notices sent.</p>
              ) : (
                <ol aria-label="Notices sent" className="flex max-h-40 flex-col gap-1 overflow-y-auto">
                  {state.notices.map((notice, index) => (
                    <li key={index} className="text-ui-sm text-brand-ink">
                      <span className="font-medium">To {notice.to}:</span> {notice.text}
                    </li>
                  ))}
                </ol>
              )}
              <p className="text-ui-sm text-brand-ink-muted">
                {state.carried_out} carried out by Flow. {state.records} audit records written.
              </p>
            </Group>

            <div className="sm:col-span-2">
              <Btn variant="danger" size="sm" disabled={busy} onClick={actions.reset}>
                Start again
              </Btn>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
