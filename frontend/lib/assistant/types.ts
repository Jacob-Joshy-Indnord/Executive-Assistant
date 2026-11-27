/** The shapes the `/api/assistant` addresses answer with. Times are ISO text in UTC. */

export type Colour = "green" | "amber" | "red";

export type Named = { id: string; display_name: string | null };
export type PersonKind = "person" | "virtual_employee";
export type Owner = Named & { kind: PersonKind | null };

// --- The assistant and its settings ---------------------------------------------------------

export type AssistantState = "new" | "active" | "paused" | "ended";

export type AssistantView = {
  id: string;
  display_name: string;
  manager_id: string;
  state: AssistantState;
  form_offered: boolean;
  /** False when paused by the manager or by the organisation. */
  running: boolean;
  version: number;
};

export type Weekday =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday";

export type ReportFrequency = "weekly" | "every_two_weeks" | "monthly" | "off";

export type PreferencesForm = {
  working_time: {
    days?: Weekday[] | null;
    starts?: string | null;
    ends?: string | null;
    time_zone?: string | null;
  } | null;
  tone: { tone?: string | null; sign_off?: string | null } | null;
  priorities: { priorities?: string[] | null; key_people?: string[] | null } | null;
  follow_ups: {
    follow_ups?: number | null;
    answer_within_working_days?: number | null;
    late_after_working_days?: number | null;
    notices_per_owner_per_day?: number | null;
  } | null;
  report: {
    frequency?: ReportFrequency | null;
    day?: Weekday | null;
    time?: string | null;
    ask_owners_working_days_before?: number | null;
    includes?: string | null;
    leaves_out?: string | null;
  } | null;
};

export type EffectiveSettings = {
  working_days: Weekday[];
  starts: string;
  ends: string;
  time_zone: string;
  follow_ups: number | null;
  answer_within_working_days: number;
  late_after_working_days: number;
  notices_per_owner_per_day: number;
  report_frequency: ReportFrequency;
  report_day: Weekday;
  report_time: string;
  ask_owners_working_days_before: number;
};

export type PreferencesView = {
  form: PreferencesForm;
  saved: boolean;
  skipped: boolean;
  form_offered: boolean;
  settings: EffectiveSettings;
};

export type ApprovalLevel = "not_allowed" | "with_approval" | "without_approval";

/** Limits as shown: the people named, each with their id. */
export type ApprovalLimits = { people: Named[] | null; per_day: number | null };

/** Limits as the manager gives them: people by name (BR-12). */
export type LimitsChange = { people: string[] | null; per_day: number | null };

export type ApprovalLevelView = {
  kind: string;
  label: string;
  level: ApprovalLevel;
  locked: boolean;
  limits: ApprovalLimits;
  allowed_limits: { people: boolean; per_day: boolean; per_day_label: string };
};

export type LevelChange = { level: ApprovalLevel; limits?: LimitsChange };

export type ColourMeaning = { colour: Colour; meaning: string | null };

export type OrganisationSettingsView = {
  organisation_id: string;
  colour_meanings: Record<Colour, string | null>;
};

// --- Work and chat ------------------------------------------------------------------------------

export type WorkState = "waiting" | "running" | "done" | "refused" | "failed" | "cancelled";

export type WorkView = {
  id: string;
  kind: string;
  state: WorkState;
  reason: string | null;
  can_retry: boolean;
  refused_count: number;
  refused: string[];
  created_at: string;
  finished_at: string | null;
  reply?: string | null;
};

export type ChatMessageView = {
  id: string;
  author: "manager" | "assistant";
  text: string;
  created_at: string;
  work: WorkView | null;
};

export type ChatView = { messages: ChatMessageView[] };

// --- Requests ----------------------------------------------------------------------------------

export type RequestState =
  | "waiting"
  | "approved"
  | "done"
  | "declined"
  | "refused"
  | "failed"
  | "withdrawn"
  | "undone";

export type RequestView = {
  id: string;
  kind: string;
  label: string;
  action_kind: string;
  state: RequestState;
  effect: string | null;
  target: Named | null;
  project: { id: string; name: string | null } | null;
  text: string | null;
  original_text: string | null;
  edited_text: string | null;
  editable: boolean;
  to_owner: boolean;
  reason: string | null;
  declined_by: "flow" | "manager" | null;
  approved_by: string | null;
  can_retry: boolean;
  can_undo: boolean;
  undo_reason: string | null;
  created_at: string;
  decided_at: string | null;
  done_at: string | null;
  undone_at: string | null;
};

export type RequestList = { count: number; requests: RequestView[] };

// --- Action items ---------------------------------------------------------------------------------

export type ItemState = "waiting_for_owner" | "open" | "declined" | "done" | "closed";
export type UpdateWord = "on_track" | "done" | "blocked";

export type UpdateView = {
  id: string;
  word: UpdateWord;
  label: string;
  note: string | null;
  date_asked_for: string | null;
  author_id: string;
  source: "owner" | "assignment";
  confirmed: boolean;
  at: string;
};

export type ItemCan =
  | "accept"
  | "propose_date"
  | "decline"
  | "update"
  | "follow_ups"
  | "extend"
  | "reassign"
  | "close"
  | "done";

export type ItemView = {
  id: string;
  text: string;
  state: ItemState;
  due_date: string;
  origin: "manager" | "chat";
  project_id: string | null;
  owner: Owner;
  assignment: { id: string; state: string | null } | null;
  follow_up_times: string[];
  follow_up_note: string | null;
  answer_reason: string | null;
  date_asked_for: string | null;
  close_reason: string | null;
  accepted_at: string | null;
  created_at: string;
  updates: UpdateView[];
  last_update: UpdateView | null;
  can: ItemCan[];
  attention?: { id: string; reason: string; reason_code: string | null; choices: string[] } | null;
  /** For the owner: the manager who gave the item. */
  for?: Named | null;
  /** For the owner: what is asked of them about it now, and by when. */
  ask?: { asks: "accept" | "update"; answer_by: string } | null;
};

export type ItemList = { count: number; open?: number; items: ItemView[] };

export type NewItem = {
  text: string;
  owner_id: string;
  due_date: string;
  project_id?: string | null;
  assignment_id?: string | null;
};

export type AssignmentView = {
  id: string;
  title: string;
  virtual_employee_id: string;
  state: string;
};

// --- Attention ---------------------------------------------------------------------------------------

export type AttentionView = {
  id: string;
  state: "open" | "decided" | "cleared";
  subject_kind: string;
  reason: string;
  reason_code: string | null;
  label: string | null;
  known: string;
  choices: string[];
  asked_date: string | null;
  decision: string | null;
  created_at: string;
  item: {
    id: string;
    text: string;
    state: ItemState;
    due_date: string;
    answer_reason: string | null;
  } | null;
  owner: Owner | null;
  last_update: UpdateView | null;
  prepared_message: string | null;
  project?: { id: string; name: string | null };
  confirmer?: Named;
};

export type AttentionList = { count: number; entries: AttentionView[] };

export type Decision = {
  choice: string;
  due_date?: string;
  owner_id?: string;
  assignment_id?: string;
  person_id?: string;
  message?: string;
  reason?: string;
};

// --- Project status ---------------------------------------------------------------------------------

export type Mark = "confirmed" | "corrected" | "awaiting_confirmation" | "no_confirmed_status";

export type StatusLine = {
  project_id: string;
  project_name: string;
  colour: Colour | null;
  colour_words: string | null;
  reason: string | null;
  what_changed: string | null;
  what_comes_next: string | null;
  source: string | null;
  date: string | null;
  statement_id: string | null;
  mark: Mark;
  mark_words: string;
  text: string;
  changed_since_last: boolean;
};

export type StatusView = {
  lines: StatusLine[];
  confirmed: number;
  awaiting_confirmation: number;
  next_report_at: string | null;
};

export type ReportView = {
  id: string;
  created_at: string;
  made_by: "schedule" | "manager";
  due_at: string | null;
  lines: StatusLine[];
  changed_since_last: { project_id: string; project_name: string | null }[];
};

export type ReportList = { count: number; reports: ReportView[] };

export type ProjectView = {
  id: string;
  name: string;
  owner_id: string;
  budget: string;
  spend: string;
  followed: boolean;
  confirmer: Named | null;
};

export type Wording = { reason: string; what_changed: string; what_comes_next: string };

export type StatementView = {
  /** In the owner's list: the manager who follows the project. */
  for?: Named;
  id: string;
  project_id: string;
  project_name?: string | null;
  owner_id: string;
  colour: Colour;
  colour_words: string;
  wording: Wording;
  source: string;
  author: Named;
  mark: "confirmed" | "corrected";
  mark_words: string;
  created_at: string;
  previous_version_id: string | null;
  correction: {
    by: Named;
    at: string;
    points: ("colour" | "reason" | "what_changed" | "what_comes_next")[];
    of: "draft" | "earlier_version";
    drafted_at: string | null;
  } | null;
};

export type StatusRequestView = {
  id: string;
  asks: "status" | "confirm";
  state: string;
  for: Named;
  project: { id: string; name: string | null };
  start: "draft" | "last_confirmed" | "empty";
  wording: Wording | null;
  colour: Colour | null;
  source: string | null;
  needed_by: string;
  colours: ColourMeaning[];
};

export type StatementAnswer = {
  colour: Colour;
  reason: string;
  what_changed?: string;
  what_comes_next?: string;
};

export type PersonMatch = { id: string; display_name: string; kind: PersonKind };

export type WaitingCount = { count: number; approvals: number; attention: number };
