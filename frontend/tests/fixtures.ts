/** Sample answers shaped like the module's. */
import type {
  AssistantView,
  AttentionView,
  ItemView,
  RequestView,
  StatusView,
  WorkView,
} from "@/lib/assistant/types";

export const T = "2026-11-23T08:00:00+00:00";

export function assistant(over: Partial<AssistantView> = {}): AssistantView {
  return {
    id: "a1",
    display_name: "Executive Assistant",
    manager_id: "m1",
    state: "active",
    form_offered: false,
    running: true,
    version: 1,
    ...over,
  };
}

export function request(id: string, over: Partial<RequestView> = {}): RequestView {
  return {
    id,
    kind: "update_ask",
    label: "Update",
    action_kind: "ask_owner",
    state: "waiting",
    effect: `Ask the Legal lead about the contract review ${id}`,
    target: { id: "p-legal", display_name: "Legal lead" },
    project: null,
    text: "Could you give an update on the review?",
    original_text: "Could you give an update on the review?",
    edited_text: null,
    editable: true,
    to_owner: true,
    reason: null,
    declined_by: null,
    approved_by: null,
    can_retry: false,
    can_undo: false,
    undo_reason: null,
    created_at: T,
    decided_at: null,
    done_at: null,
    undone_at: null,
    ...over,
  };
}

export function entry(id: string, over: Partial<AttentionView> = {}): AttentionView {
  return {
    id,
    state: "open",
    subject_kind: "action_item",
    reason: "It is not done and its due date has passed.",
    reason_code: "item_overdue",
    label: "Overdue",
    known: "",
    choices: ["Extend", "Reassign", "Take it up", "Close"],
    asked_date: null,
    decision: null,
    created_at: T,
    item: { id: `item-${id}`, text: "Legal review of the vendor contracts", state: "open", due_date: "2026-11-24", answer_reason: null },
    owner: { id: "p-legal", display_name: "Legal lead", kind: "person" },
    last_update: null,
    prepared_message: "The legal review is late. Can we talk today?",
    ...over,
  };
}

export function item(id: string, over: Partial<ItemView> = {}): ItemView {
  return {
    id,
    text: `Finish the review ${id}`,
    state: "open",
    due_date: "2026-12-04",
    origin: "manager",
    project_id: null,
    owner: { id: "p-legal", display_name: "Legal lead", kind: "person" },
    assignment: null,
    follow_up_times: [],
    follow_up_note: null,
    answer_reason: null,
    date_asked_for: null,
    close_reason: null,
    accepted_at: T,
    created_at: T,
    updates: [],
    last_update: null,
    can: ["extend", "reassign", "close"],
    ...over,
  };
}

export const status: StatusView = {
  lines: [
    {
      project_id: "vendor",
      project_name: "Vendor consolidation",
      colour: "amber",
      colour_words: "Amber",
      reason: "Legal review is late.",
      what_changed: "",
      what_comes_next: "",
      source: "Legal lead",
      date: "2026-11-23",
      statement_id: "s1",
      mark: "corrected",
      mark_words: "Corrected by its owner",
      text: "Vendor consolidation. Amber. Legal review is late. Corrected by its owner.",
      changed_since_last: false,
    },
  ],
  confirmed: 1,
  awaiting_confirmation: 0,
  next_report_at: "2026-11-30T08:00:00+00:00",
};

export function work(over: Partial<WorkView> = {}): WorkView {
  return {
    id: "w1",
    kind: "request",
    state: "done",
    reason: null,
    can_retry: false,
    refused_count: 1,
    refused: ["No one in the organisation is called Legal leed."],
    created_at: T,
    finished_at: T,
    reply: "I have proposed the action item for the Legal lead.",
    ...over,
  };
}

/** The routes of a manager's page with one of everything. */
export function managerRoutes() {
  return {
    "POST assistant/opened": assistant(),
    "GET assistant": assistant(),
    "GET status": status,
    "GET requests": { count: 2, requests: [request("r1"), request("r2", { label: "Message", to_owner: false })] },
    "GET attention": { count: 1, entries: [entry("e1")] },
    "GET action-items": { count: 2, open: 2, items: [item("i1"), item("i2")] },
    "GET done": { count: 0, requests: [] },
    "GET chat": {
      messages: [
        {
          id: "c1",
          author: "manager",
          text: "Remind the Legal lead to finish the review by Friday",
          created_at: T,
          work: work(),
        },
      ],
    },
    "GET reports": { count: 0, reports: [] },
    "GET work/w1": work(),
  } as Record<string, unknown>;
}
