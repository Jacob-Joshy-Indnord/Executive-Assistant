/**
 * The one API client every screen uses. It is made from a base address and a fetch
 * function that are passed in, so that Flow (or the demonstration) decides where the
 * module lives and how the person asking is named.
 */

import type {
  ApprovalLevelView,
  AssistantView,
  AttentionList,
  AttentionView,
  ChatMessageView,
  ChatView,
  ColourMeaning,
  Decision,
  ItemList,
  ItemView,
  LevelChange,
  NewItem,
  OrganisationSettingsView,
  PersonMatch,
  PreferencesForm,
  PreferencesView,
  ProjectView,
  ReportList,
  ReportView,
  RequestList,
  RequestView,
  StatementAnswer,
  StatementView,
  StatusRequestView,
  StatusView,
  UpdateWord,
  WaitingCount,
  WorkView,
  AssignmentView,
  Colour,
} from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export type ClientOptions = {
  /** Where the module's addresses start, e.g. `/api/assistant`. */
  baseUrl: string;
  fetch: FetchLike;
};

/** A failure to show: always one plain sentence, never a code. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get notFound(): boolean {
    return this.status === 404;
  }
}

export const UNREACHABLE = "Could not reach the server. Try again.";
const UNEXPECTED = "Something went wrong. Try again.";

/** The sentence to show for any failure. */
export function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return UNEXPECTED;
}

function join(base: string, path: string): string {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

const enc = encodeURIComponent;

export function createAssistantClient({ baseUrl, fetch: fetchFn }: ClientOptions) {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetchFn(join(baseUrl, path), {
        method,
        headers: { Accept: "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "unreachable", UNREACHABLE);
    }
    let data: unknown = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok) {
      const detail = (data as { detail?: unknown } | null)?.detail;
      if (detail && typeof detail === "object" && "message" in detail) {
        const { code, message } = detail as { code?: unknown; message?: unknown };
        throw new ApiError(
          response.status,
          typeof code === "string" ? code : "error",
          typeof message === "string" && message ? message : UNEXPECTED,
        );
      }
      // FastAPI's own validation answer: show a plain sentence, not its details.
      const fallback = response.status === 422 ? "Check what you entered and try again." : UNEXPECTED;
      throw new ApiError(response.status, "error", fallback);
    }
    return data as T;
  }

  const get = <T>(path: string) => call<T>("GET", path);
  const post = <T>(path: string, body?: unknown) => call<T>("POST", path, body ?? {});
  const put = <T>(path: string, body: unknown) => call<T>("PUT", path, body);

  return {
    // The assistant and its settings
    assistant: () => get<AssistantView>("assistant"),
    opened: (timeZone: string | null) => post<AssistantView>("assistant/opened", { time_zone: timeZone }),
    pause: () => post<AssistantView>("assistant/pause"),
    resume: () => post<AssistantView>("assistant/resume"),
    preferences: () => get<PreferencesView>("preferences"),
    savePreferences: (form: PreferencesForm) => put<PreferencesView>("preferences", form),
    skipPreferences: () => post<PreferencesView>("preferences/skip"),
    approvalLevels: () => get<ApprovalLevelView[]>("approval-levels"),
    changeApprovalLevel: (kind: string, change: LevelChange) =>
      put<ApprovalLevelView>(`approval-levels/${enc(kind)}`, change),
    waitingCount: () => get<WaitingCount>("waiting-count"),

    // Chat and work
    chat: () => get<ChatView>("chat"),
    sendChat: (text: string) => post<ChatMessageView>("chat", { text }),
    work: (id: string) => get<WorkView>(`work/${enc(id)}`),
    retryWork: (id: string) => post<WorkView>(`work/${enc(id)}/retry`),

    // Requests
    requests: () => get<RequestList>("requests"),
    approve: (id: string, text?: string) =>
      post<RequestView>(`requests/${enc(id)}/approve`, text === undefined ? {} : { text }),
    decline: (id: string, reason?: string) =>
      post<RequestView>(`requests/${enc(id)}/decline`, reason ? { reason } : {}),
    approveAll: () => post<unknown>("requests/approve-all"),
    retryRequest: (id: string) => post<RequestView>(`requests/${enc(id)}/retry`),
    undoRequest: (id: string) => post<RequestView>(`requests/${enc(id)}/undo`),
    done: () => get<RequestList>("done"),

    // Action items
    items: () => get<ItemList>("action-items"),
    addItem: (item: NewItem) => post<ItemView>("action-items", item),
    myItems: () => get<ItemList>("my-action-items"),
    assignments: () => get<{ assignments: AssignmentView[] }>("assignments"),
    item: (id: string) => get<ItemView>(`action-items/${enc(id)}`),
    acceptItem: (id: string) => post<ItemView>(`action-items/${enc(id)}/accept`),
    proposeDate: (id: string, dueDate: string, reason: string) =>
      post<ItemView>(`action-items/${enc(id)}/propose-date`, { due_date: dueDate, reason }),
    declineItem: (id: string, reason: string) =>
      post<ItemView>(`action-items/${enc(id)}/decline`, { reason }),
    giveUpdate: (id: string, update: { word: UpdateWord; note?: string; date_asked_for?: string }) =>
      post<ItemView>(`action-items/${enc(id)}/updates`, update),
    markDone: (id: string) => post<ItemView>(`action-items/${enc(id)}/done`),
    extendItem: (id: string, dueDate: string) => post<ItemView>(`action-items/${enc(id)}/extend`, { due_date: dueDate }),
    reassignItem: (id: string, owner: { owner_id: string; assignment_id?: string }) =>
      post<ItemView>(`action-items/${enc(id)}/reassign`, owner),
    closeItem: (id: string, reason?: string) => post<ItemView>(`action-items/${enc(id)}/close`, reason ? { reason } : {}),

    // Attention
    attention: () => get<AttentionList>("attention"),
    decide: (id: string, decision: Decision) => post<AttentionView>(`attention/${enc(id)}/decide`, decision),

    // Project status
    projects: () => get<{ projects: ProjectView[] }>("projects"),
    follow: (projectId: string, confirmerId?: string) =>
      post<unknown>(`projects/${enc(projectId)}/follow`, confirmerId ? { confirmer_id: confirmerId } : {}),
    unfollow: (projectId: string) => post<unknown>(`projects/${enc(projectId)}/unfollow`),
    changeConfirmer: (projectId: string, personId: string) =>
      put<unknown>(`projects/${enc(projectId)}/confirmer`, { person_id: personId }),
    status: () => get<StatusView>("status"),
    reports: () => get<ReportList>("reports"),
    reportNow: () => post<ReportView>("reports"),
    statement: (id: string) => get<StatementView>(`statements/${enc(id)}`),
    myStatements: () => get<{ count: number; statements: StatementView[] }>("my-statements"),
    correctStatement: (id: string, answer: StatementAnswer) =>
      post<StatementView>(`statements/${enc(id)}/correct`, answer),
    statusRequests: () => get<{ count: number; requests: StatusRequestView[] }>("status-requests"),
    answerStatus: (id: string, answer: StatementAnswer) =>
      post<StatementView>(`status-requests/${enc(id)}/answer`, answer),

    // Organisation
    colours: () => get<ColourMeaning[]>("colours"),
    organisationSettings: () => get<OrganisationSettingsView>("organisation/settings"),
    changeColourMeanings: (meanings: Partial<Record<Colour, string | null>>) =>
      put<OrganisationSettingsView>("organisation/settings", { colour_meanings: meanings }),

    // People, named not addressed (BR-12)
    people: (name: string) => get<{ people: PersonMatch[] }>(`people?name=${enc(name)}`),
  };
}

export type AssistantClient = ReturnType<typeof createAssistantClient>;
