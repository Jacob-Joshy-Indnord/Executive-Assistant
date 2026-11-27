/** Story 6.2: the Assistant page (task-_OgsYqPdzLk). */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AssistantPage, NO_ASSISTANT } from "@/components/assistant/AssistantPage";
import { SECTIONS } from "@/components/assistant/sections";
import { createSectionRegistry } from "@/components/assistant/sections/registry";
import { Refuse, deferred, fakeApi } from "./fakeApi";
import { assistant, managerRoutes } from "./fixtures";

async function open(routes = managerRoutes(), basePath = "/assistant", waitForDesk = true) {
  const api = fakeApi(routes);
  render(<AssistantPage client={api.client} basePath={basePath} />);
  if (waitForDesk) await screen.findByRole("heading", { level: 2, name: "Needs your approval" });
  return api;
}

function part(name: string) {
  return screen.getByRole("region", { name });
}

function countOf(name: string) {
  return within(part(name)).getByTestId("section-count");
}

describe("the parts of section 13.1", () => {
  it("are shown in order, each with a one-line summary and a count", async () => {
    await open();
    await screen.findByText("2 requests waiting for you");
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([
      "Needs your approval",
      "Needs your attention",
      "Project status",
      "Action items",
      "Done for you",
      "Message your assistant",
    ]);
    expect(countOf("Needs your approval")).toHaveTextContent("2 waiting");
    expect(countOf("Needs your attention")).toHaveTextContent("1 to decide");
    await within(part("Project status")).findByText("1 confirmed");
    expect(countOf("Action items")).toHaveTextContent("2 open");
    expect(within(part("Action items")).getByText(/2 open, \d due this week/)).toBeInTheDocument();
  });

  it("shows one short line for a part with nothing in it", async () => {
    const routes = managerRoutes();
    routes["GET requests"] = { count: 0, requests: [] };
    await open(routes);
    expect(await screen.findByText("Nothing waits for your approval.")).toBeInTheDocument();
    expect(countOf("Needs your approval")).toHaveTextContent("0");
    expect(await within(part("Done for you")).findByText("Nothing done for you yet.")).toBeInTheDocument();
    expect(within(part("Needs your approval")).queryByRole("list")).not.toBeInTheDocument();
  });

  it("are taken from the registry, so later parts can be added", () => {
    const registry = createSectionRegistry();
    registry.register({ key: "later", title: "Later", order: 35, Component: () => null });
    registry.register({ key: "first", title: "First", order: 5, Component: () => null });
    expect(registry.all().map((s) => s.key)).toEqual(["first", "later"]);
    expect(() => registry.register({ key: "first", title: "Again", order: 1, Component: () => null })).toThrow();
    expect(SECTIONS.all().map((s) => s.key)).toEqual(["approval", "attention", "status", "items", "done", "chat"]);
  });
});

describe("in-place actions", () => {
  it("approve: the row leaves and the count changes at once", async () => {
    const routes = managerRoutes();
    const answer = deferred();
    routes["POST requests/r1/approve"] = () => answer.promise;
    const api = await open(routes);
    const row = (await screen.findByText(/contract review r1/)).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Approve" }));
    expect(countOf("Needs your approval")).toHaveTextContent("1 waiting");
    expect(screen.queryByText(/contract review r1/)).not.toBeInTheDocument();
    answer.resolve({});
    await waitFor(() => expect(api.calls.some((c) => c.path === "requests/r1/approve")).toBe(true));
  });

  it("if it fails, the row returns with the reason", async () => {
    const routes = managerRoutes();
    routes["POST requests/r1/approve"] = new Refuse(409, "This has already changed.");
    await open(routes);
    const row = (await screen.findByText(/contract review r1/)).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("This has already changed.")).toBeInTheDocument();
    expect(screen.getByText(/contract review r1/)).toBeInTheDocument();
    expect(countOf("Needs your approval")).toHaveTextContent("2 waiting");
  });

  it("edit: the edited text is approved", async () => {
    const routes = managerRoutes();
    routes["POST requests/r1/approve"] = {};
    const api = await open(routes);
    const row = (await screen.findByText(/contract review r1/)).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const text = within(row).getByLabelText("Text");
    await userEvent.clear(text);
    await userEvent.type(text, "Please send the review today.");
    await userEvent.click(within(row).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "requests/r1/approve")?.body).toEqual({ text: "Please send the review today." }),
    );
    expect(countOf("Needs your approval")).toHaveTextContent("1 waiting");
  });

  it("decline: with an optional reason, in place", async () => {
    const routes = managerRoutes();
    routes["POST requests/r2/decline"] = {};
    const api = await open(routes);
    const row = (await screen.findByText(/contract review r2/)).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Decline" }));
    await userEvent.type(within(row).getByLabelText("Reason (optional)"), "Not now");
    await userEvent.click(within(row).getByRole("button", { name: "Decline" }));
    expect(countOf("Needs your approval")).toHaveTextContent("1 waiting");
    await waitFor(() => expect(api.calls.find((c) => c.path === "requests/r2/decline")?.body).toEqual({ reason: "Not now" }));
  });

  it("approve all: only requests to owners", async () => {
    const routes = managerRoutes();
    routes["GET requests"] = {
      count: 3,
      requests: [
        ...(managerRoutes()["GET requests"] as { requests: unknown[] }).requests,
        { ...(managerRoutes()["GET requests"] as { requests: Record<string, unknown>[] }).requests[0], id: "r3", effect: "Ask the Finance lead r3" },
      ],
    };
    routes["POST requests/approve-all"] = {};
    await open(routes);
    await userEvent.click(await screen.findByRole("button", { name: "Approve all" }));
    expect(countOf("Needs your approval")).toHaveTextContent("1 waiting");
    expect(screen.getByText(/contract review r2/)).toBeInTheDocument();
  });

  it("decide: the entry leaves at once; if it fails it returns with the reason", async () => {
    const routes = managerRoutes();
    routes["POST attention/e1/decide"] = new Refuse(422, "Give a reason.");
    const api = await open(routes);
    const attention = part("Needs your attention");
    await within(attention).findByText("Legal review of the vendor contracts");
    await userEvent.click(within(attention).getByRole("button", { name: "Close" }));
    await userEvent.type(within(attention).getByLabelText("Reason (optional)"), "No longer needed");
    await userEvent.click(within(attention).getByRole("button", { name: "Close" }));
    expect(await within(attention).findByText("Give a reason.")).toBeInTheDocument();
    expect(countOf("Needs your attention")).toHaveTextContent("1 to decide");
    expect(api.calls.find((c) => c.path === "attention/e1/decide")?.body).toEqual({ choice: "Close", reason: "No longer needed" });

    routes["POST attention/e1/decide"] = {};
    await userEvent.click(within(attention).getByRole("button", { name: "Close" }));
    await userEvent.click(within(attention).getByRole("button", { name: "Close" }));
    expect(countOf("Needs your attention")).toHaveTextContent("0");
  });

  it("decide Extend asks for the new date", async () => {
    const routes = managerRoutes();
    routes["POST attention/e1/decide"] = {};
    const api = await open(routes);
    const attention = part("Needs your attention");
    await userEvent.click(await within(attention).findByRole("button", { name: "Extend" }));
    await userEvent.type(within(attention).getByLabelText("New due date"), "2030-01-15");
    await userEvent.click(within(attention).getByRole("button", { name: "Extend" }));
    await waitFor(() => expect(api.calls.find((c) => c.path === "attention/e1/decide")?.body).toEqual({ choice: "Extend", due_date: "2030-01-15" }));
  });

  it("decide Take it up names the person and sends the prepared message", async () => {
    const routes = managerRoutes();
    routes["POST attention/e1/decide"] = {};
    routes["GET people"] = { people: [{ id: "p-legal", display_name: "Legal lead", kind: "person" }] };
    const api = await open(routes);
    const attention = part("Needs your attention");
    await userEvent.click(await within(attention).findByRole("button", { name: "Take it up" }));
    expect(within(attention).getByLabelText("To")).toHaveValue("Legal lead");
    expect(within(attention).getByLabelText("Message")).toHaveValue("The legal review is late. Can we talk today?");
    await userEvent.click(within(attention).getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "attention/e1/decide")?.body).toEqual({
        choice: "Take it up",
        person_id: "p-legal",
        message: "The legal review is late. Can we talk today?",
      }),
    );
  });
});

describe("opening a part in place", () => {
  it("shows its content and actions, and closing returns focus to where it was", async () => {
    const user = userEvent.setup();
    await open();
    const items = part("Action items");
    const opener = await within(items).findByRole("button", { name: "Open Action items" });
    expect(opener).toHaveAttribute("aria-expanded", "false");
    await user.click(opener);
    expect(within(items).getByText("Finish the review i1")).toBeInTheDocument();
    expect(within(items).getByRole("button", { name: "Add an action item" })).toBeInTheDocument();

    const row = within(items).getByRole("button", { name: /Finish the review i1/ });
    await user.click(row);
    expect(within(items).getByRole("button", { name: "Extend" })).toBeInTheDocument();
    await user.click(row);
    await waitFor(() => expect(row).toHaveFocus());

    const close = within(items).getByRole("button", { name: "Close Action items" });
    await user.click(close);
    expect(within(items).queryByText("Finish the review i1")).not.toBeInTheDocument();
    await waitFor(() => expect(within(items).getByRole("button", { name: "Open Action items" })).toHaveFocus());
  });

  it("returns focus to Add an action item after the form is cancelled", async () => {
    const user = userEvent.setup();
    await open();
    const items = part("Action items");
    await user.click(await within(items).findByRole("button", { name: "Open Action items" }));
    await user.click(within(items).getByRole("button", { name: "Add an action item" }));
    expect(within(items).getByLabelText("What is to be done")).toHaveFocus();
    await user.click(within(items).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(within(items).getByRole("button", { name: "Add an action item" })).toHaveFocus());
  });

  it("shows a status line with its colour in words", async () => {
    await open();
    const statusPart = part("Project status");
    await userEvent.click(await within(statusPart).findByRole("button", { name: "Open Project status" }));
    const line = within(statusPart).getByRole("button", { name: /Vendor consolidation/ });
    expect(line).toHaveTextContent("Vendor consolidation. Amber. Legal review is late.");
    expect(within(statusPart).getByText(/Corrected by its owner/)).toBeInTheDocument();
  });
});

describe("a piece of work", () => {
  it("shows its state, its reply and anything refused", async () => {
    await open();
    const chat = part("Message your assistant");
    await userEvent.click(await within(chat).findByRole("button", { name: /Remind the Legal lead/ }));
    expect(await within(chat).findByText("I have proposed the action item for the Legal lead.")).toBeInTheDocument();
    expect(within(chat).getAllByText("Done").length).toBeGreaterThan(0);
    expect(within(chat).getByText("Refused (1)")).toBeInTheDocument();
    expect(within(chat).getByText("No one in the organisation is called Legal leed.")).toBeInTheDocument();
  });

  it("is asked for from the chat", async () => {
    const routes = managerRoutes();
    routes["POST chat"] = {};
    const api = await open(routes);
    const chat = part("Message your assistant");
    await userEvent.type(await within(chat).findByLabelText("Message your assistant"), "Draft a note to Finance");
    await userEvent.click(within(chat).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "POST" && c.path === "chat")?.body).toEqual({ text: "Draft a note to Finance" }));
  });
});

describe("paused, missing and new assistants", () => {
  it("says it is paused, with Resume", async () => {
    const routes = managerRoutes();
    routes["POST assistant/opened"] = assistant({ state: "paused", running: false });
    routes["POST assistant/resume"] = assistant();
    const api = await open(routes);
    expect(await screen.findByText("Your assistant is paused.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(screen.queryByText("Your assistant is paused.")).not.toBeInTheDocument());
    expect(api.calls.some((c) => c.path === "assistant/resume")).toBe(true);
  });

  it("says there is no assistant", async () => {
    await open({}, "/assistant", false);
    expect(await screen.findByText(NO_ASSISTANT)).toBeInTheDocument();
    expect(NO_ASSISTANT).toBe("You have no Executive Assistant yet.");
  });

  it("offers the first-time form while new, sending only what was answered", async () => {
    const routes = managerRoutes();
    let state = assistant({ state: "new", form_offered: true });
    routes["POST assistant/opened"] = () => state;
    routes["GET assistant"] = () => state;
    routes["GET preferences"] = { settings: { starts: "09:00", ends: "18:00", time_zone: "Europe/London", working_days: [] } };
    routes["PUT preferences"] = () => {
      state = assistant();
      return {};
    };
    const api = await open(routes, "/assistant", false);
    expect(await screen.findByRole("heading", { name: "How you work" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Needs your approval" })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Sign-off"), "Best wishes");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({
      working_time: null,
      tone: { tone: null, sign_off: "Best wishes" },
      priorities: null,
      follow_ups: null,
      report: null,
    }));
    expect(await screen.findByRole("heading", { name: "Needs your approval" })).toBeInTheDocument();
  });

  it("keeps the browser's time zone on first open", async () => {
    const api = await open();
    await screen.findByText("2 requests waiting for you");
    const opened = api.calls.find((c) => c.path === "assistant/opened");
    expect(opened?.body).toEqual({ time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone });
  });
});

describe("links", () => {
  it("follow the base path that is passed in", async () => {
    await open(managerRoutes(), "/flow/desks/ea");
    expect(await screen.findByRole("link", { name: "Settings" })).toHaveAttribute("href", "/flow/desks/ea/settings");
  });
});
