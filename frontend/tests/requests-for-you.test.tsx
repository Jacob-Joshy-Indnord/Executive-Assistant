/** Story 6.3: Requests for you (task-pUh2iBQ4jww). */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { RequestsForYouPage } from "@/components/assistant/RequestsForYouPage";
import type { StatementView, StatusRequestView } from "@/lib/assistant/types";
import { Refuse, fakeApi } from "./fakeApi";
import { T, item } from "./fixtures";

const MANAGER = { id: "m1", display_name: "Operations director" };
const COLOURS = [
  { colour: "green", meaning: "On plan" },
  { colour: "amber", meaning: "At risk, recoverable" },
  { colour: "red", meaning: "Needs a decision" },
];

function statusAsk(over: Partial<StatusRequestView> = {}): StatusRequestView {
  return {
    id: "sr1",
    asks: "confirm",
    state: "sent",
    for: MANAGER,
    project: { id: "crm", name: "CRM rollout" },
    start: "draft",
    wording: { reason: "Import is late.", what_changed: "Two tests failed.", what_comes_next: "Fix the import." },
    colour: null,
    source: "Flow assignment: Build the CRM data import",
    needed_by: "2026-11-30T08:00:00+00:00",
    colours: COLOURS as StatusRequestView["colours"],
    ...over,
  };
}

function statement(over: Partial<StatementView> = {}): StatementView {
  return {
    id: "st1",
    project_id: "vendor",
    project_name: "Vendor consolidation",
    owner_id: "p-legal",
    colour: "amber",
    colour_words: "Amber",
    wording: { reason: "Two contracts are still open.", what_changed: "", what_comes_next: "" },
    source: "Legal lead",
    author: { id: "p-legal", display_name: "Legal lead" },
    mark: "confirmed",
    mark_words: "Confirmed",
    created_at: T,
    previous_version_id: null,
    correction: null,
    for: MANAGER,
    ...over,
  };
}

function routes() {
  return {
    "GET my-action-items": {
      count: 3,
      items: [
        item("i1", { state: "waiting_for_owner", can: ["accept", "propose_date", "decline"], for: MANAGER, ask: { asks: "accept", answer_by: "2026-11-24T18:00:00+00:00" } }),
        item("i2", { can: ["update"], for: MANAGER, ask: { asks: "update", answer_by: "2026-11-25T18:00:00+00:00" } }),
        item("i3", { can: ["update"], for: MANAGER, ask: null }),
      ],
    },
    "GET status-requests": { count: 1, requests: [statusAsk()] },
    "GET my-statements": { count: 1, statements: [statement()] },
    "GET colours": COLOURS,
  } as Record<string, unknown>;
}

async function open(r = routes()) {
  const api = fakeApi(r);
  render(<RequestsForYouPage client={api.client} basePath="/assistant" />);
  await screen.findByText(/requests? to answer|Nothing is asked of you/);
  return api;
}

const asked = () => screen.getByRole("region", { name: "Asked of you" });

describe("each request", () => {
  it("says who asks, what is needed and by when", async () => {
    await open();
    expect(within(asked()).getByText("3 requests to answer")).toBeInTheDocument();
    const accept = within(asked()).getByText("Finish the review i1").closest("li")!;
    expect(accept).toHaveTextContent("Operations director asks you to accept this action item:");
    expect(accept).toHaveTextContent(/Answer by/);
    const update = within(asked()).getByText("Finish the review i2").closest("li")!;
    expect(update).toHaveTextContent("Operations director asks you for an update:");
    const status = within(asked()).getByText(/to confirm the status of CRM rollout/).closest("li")!;
    expect(status).toHaveTextContent("Operations director asks you");
    expect(status).toHaveTextContent(/Needed by/);
  });

  it("is answered in place: accept", async () => {
    const r = routes();
    r["POST action-items/i1/accept"] = {};
    const api = await open(r);
    const row = within(asked()).getByText("Finish the review i1").closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Accept" }));
    expect(within(asked()).queryByText("Finish the review i1")).not.toBeInTheDocument();
    expect(within(asked()).getByText("2 requests to answer")).toBeInTheDocument();
    await waitFor(() => expect(api.calls.some((c) => c.path === "action-items/i1/accept")).toBe(true));
  });

  it("returns with the reason if the answer fails", async () => {
    const r = routes();
    r["POST action-items/i1/decline"] = new Refuse(422, "This item is no longer waiting for its owner.");
    const api = await open(r);
    const row = within(asked()).getByText("Finish the review i1").closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Decline" }));
    await userEvent.type(within(row).getByLabelText("Reason"), "Not mine");
    await userEvent.click(within(row).getByRole("button", { name: "Decline" }));
    expect(await screen.findByText("This item is no longer waiting for its owner.")).toBeInTheDocument();
    expect(within(asked()).getByText("Finish the review i1")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "action-items/i1/decline")?.body).toEqual({ reason: "Not mine" });
  });

  it("proposes another date with a reason", async () => {
    const r = routes();
    r["POST action-items/i1/propose-date"] = {};
    const api = await open(r);
    const row = within(asked()).getByText("Finish the review i1").closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Propose another date" }));
    await userEvent.type(within(row).getByLabelText("Date you can do it by"), "2030-02-01");
    await userEvent.type(within(row).getByLabelText("Reason"), "Waiting for the supplier");
    await userEvent.click(within(row).getByRole("button", { name: "Propose date" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "action-items/i1/propose-date")?.body).toEqual({
        due_date: "2030-02-01",
        reason: "Waiting for the supplier",
      }),
    );
  });

  it("gives an update in one step", async () => {
    const r = routes();
    r["POST action-items/i2/updates"] = {};
    const api = await open(r);
    const row = within(asked()).getByText("Finish the review i2").closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Update" }));
    await userEvent.click(within(row).getByRole("button", { name: "Send update" }));
    expect(within(row).getByText("Choose On track, Done or Blocked.")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("radio", { name: "Blocked" }));
    await userEvent.type(within(row).getByLabelText("Note (optional)"), "Waiting for legal sign-off");
    await userEvent.click(within(row).getByRole("button", { name: "Send update" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "action-items/i2/updates")?.body).toEqual({
        word: "blocked",
        note: "Waiting for legal sign-off",
      }),
    );
  });

  it("confirms a drafted status, with each colour's meaning beside it", async () => {
    const r = routes();
    r["POST status-requests/sr1/answer"] = {};
    const api = await open(r);
    const row = within(asked()).getByText(/to confirm the status of CRM rollout/).closest("li")!;
    expect(within(row).getByRole("radio", { name: /Amber.*At risk, recoverable/ })).toBeInTheDocument();
    expect(within(row).getByLabelText("Reason")).toHaveValue("Import is late.");
    await userEvent.click(within(row).getByRole("button", { name: "Confirm" }));
    expect(within(row).getByText("Choose a colour.")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("radio", { name: /Amber/ }));
    await userEvent.click(within(row).getByRole("button", { name: "Confirm with changes" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "status-requests/sr1/answer")?.body).toEqual({
        colour: "amber",
        reason: "Import is late.",
        what_changed: "Two tests failed.",
        what_comes_next: "Fix the import.",
      }),
    );
    expect(within(asked()).getByText("2 requests to answer")).toBeInTheDocument();
  });
});

describe("items and confirmed statements below", () => {
  it("lists their items with Update and their statements with Correct", async () => {
    await open();
    const items = screen.getByRole("region", { name: "Your action items" });
    const row = within(items).getByText("Finish the review i3").closest("li")!;
    expect(within(row).getByRole("button", { name: "Update" })).toBeInTheDocument();
    const statements = screen.getByRole("region", { name: "Your confirmed statements" });
    const line = within(statements).getByText(/Two contracts are still open/).closest("li")!;
    expect(line).toHaveTextContent("Vendor consolidation. Amber. Two contracts are still open.");
    expect(within(line).getByRole("button", { name: "Correct" })).toBeInTheDocument();
  });

  it("a correction shows the earlier version and that both are kept", async () => {
    const r = routes();
    r["POST statements/st1/correct"] = statement({
      id: "st2",
      colour: "red",
      colour_words: "Red",
      wording: { reason: "Both contracts are late.", what_changed: "", what_comes_next: "" },
      mark: "corrected",
      mark_words: "Corrected by its owner",
      previous_version_id: "st1",
      correction: { by: { id: "p-legal", display_name: "Legal lead" }, at: T, points: ["colour", "reason"], of: "earlier_version", drafted_at: null },
      for: undefined,
      project_name: undefined,
    });
    r["GET statements/st1"] = statement();
    const api = await open(r);
    const statements = screen.getByRole("region", { name: "Your confirmed statements" });
    await userEvent.click(within(statements).getByRole("button", { name: "Correct" }));
    expect(within(statements).getByRole("radio", { name: /Amber/ })).toBeChecked();
    await userEvent.click(within(statements).getByRole("radio", { name: /Red/ }));
    const reason = within(statements).getByLabelText("Reason");
    await userEvent.clear(reason);
    await userEvent.type(reason, "Both contracts are late.");
    await userEvent.click(within(statements).getByRole("button", { name: "Save correction" }));

    expect(await within(statements).findByText("Corrected. Both versions are kept.")).toBeInTheDocument();
    expect(within(statements).getByText(/Both contracts are late/)).toBeInTheDocument();
    expect(await within(statements).findByText("Earlier version")).toBeInTheDocument();
    expect(within(statements).getByText(/Two contracts are still open/)).toBeInTheDocument();
    expect(within(statements).getByText("Changed: colour, reason.")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "statements/st1/correct")?.body).toMatchObject({ colour: "red", reason: "Both contracts are late." });
  });

  it("says so when nothing is asked", async () => {
    await open({ "GET my-action-items": { count: 0, items: [] }, "GET status-requests": { count: 0, requests: [] }, "GET my-statements": { count: 0, statements: [] }, "GET colours": COLOURS });
    expect(screen.getByText("Nothing is asked of you.")).toBeInTheDocument();
    expect(await screen.findByText("You have no confirmed statements.")).toBeInTheDocument();
  });
});
