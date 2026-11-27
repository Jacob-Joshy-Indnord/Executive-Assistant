/** Story 6.4: Settings and Organisation settings (task-x9OsVvSZH9Q). */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { OrganisationSettingsPage } from "@/components/assistant/OrganisationSettingsPage";
import { SettingsPage } from "@/components/assistant/SettingsPage";
import type { ApprovalLevelView } from "@/lib/assistant/types";
import { Refuse, fakeApi } from "./fakeApi";
import { assistant } from "./fixtures";

function levels(): ApprovalLevelView[] {
  return [
    {
      kind: "send_message",
      label: "Send a message to a person in the organisation",
      level: "with_approval",
      locked: false,
      limits: { people: null, per_day: 5 },
      allowed_limits: { people: true, per_day: true, per_day_label: "messages a day" },
    },
    {
      kind: "purchase",
      label: "Any purchase, payment, booking or agreement",
      level: "not_allowed",
      locked: true,
      limits: { people: null, per_day: null },
      allowed_limits: { people: false, per_day: false, per_day_label: "" },
    },
  ];
}

const SETTINGS = {
  working_days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
  starts: "09:00",
  ends: "18:00",
  time_zone: "Europe/London",
  follow_ups: null,
  answer_within_working_days: 1,
  late_after_working_days: 0,
  notices_per_owner_per_day: 3,
  report_frequency: "weekly",
  report_day: "monday",
  report_time: "08:00",
  ask_owners_working_days_before: 2,
};

function routes() {
  return {
    "GET assistant": assistant(),
    "GET preferences": {
      form: { working_time: null, tone: { tone: "Warm", sign_off: null }, priorities: null, follow_ups: null, report: null },
      saved: true,
      skipped: false,
      form_offered: false,
      settings: SETTINGS,
    },
    "GET approval-levels": levels(),
    "GET projects": {
      projects: [
        { id: "crm", name: "CRM rollout", owner_id: "p-prog", budget: "1.00", spend: "0.00", followed: true, confirmer: { id: "p-legal", display_name: "Legal lead" } },
        { id: "hiring", name: "Hiring plan", owner_id: "p-fin", budget: "1.00", spend: "0.00", followed: false, confirmer: null },
      ],
    },
  } as Record<string, unknown>;
}

async function open(r = routes()) {
  const api = fakeApi(r);
  render(<SettingsPage client={api.client} basePath="/desk" />);
  await screen.findByRole("heading", { name: "Approval levels" });
  await screen.findByText("Any purchase, payment, booking or agreement");
  return api;
}

const region = (name: string) => screen.getByRole("region", { name });

describe("Settings", () => {
  it("changes preferences and the report", async () => {
    const r = routes();
    r["PUT preferences"] = (r["GET preferences"] as object);
    const api = await open(r);
    const prefs = region("How you work and the report");
    await waitFor(() => expect(within(prefs).getByLabelText("Tone")).toHaveValue("Warm"));
    await userEvent.selectOptions(within(prefs).getByLabelText("How often"), "every_two_weeks");
    await userEvent.selectOptions(within(prefs).getByLabelText("Day"), "friday");
    await userEvent.click(within(prefs).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(within(prefs).getByText("Saved.")).toBeInTheDocument());
    const body = api.calls.find((c) => c.method === "PUT" && c.path === "preferences")?.body as Record<string, Record<string, unknown>>;
    expect(body.tone).toEqual({ tone: "Warm", sign_off: null });
    expect(body.report).toMatchObject({ frequency: "every_two_weeks", day: "friday" });
  });

  it("changes a level's limits", async () => {
    const r = routes();
    r["PUT approval-levels/send_message"] = { ...levels()[0], limits: { people: [{ id: "p-fin", display_name: "Finance lead" }], per_day: 3 } };
    const api = await open(r);
    const row = within(region("Approval levels")).getByRole("form", { name: /Send a message/ });
    const perDay = within(row).getByLabelText("Most messages a day");
    await userEvent.clear(perDay);
    await userEvent.type(perDay, "3");
    await userEvent.type(within(row).getByLabelText("Only for these people"), "Finance lead");
    await userEvent.click(within(row).getByRole("button", { name: "Save level" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({
        level: "with_approval",
        limits: { people: ["Finance lead"], per_day: 3 },
      }),
    );
  });

  it("confirms before a level is set to without approval, and focus returns after", async () => {
    const user = userEvent.setup();
    const r = routes();
    r["PUT approval-levels/send_message"] = { ...levels()[0], level: "without_approval" };
    const api = await open(r);
    const row = within(region("Approval levels")).getByRole("form", { name: /Send a message/ });
    await user.selectOptions(within(row).getByLabelText("Level"), "without_approval");
    const save = within(row).getByRole("button", { name: "Save level" });
    await user.click(save);
    const dialog = screen.getByRole("dialog", { name: "Carry out without approval?" });
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(save).toHaveFocus();
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);

    await user.click(save);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toMatchObject({ level: "without_approval" }));
    expect(await within(row).findByText("Saved.")).toBeInTheDocument();
  });

  it("shows a locked level as one that cannot be changed", async () => {
    await open();
    const levelsRegion = region("Approval levels");
    expect(within(levelsRegion).getByText("This cannot be changed.")).toBeInTheDocument();
    expect(within(levelsRegion).queryByRole("form", { name: /Any purchase/ })).not.toBeInTheDocument();
  });

  it("changes the projects followed", async () => {
    const r = routes();
    r["POST projects/hiring/follow"] = {};
    r["POST projects/crm/unfollow"] = {};
    r["PUT projects/crm/confirmer"] = {};
    r["GET people"] = { people: [{ id: "p-fin", display_name: "Finance lead", kind: "person" }] };
    const api = await open(r);
    const projects = region("Projects followed");
    await waitFor(() => expect(within(projects).getByText("Hiring plan")).toBeInTheDocument());
    expect(within(projects).getByText(/Legal lead confirms its status/)).toBeInTheDocument();

    const hiring = within(projects).getByText("Hiring plan").closest("li")!;
    await userEvent.click(within(hiring).getByRole("button", { name: "Follow" }));
    await userEvent.click(within(hiring).getByRole("button", { name: "Follow" }));
    await waitFor(() => expect(api.calls.find((c) => c.path === "projects/hiring/follow")?.body).toEqual({}));

    const crm = within(projects).getByText("CRM rollout").closest("li")!;
    await userEvent.click(within(crm).getByRole("button", { name: "Change who confirms" }));
    await userEvent.type(within(crm).getByLabelText("Who confirms its status"), "Finance lead");
    await userEvent.click(within(crm).getByRole("button", { name: "Change" }));
    await waitFor(() => expect(api.calls.find((c) => c.path === "projects/crm/confirmer")?.body).toEqual({ person_id: "p-fin" }));

    await userEvent.click(within(crm).getByRole("button", { name: "Stop following" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "projects/crm/unfollow")).toBe(true));
  });

  it("pauses and resumes", async () => {
    const r = routes();
    r["POST assistant/pause"] = assistant({ state: "paused", running: false });
    r["POST assistant/resume"] = assistant();
    await open(r);
    const pause = region("Pause");
    await userEvent.click(within(pause).getByRole("button", { name: "Pause" }));
    expect(await within(pause).findByText(/Your assistant is paused/)).toBeInTheDocument();
    await userEvent.click(within(pause).getByRole("button", { name: "Resume" }));
    expect(await within(pause).findByText("Your assistant is working.")).toBeInTheDocument();
  });

  it("shows a failure as its sentence", async () => {
    const r = routes();
    r["PUT approval-levels/send_message"] = new Refuse(422, "No one in the organisation is called Nobody.");
    await open(r);
    const row = within(region("Approval levels")).getByRole("form", { name: /Send a message/ });
    await userEvent.type(within(row).getByLabelText("Only for these people"), "Nobody");
    await userEvent.click(within(row).getByRole("button", { name: "Save level" }));
    expect(await within(row).findByText("No one in the organisation is called Nobody.")).toBeInTheDocument();
  });

  it("links back following the base path", async () => {
    await open();
    expect(screen.getByRole("link", { name: "Back to your assistant" })).toHaveAttribute("href", "/desk");
  });
});

describe("Organisation settings", () => {
  it("lets an administrator write the meaning of each colour", async () => {
    const api = fakeApi({
      "GET organisation/settings": { organisation_id: "o1", colour_meanings: { green: "On plan", amber: null, red: null } },
      "PUT organisation/settings": { organisation_id: "o1", colour_meanings: { green: "On plan", amber: "At risk", red: "Needs a decision" } },
    });
    render(<OrganisationSettingsPage client={api.client} basePath="/assistant" />);
    const green = await screen.findByLabelText(/Green means/);
    expect(green).toHaveValue("On plan");
    await userEvent.type(screen.getByLabelText(/Amber means/), "At risk");
    await userEvent.type(screen.getByLabelText(/Red means/), "Needs a decision");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({
        colour_meanings: { green: "On plan", amber: "At risk", red: "Needs a decision" },
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("tells anyone else it is for administrators", async () => {
    const api = fakeApi({});
    render(<OrganisationSettingsPage client={api.client} basePath="/assistant" />);
    expect(await screen.findByText("Only administrators can change the organisation settings.")).toBeInTheDocument();
  });
});
