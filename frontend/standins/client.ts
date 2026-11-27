"use client";

/**
 * The demonstration's clients. The module's pages get the module's client, made with a fetch
 * that names the person chosen in the bar, as Flow's sign-in would. The bar gets its own small
 * client for the stand-ins' addresses under `/api/demo`, which are not part of the module.
 */

import { ApiError, createAssistantClient, messageOf, type AssistantClient, type FetchLike } from "@/lib/assistant/client";
import { PERSON_HEADER, currentPerson } from "./person";

export const DEMO_BASE_URL = "/api/assistant";
export const DEMO_BASE_PATH = "/assistant";
export const BAR_BASE_URL = "/api/demo";

/** The browser's fetch, with the chosen person named on every call. */
export function personFetch(fetchFn: FetchLike = (input, init) => window.fetch(input, init)): FetchLike {
  return (input, init = {}) => {
    const headers = new Headers(init.headers);
    const person = currentPerson();
    if (person) headers.set(PERSON_HEADER, person);
    return fetchFn(input, { ...init, headers, credentials: "same-origin" });
  };
}

let shared: AssistantClient | null = null;

export function demoClient(): AssistantClient {
  shared ??= createAssistantClient({ baseUrl: DEMO_BASE_URL, fetch: personFetch() });
  return shared;
}

// --- The bar's own addresses ----------------------------------------------------------------

export type BarPerson = { id: string; name: string; role: string };
export type BarAssistant = { flow_id: string; manager_id: string; manager_name: string; state: string };
export type Fault = "assistant_faults" | "assistant_refusing" | "approval" | "notices" | "records";
export type BarNotice = { to: string; text: string };
export type AssignmentState = "in_progress" | "blocked" | "waiting_for_review" | "approved";
export type BarAssignment = { id: string; title: string; state: AssignmentState };

export type BarState = {
  now: string;
  people: BarPerson[];
  assistants: BarAssistant[];
  all_paused: boolean;
  faults: Record<Fault, boolean>;
  notices: BarNotice[];
  assignments: BarAssignment[];
  carried_out: number;
  records: number;
};

export type ClockMove = "hour" | "day" | "next";

export function createBarClient(fetchFn: FetchLike = (input, init) => window.fetch(input, init)) {
  async function call(method: string, path: string, body?: unknown): Promise<BarState> {
    let response: Response;
    try {
      response = await fetchFn(`${BAR_BASE_URL}${path}`, {
        method,
        headers: { Accept: "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "unreachable", "Could not reach the demonstration. Start the back end and try again.");
    }
    const data = (await response.json().catch(() => null)) as { detail?: { code?: string; message?: string } } | null;
    if (!response.ok) {
      throw new ApiError(
        response.status,
        data?.detail?.code ?? "error",
        data?.detail?.message ?? "Something went wrong. Try again.",
      );
    }
    return data as BarState;
  }

  return {
    state: () => call("GET", ""),
    moveClock: (move: ClockMove) => call("POST", "/clock", { move }),
    setFault: (fault: Fault, on: boolean) => call("PUT", "/faults", { fault, on }),
    reset: () => call("POST", "/reset"),
    setUp: (managerId: string) => call("POST", "/set-up", { manager_id: managerId }),
    handOver: (flowId: string, newManagerId: string) =>
      call("POST", "/hand-over", { flow_id: flowId, new_manager_id: newManagerId }),
    end: (flowId: string) => call("POST", "/end", { flow_id: flowId }),
    pauseAll: (paused: boolean) => call("POST", "/pause-all", { paused }),
    changeAssignment: (assignmentId: string, state: AssignmentState) =>
      call("POST", "/assignment", { assignment_id: assignmentId, state }),
  };
}

export type BarClient = ReturnType<typeof createBarClient>;

export { messageOf };
